"""Property test for task-status validity (task 12.3).

**Property 4: Task-status validity**
**Validates: Requirements 7.1, 7.2**

A Task's ``status`` is always one of the four domain members
{PENDING, DONE, SNOOZED, DISMISSED}. A ``PATCH /tasks/{id}`` requesting any
other value is rejected by request validation (HTTP 422/400) and leaves the
Task entirely unchanged; a request with a valid member is applied.

The test drives the real endpoint through a FastAPI ``TestClient`` against an
isolated, file-backed SQLite database (the same harness style as
``test_task_update_smoke.py``): ``get_session``/``get_settings_dep``/
``get_refresh_token_store`` are overridden, a user is registered and logged in,
and one owned task is seeded via the sessionmaker. Each Hypothesis example
issues live HTTP calls against that per-test database and re-reads the row from
the DB to confirm the persisted status.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
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

# The four domain members that a Task.status may ever hold (Requirement 7.1).
_VALID_NAMES = frozenset(member.value for member in TaskStatus)


# --- Strategies --------------------------------------------------------------
def valid_status_strategy() -> st.SearchStrategy[str]:
    """A strategy over the 4 VALID TaskStatus member values."""

    return st.sampled_from(sorted(_VALID_NAMES))


def invalid_status_strategy() -> st.SearchStrategy[str]:
    """A strategy over INVALID status payloads.

    Mixes arbitrary text, lowercase variants of valid names, numbers, and the
    empty string -- always excluding the four exact valid member values.
    """

    return st.one_of(
        st.text(),
        st.sampled_from([name.lower() for name in _VALID_NAMES]),
        st.sampled_from(["", " ", "ARCHIVED", "done ", "Pending", "0", "123", "null"]),
        st.integers().map(str),
    ).filter(lambda value: value not in _VALID_NAMES)


# --- DB / app harness --------------------------------------------------------
def _query(sessionmaker, coro_fn):
    async def _run():
        async with sessionmaker() as session:
            return await coro_fn(session)

    return asyncio.run(_run())


def _seed_task(sessionmaker, user_id: int) -> int:
    async def _do(session: AsyncSession) -> int:
        c = Contract(user_id=user_id, filename="a.pdf", file_key=f"k/{user_id}/a")
        session.add(c)
        await session.flush()
        t = Task(
            contract_id=c.id,
            title="Original title",
            obligated_party="Acme Corp",
            priority="LOW",
            status=TaskStatus.PENDING,
            source_text="x",
        )
        session.add(t)
        await session.commit()
        return t.id

    return _query(sessionmaker, _do)


def _fetch_status(sessionmaker, task_id: int) -> TaskStatus:
    async def _do(session: AsyncSession) -> TaskStatus:
        task = await session.get(Task, task_id)
        return task.status

    return _query(sessionmaker, _do)


class _Harness:
    """A built app + client + seeded owned task reused across examples."""

    def __init__(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self._path = path
        url = f"sqlite+aiosqlite:///{path}"

        async def _create() -> None:
            engine = create_async_engine(url, poolclass=NullPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await engine.dispose()

        asyncio.run(_create())

        engine = create_async_engine(url, poolclass=NullPool)
        self.sessionmaker = async_sessionmaker(
            engine, expire_on_commit=False, autoflush=False
        )

        app = create_app()

        async def _override_get_session() -> AsyncIterator[AsyncSession]:
            async with self.sessionmaker() as session:
                yield session

        app.dependency_overrides[get_session] = _override_get_session
        app.dependency_overrides[get_settings_dep] = lambda: TEST_SETTINGS
        app.dependency_overrides[get_refresh_token_store] = (
            lambda: InMemoryRefreshTokenStore()
        )

        self.client = TestClient(app)
        self.client.__enter__()

        reg = self.client.post(
            "/auth/register",
            json={"email": "owner@example.com", "password": "pw-owner-123"},
        )
        assert reg.status_code == 201, reg.text
        user_id = reg.json()["id"]
        login = self.client.post(
            "/auth/login",
            json={"email": "owner@example.com", "password": "pw-owner-123"},
        )
        assert login.status_code == 200, login.text
        self.token = login.json()["access_token"]
        self.client.cookies.clear()

        self.task_id = _seed_task(self.sessionmaker, user_id)

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        try:
            os.remove(self._path)
        except OSError:
            pass


@pytest.fixture()
def harness() -> Iterator[_Harness]:
    h = _Harness()
    try:
        yield h
    finally:
        h.close()


# --- Properties --------------------------------------------------------------
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(new_status=valid_status_strategy())
def test_valid_status_is_applied_and_within_domain(
    harness: _Harness, new_status: str
) -> None:
    """Validates: Requirements 7.1, 7.2.

    A valid status is applied (200, returned + persisted status equals it) and
    the persisted status is always one of the four domain members.
    """

    resp = harness.client.patch(
        f"/tasks/{harness.task_id}",
        json={"status": new_status},
        headers=harness.auth,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == new_status

    persisted = _fetch_status(harness.sessionmaker, harness.task_id)
    assert persisted is TaskStatus(new_status)
    assert persisted.value in _VALID_NAMES


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(bad_status=invalid_status_strategy())
def test_invalid_status_is_rejected_and_leaves_task_unchanged(
    harness: _Harness, bad_status: str
) -> None:
    """Validates: Requirements 7.1, 7.2.

    A mutation requesting a non-domain status is rejected (422/400) and the
    Task's status is unchanged; the persisted status stays within the domain.
    """

    before = _fetch_status(harness.sessionmaker, harness.task_id)

    resp = harness.client.patch(
        f"/tasks/{harness.task_id}",
        json={"status": bad_status},
        headers=harness.auth,
    )
    assert resp.status_code in (400, 422), resp.text

    after = _fetch_status(harness.sessionmaker, harness.task_id)
    assert after is before
    assert after.value in _VALID_NAMES
