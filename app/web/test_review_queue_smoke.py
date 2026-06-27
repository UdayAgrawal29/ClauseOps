"""Smoke checks for the review-queue endpoint (task 10.4).

Light end-to-end coverage that ``GET /review-queue`` registers and behaves
correctly through a FastAPI ``TestClient`` against an isolated file-backed
SQLite database. The fixture style mirrors ``test_read_contracts_smoke``:
``get_session`` is overridden with a per-test async SQLite engine and
``get_settings_dep`` / ``get_refresh_token_store`` are overridden so auth works
without real infrastructure.

The exhaustive read-API tests live in task 10.5; this module only confirms:
* the endpoint is routed;
* it returns only the owner's ``requires_review`` tasks;
* it excludes the owner's non-flagged tasks and another user's flagged task.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import date

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
from app.data.enums import TaskStatus
from app.data.models import Contract, Task
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
def client(sessionmaker: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    app = create_app()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings_dep] = lambda: TEST_SETTINGS
    app.dependency_overrides[get_refresh_token_store] = lambda: InMemoryRefreshTokenStore()

    with TestClient(app) as test_client:
        yield test_client


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


def _seed_task(
    sessionmaker,
    user_id: int,
    *,
    title: str,
    requires_review: bool,
    due_date: date | None = None,
) -> int:
    """Insert a contract owning a single task and return the task id."""

    async def _seed(session: AsyncSession) -> int:
        contract = Contract(
            user_id=user_id,
            filename=f"{title}.pdf",
            file_key=f"contracts/{user_id}/{title}.key",
            file_size_kb=10,
        )
        session.add(contract)
        await session.flush()

        task = Task(
            contract_id=contract.id,
            title=title,
            status=TaskStatus.PENDING,
            requires_review=requires_review,
            due_date=due_date,
            source_text=f"{title} source text",
        )
        session.add(task)
        await session.commit()
        return task.id

    async def _run() -> int:
        async with sessionmaker() as session:
            return await _seed(session)

    return asyncio.run(_run())


def test_review_queue_returns_only_owner_flagged_tasks(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    other_id, _other_token = _register_and_login(client, "other@example.com", "pw-other-123")

    # Owner's flagged tasks: one dated, one undated (dated should sort first).
    flagged_dated = _seed_task(
        sessionmaker, owner_id, title="flagged-dated",
        requires_review=True, due_date=date(2025, 1, 1),
    )
    flagged_undated = _seed_task(
        sessionmaker, owner_id, title="flagged-undated", requires_review=True,
    )
    # Owner's non-flagged task must be excluded.
    _seed_task(sessionmaker, owner_id, title="not-flagged", requires_review=False)
    # Another user's flagged task must never leak into the owner's queue.
    _seed_task(sessionmaker, other_id, title="intruder-flagged", requires_review=True)

    resp = client.get("/review-queue", headers=_auth(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    ids = [row["id"] for row in body]
    # Only the owner's two flagged tasks, dated first (nulls last).
    assert ids == [flagged_dated, flagged_undated]
    assert all(row["requires_review"] is True for row in body)
