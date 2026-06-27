"""Unit tests for the Auth Service HTTP flows (task 4.3).

Exercises the auth endpoints end-to-end through a FastAPI ``TestClient`` against
an isolated, file-backed SQLite database (so the async ORM runs without a live
PostgreSQL), with the refresh-token revocation store reset per test for
isolation.

Coverage:
* register success creates a user storing only the hash; duplicate-email
  registration is rejected (409) and creates no second user. (Req 1.1, 1.2)
* login returns access + refresh tokens and sets the httpOnly refresh cookie;
  unknown email / wrong password are rejected (401) with no tokens. (Req 1.4)
* refresh rotation issues a new pair and invalidates the old refresh token, so
  a replay of the old token is rejected (401). (Req 1.5)
* logout invalidates the refresh token (subsequent refresh -> 401). (Req 1.6)
* the protected ``GET /me`` endpoint is rejected without a valid access token
  and succeeds with one. (Req 1.7)
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.data.database import Base, get_session
from app.data.models import User
from app.web.dependencies import get_settings_dep
from app.web.main import create_app
from app.web.revocation import (
    InMemoryRefreshTokenStore,
    get_refresh_token_store,
)
from app.web.auth_router import REFRESH_COOKIE_NAME
from app.auth.security import verify_password

# Test settings with fixed, known secrets so token issue/verify are consistent
# across the login/refresh endpoints and the current-user dependency.
TEST_SETTINGS = Settings(
    jwt_secret="test-access-secret",
    jwt_refresh_secret="test-refresh-secret",
    jwt_algorithm="HS256",
    access_token_ttl_seconds=900,
    refresh_token_ttl_seconds=60 * 60 * 24 * 14,
)


@pytest.fixture()
def db_url() -> Iterator[str]:
    """A throwaway file-backed SQLite database, unique per test."""

    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    # NullPool + fresh connections per checkout keeps the async engine usable
    # across the distinct event loops used by setup and the TestClient portal.
    url = f"sqlite+aiosqlite:///{path}"

    async def _create() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create())
    try:
        yield url
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@pytest.fixture()
def sessionmaker(db_url: str) -> async_sessionmaker[AsyncSession]:
    """Async session factory bound to the per-test database."""

    engine = create_async_engine(db_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture()
def store() -> InMemoryRefreshTokenStore:
    """A fresh, isolated refresh-token revocation store per test."""

    return InMemoryRefreshTokenStore()


@pytest.fixture()
def client(
    sessionmaker: async_sessionmaker[AsyncSession],
    store: InMemoryRefreshTokenStore,
) -> Iterator[TestClient]:
    """A TestClient with DB session, settings, and token store overridden."""

    app = create_app()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings_dep] = lambda: TEST_SETTINGS
    app.dependency_overrides[get_refresh_token_store] = lambda: store

    with TestClient(app) as test_client:
        yield test_client


def _query(sessionmaker: async_sessionmaker[AsyncSession], coro_fn):
    """Run a one-off async DB query against the test database."""

    async def _run():
        async with sessionmaker() as session:
            return await coro_fn(session)

    return asyncio.run(_run())


def _register(client: TestClient, email: str, password: str):
    return client.post(
        "/auth/register", json={"email": email, "password": password}
    )


def _login(client: TestClient, email: str, password: str):
    return client.post(
        "/auth/login", json={"email": email, "password": password}
    )


# ---------------------------------------------------------------------------
# Registration (Requirements 1.1, 1.2)
# ---------------------------------------------------------------------------


def test_register_success_creates_user_storing_only_hash(
    client: TestClient, sessionmaker
) -> None:
    resp = _register(client, "alice@example.com", "s3cret-pass")

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert "id" in body
    # The response never leaks any password material.
    assert "password" not in body
    assert "password_hash" not in body

    async def _check(session: AsyncSession):
        users = (await session.scalars(select(User))).all()
        return users

    users = _query(sessionmaker, _check)
    assert len(users) == 1
    stored = users[0]
    # Only the Argon2 hash is stored -- never the raw password.
    assert stored.password_hash != "s3cret-pass"
    assert stored.password_hash.startswith("$argon2")
    assert verify_password("s3cret-pass", stored.password_hash)


def test_duplicate_email_registration_rejected_no_second_user(
    client: TestClient, sessionmaker
) -> None:
    first = _register(client, "dup@example.com", "pw-one-123")
    assert first.status_code == 201

    second = _register(client, "dup@example.com", "pw-two-456")
    assert second.status_code == 409

    async def _count(session: AsyncSession):
        return await session.scalar(select(func.count()).select_from(User))

    assert _query(sessionmaker, _count) == 1


# ---------------------------------------------------------------------------
# Login (Requirements 1.3, 1.4)
# ---------------------------------------------------------------------------


def test_login_success_returns_tokens_and_sets_httponly_cookie(
    client: TestClient,
) -> None:
    _register(client, "bob@example.com", "bobs-password-1")

    resp = _login(client, "bob@example.com", "bobs-password-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"

    set_cookie = resp.headers.get("set-cookie", "")
    assert REFRESH_COOKIE_NAME in set_cookie
    assert "httponly" in set_cookie.lower()
    # The refresh cookie is also recorded in the client jar.
    assert client.cookies.get(REFRESH_COOKIE_NAME) == body["refresh_token"]


def test_login_unknown_email_rejected_no_tokens(client: TestClient) -> None:
    resp = _login(client, "nobody@example.com", "whatever-pass")

    assert resp.status_code == 401
    body = resp.json()
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None


def test_login_wrong_password_rejected_no_tokens(client: TestClient) -> None:
    _register(client, "carol@example.com", "correct-horse-99")

    resp = _login(client, "carol@example.com", "wrong-password-00")

    assert resp.status_code == 401
    body = resp.json()
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None


# ---------------------------------------------------------------------------
# Refresh rotation (Requirement 1.5)
# ---------------------------------------------------------------------------


def test_refresh_rotates_and_invalidates_old_token(client: TestClient) -> None:
    _register(client, "dave@example.com", "dave-pass-123")
    login_body = _login(client, "dave@example.com", "dave-pass-123").json()
    old_refresh = login_body["refresh_token"]

    # Rotate using the original refresh token.
    client.cookies.clear()
    rotated = client.post(
        "/auth/refresh", cookies={REFRESH_COOKIE_NAME: old_refresh}
    )
    assert rotated.status_code == 200
    rotated_body = rotated.json()
    new_refresh = rotated_body["refresh_token"]
    assert rotated_body["access_token"]
    assert new_refresh
    assert new_refresh != old_refresh

    # The new refresh token works...
    client.cookies.clear()
    again = client.post(
        "/auth/refresh", cookies={REFRESH_COOKIE_NAME: new_refresh}
    )
    assert again.status_code == 200

    # ...but replaying the rotated-out (old) token is rejected.
    client.cookies.clear()
    replay = client.post(
        "/auth/refresh", cookies={REFRESH_COOKIE_NAME: old_refresh}
    )
    assert replay.status_code == 401


# ---------------------------------------------------------------------------
# Logout invalidation (Requirement 1.6)
# ---------------------------------------------------------------------------


def test_logout_invalidates_refresh_token(client: TestClient) -> None:
    _register(client, "erin@example.com", "erin-pass-123")
    refresh = _login(client, "erin@example.com", "erin-pass-123").json()[
        "refresh_token"
    ]

    client.cookies.clear()
    logout = client.post("/auth/logout", cookies={REFRESH_COOKIE_NAME: refresh})
    assert logout.status_code == 204

    # The logged-out refresh token can no longer be used.
    client.cookies.clear()
    after = client.post("/auth/refresh", cookies={REFRESH_COOKIE_NAME: refresh})
    assert after.status_code == 401


# ---------------------------------------------------------------------------
# Protected endpoint gating (Requirement 1.7)
# ---------------------------------------------------------------------------


def test_me_rejected_without_access_token(client: TestClient) -> None:
    resp = client.get("/me")
    assert resp.status_code == 401


def test_me_rejected_with_malformed_token(client: TestClient) -> None:
    resp = client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_me_succeeds_with_valid_access_token(client: TestClient) -> None:
    _register(client, "frank@example.com", "frank-pass-123")
    access = _login(client, "frank@example.com", "frank-pass-123").json()[
        "access_token"
    ]

    resp = client.get("/me", headers={"Authorization": f"Bearer {access}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "frank@example.com"
    assert "password_hash" not in body
