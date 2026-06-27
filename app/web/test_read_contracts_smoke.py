"""Smoke checks for the read APIs (task 10.1).

Light end-to-end coverage that ``GET /contracts`` and ``GET /contracts/{id}``
register and behave correctly through a FastAPI ``TestClient`` against an
isolated file-backed SQLite database. The fixture style mirrors
``test_contracts``: ``get_session`` is overridden with a per-test async SQLite
engine and ``get_settings_dep`` / ``get_refresh_token_store`` are overridden so
auth works without real infrastructure.

The exhaustive read-API tests live in task 10.5; this module only confirms:
* both endpoints are routed;
* the list scopes to the caller and honors the ``status`` filter (newest-first);
* the detail endpoint returns clauses + tasks with span offsets for an owned
  contract, and 404s for a non-owned/missing one.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator

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
from app.data.models import Clause, Contract, Task
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


def _seed_contract(
    sessionmaker,
    user_id: int,
    *,
    filename: str,
    status: ContractStatus = ContractStatus.PENDING,
    with_analysis: bool = False,
) -> int:
    """Insert a contract (optionally with one clause + one grounded task)."""

    async def _seed(session: AsyncSession) -> int:
        contract = Contract(
            user_id=user_id,
            filename=filename,
            file_key=f"contracts/{user_id}/{filename}.key",
            file_size_kb=10,
            status=status,
        )
        session.add(contract)
        await session.flush()

        if with_analysis:
            clause = Clause(
                contract_id=contract.id,
                clause_index=0,
                heading="Section 1",
                body_text="The Supplier shall deliver the goods.",
            )
            session.add(clause)
            await session.flush()

            source = "The Supplier shall deliver the goods."
            party = "The Supplier"
            action = "deliver the goods"
            task = Task(
                contract_id=contract.id,
                clause_id=clause.id,
                title="Deliver the goods",
                obligated_party=party,
                action=action,
                agent_start=source.find(party),
                agent_end=source.find(party) + len(party),
                action_start=source.find(action),
                action_end=source.find(action) + len(action),
                status=TaskStatus.PENDING,
                source_text=source,
            )
            session.add(task)
        await session.commit()
        return contract.id

    async def _run():
        async with sessionmaker() as session:
            return await _seed(session)

    return asyncio.run(_run())


def test_list_contracts_scopes_to_owner_newest_first(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    other_id, _other_token = _register_and_login(client, "other@example.com", "pw-other-123")

    first = _seed_contract(sessionmaker, owner_id, filename="first.pdf")
    second = _seed_contract(sessionmaker, owner_id, filename="second.pdf")
    # A contract owned by a different user must never appear in the owner's list.
    _seed_contract(sessionmaker, other_id, filename="intruder.pdf")

    resp = client.get("/contracts", headers=_auth(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    ids = [row["id"] for row in body]
    # Only the owner's two contracts, newest-first (higher id created later).
    assert ids == [second, first]
    assert all("clauses" not in row and "tasks" not in row for row in body)


def test_list_contracts_status_filter(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "filter@example.com", "pw-filter-123")

    _seed_contract(sessionmaker, owner_id, filename="pending.pdf", status=ContractStatus.PENDING)
    complete_id = _seed_contract(
        sessionmaker, owner_id, filename="done.pdf", status=ContractStatus.COMPLETE
    )

    resp = client.get(
        "/contracts", params={"status": "COMPLETE"}, headers=_auth(owner_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [row["id"] for row in body] == [complete_id]
    assert body[0]["status"] == "COMPLETE"


def test_get_contract_returns_clauses_and_tasks_with_spans(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "detail@example.com", "pw-detail-123")
    contract_id = _seed_contract(
        sessionmaker, owner_id, filename="analysis.pdf",
        status=ContractStatus.COMPLETE, with_analysis=True,
    )

    resp = client.get(f"/contracts/{contract_id}", headers=_auth(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["id"] == contract_id
    assert len(body["clauses"]) == 1
    assert len(body["tasks"]) == 1

    task = body["tasks"][0]
    source = task["source_text"]
    # The grounding span offsets round-trip back to the party/action text.
    assert source[task["agent_start"]:task["agent_end"]] == task["obligated_party"]
    assert source[task["action_start"]:task["action_end"]] == task["action"]


def test_get_contract_non_owned_returns_404(client, sessionmaker) -> None:
    owner_id, _owner_token = _register_and_login(client, "real@example.com", "pw-real-123")
    _other_id, other_token = _register_and_login(client, "thief@example.com", "pw-thief-123")

    contract_id = _seed_contract(sessionmaker, owner_id, filename="secret.pdf")

    resp = client.get(f"/contracts/{contract_id}", headers=_auth(other_token))
    assert resp.status_code == 404, resp.text

    # A missing contract is likewise a 404 (no enumeration).
    missing = client.get("/contracts/999999", headers=_auth(other_token))
    assert missing.status_code == 404, missing.text
