"""Light smoke check for ``GET /tasks`` (task 10.2).

Confirms the endpoint registers, scopes results to the authenticated owner, and
that each optional filter narrows results correctly. Full unit coverage of
filter combinations lives in task 10.5; this file keeps the seed small and
exercises the happy path through a FastAPI ``TestClient`` against an isolated,
file-backed SQLite database (the async ORM runs without a live PostgreSQL).
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
    """Seed two contracts with a few tasks for ``user_id``; return id map."""

    async def _do(session: AsyncSession) -> dict[str, int]:
        c1 = Contract(user_id=user_id, filename="a.pdf", file_key=f"k/{user_id}/a")
        c2 = Contract(user_id=user_id, filename="b.pdf", file_key=f"k/{user_id}/b")
        session.add_all([c1, c2])
        await session.flush()

        # c1: a high-priority dated task pending review, and an undated done task.
        t_high = Task(
            contract_id=c1.id,
            title="High priority dated",
            priority="HIGH",
            status=TaskStatus.PENDING,
            requires_review=True,
            due_date=date(2030, 6, 1),
            source_text="x",
        )
        t_undated = Task(
            contract_id=c1.id,
            title="Undated done",
            priority="LOW",
            status=TaskStatus.DONE,
            requires_review=False,
            due_date=None,
            source_text="x",
        )
        # c2: a low-priority dated task far in the future.
        t_far = Task(
            contract_id=c2.id,
            title="Far dated",
            priority="LOW",
            status=TaskStatus.PENDING,
            requires_review=False,
            due_date=date(2031, 1, 1),
            source_text="x",
        )
        session.add_all([t_high, t_undated, t_far])
        await session.commit()
        return {
            "c1": c1.id,
            "c2": c2.id,
            "t_high": t_high.id,
            "t_undated": t_undated.id,
            "t_far": t_far.id,
        }

    return _query(sessionmaker, _do)


def test_list_tasks_scopes_to_owner_and_orders_deadline_first(
    client: TestClient, sessionmaker
) -> None:
    owner_id, owner_token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    ids = _seed(sessionmaker, owner_id)

    # A different user with their own task must not see the owner's tasks.
    other_id, other_token = _register_and_login(client, "other@example.com", "pw-other-123")
    other_ids = _seed(sessionmaker, other_id)

    resp = client.get("/tasks", headers=_auth(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    returned = [t["id"] for t in body]

    # Only the owner's three tasks are returned (none of the other user's).
    assert set(returned) == {ids["t_high"], ids["t_undated"], ids["t_far"]}
    assert other_ids["t_high"] not in returned

    # Deadline-first ordering: dated ascending, then undated last.
    assert returned == [ids["t_high"], ids["t_far"], ids["t_undated"]]


def test_list_tasks_filters_narrow_results(client: TestClient, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "filter@example.com", "pw-filter-123")
    ids = _seed(sessionmaker, owner_id)

    def get(**params) -> list[int]:
        resp = client.get("/tasks", params=params, headers=_auth(token))
        assert resp.status_code == 200, resp.text
        return [t["id"] for t in resp.json()]

    # status filter
    assert set(get(status="PENDING")) == {ids["t_high"], ids["t_far"]}
    assert get(status="DONE") == [ids["t_undated"]]

    # priority filter
    assert get(priority="HIGH") == [ids["t_high"]]
    assert set(get(priority="LOW")) == {ids["t_far"], ids["t_undated"]}

    # requires_review filter
    assert get(requires_review=True) == [ids["t_high"]]

    # contract_id filter
    assert set(get(contract_id=ids["c1"])) == {ids["t_high"], ids["t_undated"]}
    assert get(contract_id=ids["c2"]) == [ids["t_far"]]

    # due_before / due_after (inclusive; undated excluded when a bound is set)
    assert get(due_before="2030-12-31") == [ids["t_high"]]
    assert get(due_after="2030-12-31") == [ids["t_far"]]

    # combined AND: pending + low priority -> only the far dated task
    assert get(status="PENDING", priority="LOW") == [ids["t_far"]]
