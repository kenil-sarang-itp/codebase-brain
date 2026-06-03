"""
Authentication routes — register, login, logout, current user.

These endpoints implement the spec's "proper login/logout routes". The system
uses stateless JWT bearer tokens:

    * `POST /auth/register` — create an account.
    * `POST /auth/login`    — exchange credentials for an access token.
    * `POST /auth/logout`   — client-side token disposal (see handler note).
    * `GET  /auth/me`       — return the current authenticated user.

Routes stay thin: validation is handled by Pydantic schemas, business rules by
`AuthService`, and error-to-HTTP mapping by the global exception handlers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db_session
from app.schemas.api_schemas import (
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.service_factory import build_auth_service

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Register a new account and return an access token.

    Registering also logs the user straight in (returns a token) so the client
    needs only one round trip to get started.
    """
    auth_service = build_auth_service(session)
    user = await auth_service.register(
        username=payload.username,
        email=payload.email,
        password=payload.password,
    )
    # Issue a token immediately so registration doubles as login.
    _, token = await auth_service.authenticate(
        username=payload.username, password=payload.password
    )
    return TokenResponse(
        access_token=token, username=user.username, user_id=user.id
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Authenticate with username + password and receive a JWT access token."""
    auth_service = build_auth_service(session)
    user, token = await auth_service.authenticate(
        username=payload.username, password=payload.password
    )
    return TokenResponse(
        access_token=token, username=user.username, user_id=user.id
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    _current_user: User = Depends(get_current_user),
) -> MessageResponse:
    """Log out the current user.

    The system uses stateless JWTs, so "logout" means the client discards its
    token. This endpoint exists so the client has a definite action to call
    (and so the flow is symmetric with login); it requires a valid token,
    confirming the caller was genuinely authenticated. A production deployment
    that needs server-side revocation would add a token denylist here.
    """
    return MessageResponse(message="Logged out. Please discard your token.")


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Return the currently authenticated user's public profile."""
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
    )
