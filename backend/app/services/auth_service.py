"""
Authentication service.

Holds the business logic for user registration and login, sitting between the
API routes and the `UserRepository`. Routes stay thin (HTTP concerns only);
this service owns the rules — uniqueness checks, password hashing, credential
verification, and token issuance.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.exceptions import AuthenticationError, ConflictError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.db.models import User
from app.db.repositories.user_repository import UserRepository

logger = get_logger(__name__)


class AuthService:
    """User registration, login, and token issuance."""

    def __init__(self, user_repo: UserRepository) -> None:
        """Inject the user repository (Dependency Inversion)."""
        self._users = user_repo

    async def register(
        self, *, username: str, email: str, password: str
    ) -> User:
        """Register a new user.

        Args:
            username: Desired unique username.
            email: Desired unique email.
            password: Plaintext password — hashed before storage, never kept.

        Returns:
            The newly created `User`.

        Raises:
            ConflictError: If the username or email is already taken.
        """
        if await self._users.exists(username=username, email=email):
            raise ConflictError(
                "A user with that username or email already exists."
            )

        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(password),
        )
        await self._users.add(user)
        logger.info("Registered new user: %s", username)
        return user

    async def authenticate(
        self, *, username: str, password: str
    ) -> tuple[User, str]:
        """Verify credentials and issue an access token.

        Args:
            username: The username presented at login.
            password: The plaintext password presented at login.

        Returns:
            A tuple of the authenticated `User` and a signed JWT access token.

        Raises:
            AuthenticationError: If credentials are invalid or the account is
                inactive. The same error is used for "no such user" and "wrong
                password" so the API cannot be used to enumerate accounts.
        """
        user = await self._users.get_by_username(username)
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Incorrect username or password.")
        if not user.is_active:
            raise AuthenticationError("This account is disabled.")

        # Record the login time (best-effort; part of the request transaction).
        user.last_login_at = datetime.now(timezone.utc)

        token = create_access_token(subject=user.id, username=user.username)
        logger.info("User authenticated: %s", username)
        return user, token

    async def get_user(self, user_id: str) -> User:
        """Fetch a user by id, raising if absent.

        Used by the current-user dependency after a token is decoded.

        Raises:
            AuthenticationError: If the user no longer exists.
        """
        user = await self._users.get(user_id)
        if user is None:
            raise AuthenticationError("User account no longer exists.")
        return user
