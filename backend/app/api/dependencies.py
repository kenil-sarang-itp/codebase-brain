"""
FastAPI dependencies.

Reusable dependency callables injected into route handlers via `Depends(...)`.
The central one is `get_current_user`, which enforces JWT authentication:

    1. Extract the bearer token from the `Authorization` header.
    2. Decode and verify it (`decode_access_token`).
    3. Load the corresponding user and confirm the account is active.

A route that depends on `get_current_user` is automatically protected — an
absent or invalid token yields a 401 before the handler body runs.
"""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db_session
from app.services.service_factory import build_auth_service

# `auto_error=False` so we can raise our *own* typed AuthenticationError
# (handled uniformly by the exception handlers) instead of FastAPI's default.
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve and return the authenticated user, or raise 401.

    Injected into every protected route. The decoded token's `sub` claim is the
    user id; the user is then loaded to confirm the account still exists and is
    active (a token alone is not enough — the account could have been disabled).
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token.")

    claims = decode_access_token(credentials.credentials)
    user_id = claims["sub"]

    auth_service = build_auth_service(session)
    user = await auth_service.get_user(user_id)
    if not user.is_active:
        raise AuthenticationError("This account is disabled.")

    # Stash the user id on request state so logging/tracing can use it.
    request.state.user_id = user.id
    return user


def get_db():
    """Re-export of the DB session dependency for a single import point."""
    return get_db_session()
