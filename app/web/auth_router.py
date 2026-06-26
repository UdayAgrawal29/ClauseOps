"""Auth Service HTTP endpoints (design "Component 1: Auth Service").

Routes (mounted at the app root):

* ``POST /auth/register`` -- create a user with a unique, non-empty email,
  storing only the Argon2 hash; duplicate emails are rejected and no user is
  created. (Requirements 1.1, 1.2)
* ``POST /auth/login`` -- verify credentials and return an access + refresh
  token pair; the refresh token is also set in an httpOnly cookie. Bad
  credentials are rejected with no tokens. (Requirements 1.3, 1.4)
* ``POST /auth/refresh`` -- verify the refresh token from the cookie, rotate it
  (new access + refresh), and invalidate the rotated-out token. (Requirement 1.5)
* ``POST /auth/logout`` -- invalidate the current refresh token. (Requirement 1.6)
* ``GET /me`` -- return the current user, gated by the access-token dependency
  (Requirement 1.7).
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.security import (
    TokenError,
    decode_refresh_token,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    rotate_refresh_token,
    verify_password,
)
from app.config import Settings
from app.data.models import User
from app.web.dependencies import (
    CurrentUser,
    DbSession,
    get_settings_dep,
)
from app.web.revocation import RefreshTokenStore, get_refresh_token_store
from app.web.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

# Name of the httpOnly cookie carrying the rotating refresh token.
REFRESH_COOKIE_NAME = "clauseops_refresh"

SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
RefreshStoreDep = Annotated[RefreshTokenStore, Depends(get_refresh_token_store)]

router = APIRouter(tags=["auth"])

_BAD_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password",
    headers={"WWW-Authenticate": "Bearer"},
)
_INVALID_REFRESH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired refresh token",
    headers={"WWW-Authenticate": "Bearer"},
)


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    """Write the refresh token to an httpOnly cookie scoped to the auth routes."""

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        samesite="lax",
        # ``Secure`` is config-driven: keep it off for plain-HTTP localhost in
        # the offline MVP and enable it (CLAUSEOPS_COOKIE_SECURE=1) behind TLS.
        secure=settings.cookie_secure,
        path="/auth",
    )


@router.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    session: DbSession,
) -> User:
    """Create a new user, storing only the Argon2 password hash.

    A registration that reuses an existing email is rejected with 409 and
    creates no user record (Requirement 1.2).
    """

    existing = await session.scalar(
        select(User).where(User.email == payload.email)
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # Lost the check-then-insert race against a concurrent registration of
        # the same email (unique constraint). Treat as a duplicate.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )
    await session.refresh(user)
    return user


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
) -> TokenResponse:
    """Verify credentials and issue access + refresh tokens.

    On any credential failure no tokens are issued (Requirement 1.4). The
    response is identical for an unknown email and a bad password so the
    endpoint does not leak which emails are registered.
    """

    user = await session.scalar(
        select(User).where(User.email == payload.email)
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise _BAD_CREDENTIALS

    access = issue_access_token(user.id, settings=settings)
    refresh = issue_refresh_token(user.id, settings=settings)
    _set_refresh_cookie(response, refresh.token, settings)
    return TokenResponse(access_token=access.token, refresh_token=refresh.token)


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh_tokens(
    response: Response,
    settings: SettingsDep,
    store: RefreshStoreDep,
    refresh_cookie: Annotated[Optional[str], Cookie(alias=REFRESH_COOKIE_NAME)] = None,
) -> TokenResponse:
    """Rotate the refresh token: issue a new pair and invalidate the old token.

    The presented refresh token (from the httpOnly cookie) must verify and must
    not already be revoked. After rotation the previous token's ``jti`` is
    revoked so it can never be reused (Requirement 1.5).
    """

    if not refresh_cookie:
        raise _INVALID_REFRESH

    # Verify first so we can reject a revoked/expired/forged token uniformly.
    try:
        payload = decode_refresh_token(refresh_cookie, settings=settings)
    except TokenError as exc:
        raise _INVALID_REFRESH from exc

    # Atomically consume the presented token's jti: the FIRST use marks it
    # revoked and returns True; any concurrent or later reuse returns False.
    # This closes the check-then-revoke race where two requests could both
    # rotate the same refresh token.
    if not await store.consume(payload.jti):
        raise _INVALID_REFRESH

    rotated = rotate_refresh_token(refresh_cookie, settings=settings)
    _set_refresh_cookie(response, rotated.refresh.token, settings)
    return TokenResponse(
        access_token=rotated.access.token,
        refresh_token=rotated.refresh.token,
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    settings: SettingsDep,
    store: RefreshStoreDep,
    refresh_cookie: Annotated[Optional[str], Cookie(alias=REFRESH_COOKIE_NAME)] = None,
) -> Response:
    """Invalidate the current refresh token and clear its cookie.

    Logout is idempotent: a missing or already-invalid cookie still results in
    a cleared cookie and a 204 (Requirement 1.6).
    """

    if refresh_cookie:
        try:
            payload = decode_refresh_token(refresh_cookie, settings=settings)
        except TokenError:
            payload = None
        if payload is not None:
            await store.revoke(payload.jti)

    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/auth")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/auth/ws-ticket", response_model=TokenResponse)
async def ws_ticket(
    current_user: CurrentUser,
    settings: SettingsDep,
) -> TokenResponse:
    """Mint a very short-lived access token for a WebSocket handshake.

    Browsers cannot set ``Authorization`` headers on a WS upgrade, so the token
    must travel in the URL query string. Returning a ~60s single-purpose token
    here (instead of the full-lifetime access token) bounds the exposure window
    if the URL leaks via logs/history. The WS endpoint validates it with the
    same ``decode_access_token`` path.
    """

    ticket = issue_access_token(current_user.id, settings=settings, ttl_seconds=60)
    return TokenResponse(access_token=ticket.token, refresh_token="")


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUser) -> User:
    """Return the authenticated user (Requirement 1.7 enforced by the dependency)."""

    return current_user


__all__ = ["router", "REFRESH_COOKIE_NAME"]
