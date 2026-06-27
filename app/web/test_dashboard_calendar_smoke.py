"""Smoke checks for the dashboard summary and calendar APIs (task 10.3).

Light end-to-end coverage that ``GET /dashboard/summary`` and ``GET /calendar``
register and behave correctly through a FastAPI ``TestClient`` against an
isolated file-backed SQLite database. The fixture style mirrors
``test_read_contracts_smoke``: ``get_session`` is overridden with a per-test
async SQLite engine and ``get_settings_dep`` / ``get_refresh_token_store`` are
overridden so auth works without real infrastructure.

The exhaustive aggregation tests live in task 10.5; this module only confirms:
* both endpoints are routed;
* the dashboard counts (by priority, by status, review flag) are correct and
  scoped to the owner;
* the upcoming-deadlines list excludes past-due tasks and is ordered;
* the calendar windows inclusively on ``due_date`` and is owner-scoped;
* an inverted calendar window is rejected.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta

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
from app.data.enums import ContractStatus, TaskStatus
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


def _seed_contract_with_tasks(sessionmaker, user_id: int, *, filename: str, tasks: list[dict]) -> int:
    """Insert a contract owned by ``user_id`` and the given task dicts."""

    async def _seed(session: AsyncSession) -> int:
        contract = Contract(
            user_id=user_id,
            filename=filename,
            file_key=f"contracts/{user_id}/{filename}.key",
            file_size_kb=10,
            status=ContractStatus.COMPLETE,
        )
        session.add(contract)
        await session.flush()
        for spec in tasks:
            session.add(
                Task(
                    contract_id=contract.id,
                    title=spec.get("title", "T"),
                    priority=spec.get("priority"),
                    status=spec.get("status", TaskStatus.PENDING),
                    requires_review=spec.get("requires_review", False),
                    due_date=spec.get("due_date"),
                    source_text=spec.get("source_text", ""),
                )
            )
        await session.commit()
        return contract.id

    async def _run() -> int:
        async with sessionmaker() as session:
            return await _seed(session)

    return asyncio.run(_run())


def test_dashboard_summary_counts_and_review_scoped_to_owner(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "dash@example.com", "pw-dash-1234")
    other_id, _other_token = _register_and_login(client, "intruder@example.com", "pw-intr-1234")

    today = date.today()
    _seed_contract_with_tasks(
        sessionmaker, owner_id, filename="owner.pdf",
        tasks=[
            {"priority": "CRITICAL", "status": TaskStatus.PENDING, "requires_review": True,
             "due_date": today + timedelta(days=2)},
            {"priority": "CRITICAL", "status": TaskStatus.DONE, "requires_review": False,
             "due_date": today + timedelta(days=10)},
            {"priority": "LOW", "status": TaskStatus.PENDING, "requires_review": True,
             "due_date": today - timedelta(days=3)},  # past due -> excluded from upcoming
            {"priority": None, "status": TaskStatus.SNOOZED, "requires_review": False,
             "due_date": None},
        ],
    )
    # A different owner's tasks must not affect the summary.
    _seed_contract_with_tasks(
        sessionmaker, other_id, filename="intruder.pdf",
        tasks=[
            {"priority": "CRITICAL", "status": TaskStatus.PENDING, "requires_review": True,
             "due_date": today + timedelta(days=1)},
        ],
    )

    resp = client.get("/dashboard/summary", headers=_auth(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["counts_by_priority"] == {"CRITICAL": 2, "LOW": 1, "UNSET": 1}
    assert body["counts_by_status"] == {"PENDING": 2, "DONE": 1, "SNOOZED": 1}
    assert body["requires_review_count"] == 2

    # Upcoming excludes the past-due task and the null due_date task, ordered asc.
    upcoming = body["upcoming_deadlines"]
    due_dates = [entry["due_date"] for entry in upcoming]
    assert due_dates == sorted(due_dates)
    assert (today - timedelta(days=3)).isoformat() not in due_dates
    assert len(upcoming) == 2


def test_dashboard_summary_empty_owner(client, sessionmaker) -> None:
    _owner_id, owner_token = _register_and_login(client, "empty@example.com", "pw-empty-1234")
    resp = client.get("/dashboard/summary", headers=_auth(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts_by_priority"] == {}
    assert body["counts_by_status"] == {}
    assert body["requires_review_count"] == 0
    assert body["upcoming_deadlines"] == []


def test_calendar_windows_inclusively_and_scopes(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "cal@example.com", "pw-cal-12345")
    other_id, _other_token = _register_and_login(client, "calintr@example.com", "pw-cal-99999")

    base = date(2025, 6, 15)
    _seed_contract_with_tasks(
        sessionmaker, owner_id, filename="cal.pdf",
        tasks=[
            {"title": "before", "due_date": base - timedelta(days=1)},   # outside
            {"title": "start", "due_date": base},                        # inclusive start
            {"title": "mid", "due_date": base + timedelta(days=5)},
            {"title": "end", "due_date": base + timedelta(days=10)},     # inclusive end
            {"title": "after", "due_date": base + timedelta(days=11)},   # outside
            {"title": "nodue", "due_date": None},                        # excluded
        ],
    )
    _seed_contract_with_tasks(
        sessionmaker, other_id, filename="calintr.pdf",
        tasks=[{"title": "intruder", "due_date": base + timedelta(days=3)}],
    )

    resp = client.get(
        "/calendar",
        params={"from": base.isoformat(), "to": (base + timedelta(days=10)).isoformat()},
        headers=_auth(owner_token),
    )
    assert resp.status_code == 200, resp.text
    titles = [t["title"] for t in resp.json()]
    assert titles == ["start", "mid", "end"]


def test_calendar_inverted_window_rejected(client, sessionmaker) -> None:
    _owner_id, owner_token = _register_and_login(client, "inv@example.com", "pw-inv-12345")
    resp = client.get(
        "/calendar",
        params={"from": "2025-06-20", "to": "2025-06-10"},
        headers=_auth(owner_token),
    )
    assert resp.status_code == 400, resp.text


def test_dashboard_and_calendar_require_auth(client) -> None:
    assert client.get("/dashboard/summary").status_code == 401
    assert client.get(
        "/calendar", params={"from": "2025-01-01", "to": "2025-12-31"}
    ).status_code == 401
