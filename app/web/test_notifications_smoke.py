"""Light smoke check for the notification endpoints (task 13.1).

Confirms ``GET /notifications`` and ``PATCH /notifications/{id}`` register, scope
strictly to the authenticated owner, return notifications newest-first, mark a
notification read, reject a missing/non-owned id with 404, and require auth.
Runs through a FastAPI ``TestClient`` against an isolated, file-backed SQLite
database (the async ORM runs without a live PostgreSQL).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.data.database import Base, get_session
from app.data.models import Notification
from app.web.dependencies import get_settings_dep
from app.web.main import create_app
from app.web.revocation import InMemoryRefreshTokenStore, get_refresh_token_store

TEST_SETTINGS = Settings(
    jwt_secret="test-access-secret",
    jwt_refresh_secret="test-refresh-secret",
    jwt_algorithm="HS256",
    access_token_ttl_seconds=900,
    refresh_token_ttl_seconds=60 * 60 * 24 * 14,
)


@pytest.fixture()
def db_url() -> Iterator[str]:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
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
    engine = create_async_engine(db_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture()
def client(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> Iterator[TestClient]:
    app = create_app()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings_dep] = lambda: TEST_SETTINGS
    app.dependency_overrides[get_refresh_token_store] = lambda: InMemoryRefreshTokenStore()

    with TestClient(app) as test_client:
        yield test_client


def _query(sessionmaker, coro_fn):
    async def _run():
        async with sessionmaker() as session:
            return await coro_fn(session)

    return asyncio.run(_run())


def _register_and_login(client: TestClient, email: str, password: str) -> tuple[int, str]:
    reg = client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    user_id = reg.json()["id"]
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    client.cookies.clear()
    return user_id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed(sessionmaker, user_id: int) -> dict[str, int]:
    """Seed three notifications for ``user_id`` with distinct timestamps."""

    base = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    async def _do(session: AsyncSession) -> dict[str, int]:
        oldest = Notification(
            user_id=user_id,
            message="oldest",
            type="reminder",
            read=False,
            created_at=base,
        )
        middle = Notification(
            user_id=user_id,
            message="middle",
            type="reminder",
            read=False,
            created_at=base + timedelta(hours=1),
        )
        newest = Notification(
            user_id=user_id,
            message="newest",
            type="reminder",
            read=False,
            created_at=base + timedelta(hours=2),
        )
        session.add_all([oldest, middle, newest])
        await session.commit()
        return {"oldest": oldest.id, "middle": middle.id, "newest": newest.id}

    return _query(sessionmaker, _do)


def _read_flag(sessionmaker, notification_id: int) -> bool:
    async def _do(session: AsyncSession) -> bool:
        n = await session.get(Notification, notification_id)
        assert n is not None
        return n.read

    return _query(sessionmaker, _do)


def test_list_notifications_scopes_to_owner_newest_first(
    client: TestClient, sessionmaker
) -> None:
    owner_id, owner_token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    ids = _seed(sessionmaker, owner_id)

    # A different user with their own notifications must not see the owner's.
    other_id, other_token = _register_and_login(client, "other@example.com", "pw-other-123")
    other_ids = _seed(sessionmaker, other_id)

    resp = client.get("/notifications", headers=_auth(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    returned = [n["id"] for n in body]

    # Only the owner's notifications, newest-first.
    assert returned == [ids["newest"], ids["middle"], ids["oldest"]]
    assert other_ids["newest"] not in returned
    # Every returned row is owned by the caller.
    assert all(n["user_id"] == owner_id for n in body)


def test_patch_marks_notification_read(client: TestClient, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "patch@example.com", "pw-patch-123")
    ids = _seed(sessionmaker, owner_id)

    assert _read_flag(sessionmaker, ids["middle"]) is False

    resp = client.patch(f"/notifications/{ids['middle']}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == ids["middle"]
    assert body["read"] is True

    # Persisted.
    assert _read_flag(sessionmaker, ids["middle"]) is True


def test_patch_non_owned_returns_404(client: TestClient, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "a@example.com", "pw-a-123")
    owner_ids = _seed(sessionmaker, owner_id)

    other_id, other_token = _register_and_login(client, "b@example.com", "pw-b-123")

    # The other user cannot mark the owner's notification read.
    resp = client.patch(
        f"/notifications/{owner_ids['newest']}", headers=_auth(other_token)
    )
    assert resp.status_code == 404, resp.text
    # And the owner's notification stays unread.
    assert _read_flag(sessionmaker, owner_ids["newest"]) is False

    # A wholly missing id is also a 404.
    missing = client.patch("/notifications/999999", headers=_auth(owner_token))
    assert missing.status_code == 404, missing.text


def test_notifications_require_auth(client: TestClient) -> None:
    assert client.get("/notifications").status_code == 401
    assert client.patch("/notifications/1").status_code == 401
