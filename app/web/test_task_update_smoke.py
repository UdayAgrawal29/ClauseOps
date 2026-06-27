"""Light smoke check for ``PATCH /tasks/{id}`` (task 12.1).

Confirms the mutation endpoint: applies a valid status change, rejects an
invalid status while leaving the Task unchanged, records field corrections in
``corrected_fields`` (setting ``is_user_corrected``), and returns a uniform 404
for a task the caller does not own. Exercises the happy path through a FastAPI
``TestClient`` against an isolated, file-backed SQLite database (the async ORM
runs without a live PostgreSQL). Full property/audit coverage lives in tasks
12.3/12.4.
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
from app.data.models import AuditLog, Contract, Task
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


def _seed_task(sessionmaker, user_id: int) -> int:
    """Seed one contract with a single PENDING task for ``user_id``; return its id."""

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
            due_date=date(2030, 6, 1),
            source_text="x",
        )
        session.add(t)
        await session.commit()
        return t.id

    return _query(sessionmaker, _do)


def _fetch_task(sessionmaker, task_id: int) -> Task:
    async def _do(session: AsyncSession) -> Task:
        return await session.get(Task, task_id)

    return _query(sessionmaker, _do)


def _fetch_audit_logs(sessionmaker, task_id: int) -> list[AuditLog]:
    """Return all AuditLog rows for ``task_id`` (entity ``"Task"``)."""

    async def _do(session: AsyncSession) -> list[AuditLog]:
        from sqlalchemy import select

        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.entity == "Task")
            .where(AuditLog.entity_id == task_id)
        )
        return list(result.scalars().all())

    return _query(sessionmaker, _do)


def test_valid_status_change_applies(client: TestClient, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    task_id = _seed_task(sessionmaker, owner_id)

    resp = client.patch(f"/tasks/{task_id}", json={"status": "DONE"}, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DONE"

    assert _fetch_task(sessionmaker, task_id).status is TaskStatus.DONE


def test_invalid_status_rejected_and_task_unchanged(
    client: TestClient, sessionmaker
) -> None:
    owner_id, token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    task_id = _seed_task(sessionmaker, owner_id)

    resp = client.patch(
        f"/tasks/{task_id}", json={"status": "ARCHIVED"}, headers=_auth(token)
    )
    assert resp.status_code == 422, resp.text

    # The Task must be left entirely unchanged (no partial application).
    task = _fetch_task(sessionmaker, task_id)
    assert task.status is TaskStatus.PENDING
    assert task.is_user_corrected is False


def test_field_correction_records_overrides(client: TestClient, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    task_id = _seed_task(sessionmaker, owner_id)

    resp = client.patch(
        f"/tasks/{task_id}",
        json={"obligated_party": "Beta LLC", "priority": "HIGH"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["obligated_party"] == "Beta LLC"
    assert body["priority"] == "HIGH"
    assert body["is_user_corrected"] is True
    assert body["corrected_fields"]["obligated_party"] == {
        "old": "Acme Corp",
        "new": "Beta LLC",
    }
    assert body["corrected_fields"]["priority"] == {"old": "LOW", "new": "HIGH"}

    task = _fetch_task(sessionmaker, task_id)
    assert task.obligated_party == "Beta LLC"
    assert task.is_user_corrected is True


def test_non_owned_task_returns_404(client: TestClient, sessionmaker) -> None:
    owner_id, _owner_token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    task_id = _seed_task(sessionmaker, owner_id)

    _other_id, other_token = _register_and_login(client, "other@example.com", "pw-other-123")
    resp = client.patch(
        f"/tasks/{task_id}", json={"status": "DONE"}, headers=_auth(other_token)
    )
    assert resp.status_code == 404, resp.text

    # The owner's task is untouched by the non-owner's attempt.
    assert _fetch_task(sessionmaker, task_id).status is TaskStatus.PENDING


# --- Audit logging (task 12.2, Requirement 7.4) -----------------------------


def test_status_change_writes_single_audit_row(
    client: TestClient, sessionmaker
) -> None:
    owner_id, token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    task_id = _seed_task(sessionmaker, owner_id)

    resp = client.patch(f"/tasks/{task_id}", json={"status": "DONE"}, headers=_auth(token))
    assert resp.status_code == 200, resp.text

    logs = _fetch_audit_logs(sessionmaker, task_id)
    assert len(logs) == 1
    entry = logs[0]
    assert entry.user_id == owner_id
    assert entry.entity == "Task"
    assert entry.entity_id == task_id
    assert entry.action == "task.update"
    assert entry.before == {"status": "PENDING"}
    assert entry.after == {"status": "DONE"}


def test_field_correction_writes_single_audit_row(
    client: TestClient, sessionmaker
) -> None:
    owner_id, token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    task_id = _seed_task(sessionmaker, owner_id)

    resp = client.patch(
        f"/tasks/{task_id}",
        json={"obligated_party": "Beta LLC", "priority": "HIGH"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text

    logs = _fetch_audit_logs(sessionmaker, task_id)
    assert len(logs) == 1
    entry = logs[0]
    assert entry.user_id == owner_id
    assert entry.action == "task.update"
    assert entry.before == {"obligated_party": "Acme Corp", "priority": "LOW"}
    assert entry.after == {"obligated_party": "Beta LLC", "priority": "HIGH"}


def test_noop_patch_writes_no_audit_row(client: TestClient, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    task_id = _seed_task(sessionmaker, owner_id)

    # Re-send the stored values: nothing actually changes.
    resp = client.patch(
        f"/tasks/{task_id}",
        json={"status": "PENDING", "obligated_party": "Acme Corp", "priority": "LOW"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text

    assert _fetch_audit_logs(sessionmaker, task_id) == []


def test_rejected_status_writes_no_audit_row(
    client: TestClient, sessionmaker
) -> None:
    owner_id, token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    task_id = _seed_task(sessionmaker, owner_id)

    resp = client.patch(
        f"/tasks/{task_id}", json={"status": "ARCHIVED"}, headers=_auth(token)
    )
    assert resp.status_code == 422, resp.text

    # No audit row and the task is left unchanged.
    assert _fetch_audit_logs(sessionmaker, task_id) == []
    assert _fetch_task(sessionmaker, task_id).status is TaskStatus.PENDING
