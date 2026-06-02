"""
Security primitives: password hashing and JWT token handling.

This module is intentionally small and pure — it has *no* knowledge of FastAPI,
databases, or request objects. That keeps it unit-testable in isolation and
reusable from both the API layer and any CLI/worker code.

    * Passwords: hashed with bcrypt (used directly, not via passlib — passlib
      1.7.x is unmaintained and incompatible with bcrypt 5.x). Bcrypt salts
      automatically and is deliberately slow, the correct property for password
      hashing.
    * Tokens: stateless JWTs signed with HS256. The token payload carries the
      user id (`sub`), username, and an expiry (`exp`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config.settings import get_settings
from app.core.exceptions import AuthenticationError

# Bcrypt operates on bytes and has a hard 72-byte limit on the input password;
# anything longer must be truncated before hashing (and identically before
# verifying, so the two paths stay consistent).
_BCRYPT_MAX_BYTES = 72


# --------------------------------------------------------------------------- #
# Password hashing                                                            #
# --------------------------------------------------------------------------- #
def _to_bcrypt_bytes(plain_password: str) -> bytes:
    """Encode a password to UTF-8 bytes, truncated to bcrypt's 72-byte limit.

    Truncation is applied to the *byte* string, not the character string, so a
    multi-byte UTF-8 character is never cut in half.
    """
    return plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain_password: str) -> str:
    """Return a salted bcrypt hash of `plain_password`.

    The salt is generated and embedded by bcrypt automatically, so the returned
    string is fully self-contained. The password is truncated to 72 bytes first
    because that is bcrypt's hard limit.
    """
    hashed = bcrypt.hashpw(_to_bcrypt_bytes(plain_password), bcrypt.gensalt())
    # Store as str — the hash is pure ASCII.
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True iff `plain_password` matches the stored bcrypt hash.

    Never raises on a bad hash format — returns False instead, so a corrupt
    stored hash degrades to "login fails" rather than a 500 error.
    """
    try:
        return bcrypt.checkpw(
            _to_bcrypt_bytes(plain_password),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# JWT access tokens                                                           #
# --------------------------------------------------------------------------- #
def create_access_token(
    subject: str,
    username: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT access token.

    Args:
        subject: The user id — stored in the standard `sub` claim.
        username: Convenience claim so the API need not hit the DB to greet
            the user.
        extra_claims: Optional additional claims to embed.

    Returns:
        The encoded, signed JWT string.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify and decode a JWT access token.

    Args:
        token: The raw JWT string (without the "Bearer " prefix).

    Returns:
        The decoded claims dict.

    Raises:
        AuthenticationError: If the token is expired, malformed, or has an
            invalid signature. A single exception type means the API layer
            handles all token failures uniformly.
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Access token is invalid.") from exc

    if claims.get("type") != "access":
        raise AuthenticationError("Wrong token type supplied.")
    if "sub" not in claims:
        raise AuthenticationError("Token is missing the subject claim.")

    return claims
