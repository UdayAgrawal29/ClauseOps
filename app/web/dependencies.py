"""Shared FastAPI dependencies for the web layer.

The headline dependency here is :func:`get_current_user`, which every protected
data endpoint depends on. It resolves the authenticated ``user_id`` from the
access token in the ``Authorization: Bearer`` header and rejects any request
that presents no valid access token with HTTP 401 (Requirement 1.7).
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import TokenError, decode_access_token
from app.config import Settings, get_settings
from app.data.database import get_session
from app.data.models import User

# ``auto_error=False`` lets us return a consistent 401 (with a WWW-Authenticate
# header) ourselves instead of FastAPI's default 403 when the header is absent.
_bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_settings_dep() -> Settings:
    """Expose cached settings as a dependency (overridable in tests)."""

    return get_settings()


async def get_current_user_id(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(_bearer_scheme)
    ],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> int:
    """Resolve the authenticated user's id from a valid access token.

    Raises 401 when the bearer token is missing, malformed, expired,
    mis-signed, or not an access token.
    """

    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED
    try:
        payload = decode_access_token(credentials.credentials, settings=settings)
    except TokenError as exc:
        raise _UNAUTHENTICATED from exc
    return payload.user_id


async def get_current_user(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Load and return the authenticated :class:`User`.

    The token may be cryptographically valid yet reference a user that no
    longer exists (e.g. deleted account); that is treated as unauthenticated.
    """

    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise _UNAUTHENTICATED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


__all__ = [
    "get_current_user",
    "get_current_user_id",
    "get_settings_dep",
    "CurrentUser",
    "DbSession",
]
