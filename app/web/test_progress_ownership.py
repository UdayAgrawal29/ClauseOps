"""Unit tests for Progress Channel ownership + forwarding (task 9.4).

Exercises the read side of the live-progress feature implemented in
``app.web.progress_router`` end-to-end through a FastAPI ``TestClient`` against
an isolated, file-backed SQLite database (so the async ORM runs without a live
PostgreSQL), mirroring the fixture style of ``test_contracts`` /
``test_auth_flows``. The ``get_session`` and ``get_settings_dep`` dependencies
are overridden; users are created via the real register/login flow and
Contracts are seeded directly through the test sessionmaker.

Coverage (Requirements 4.5, 4.8):

* ``GET /contracts/{id}/status`` (the polling fallback, Req 4.5):
    - the owner gets 200 with the Contract's current ``{status, progress_pct}``;
    - a different (non-owning) user gets a uniform 404;
    - a missing contract gives 404;
    - no auth gives 401.

* ``WS /ws/contracts/{id}`` (ownership enforcement + forwarding, Req 4.8 / 4.4):
    - unauthenticated (no token) is denied/closed with ``4401``;
    - an invalid token is denied with ``4401``;
    - a valid token for a non-owner is denied with ``4403``;
    - a valid token for a missing contract is denied with ``4403``;
    - the OWNER with a valid token has the connection accepted and synthetic
      progress messages fed through an overridden ``_subscriber_factory`` are
      forwarded verbatim, and the stream closes on a terminal status.

The WebSocket forwarding path is exercised WITHOUT a live Redis by
``monkeypatch``-ing ``app.web.progress_router._subscriber_factory`` with a fake
async context manager that yields an async iterator of dict messages.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

import app.web.progress_router as progress_router
from app.config import Settings
from app.data.database import Base, get_session
from app.data.enums import ContractStatus
from app.data.models import Contract
from app.web.dependencies import get_settings_dep
from app.web.main import create_app
from app.web.progress_router import (
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_UNAUTHENTICATED,
)

# Fixed, known secrets so token issue/verify are consistent across the
# register/login endpoints and the WebSocket query-param token check.
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
def client(sessionmaker: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    """TestClient with the DB session and settings dependencies overridden."""

    app = create_app()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings_dep] = lambda: TEST_SETTINGS

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _query(sessionmaker: async_sessionmaker[AsyncSession], coro_fn):
    """Run a one-off async DB query/operation against the test database."""

    async def _run():
        async with sessionmaker() as session:
            return await coro_fn(session)

    return asyncio.run(_run())


def _register_and_login(client: TestClient, email: str, password: str) -> tuple[int, str]:
    """Register then login a user; return (user_id, access_token)."""

    reg = client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    user_id = reg.json()["id"]

    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    # Clear the refresh cookie so it does not bleed into subsequent requests.
    client.cookies.clear()
    return user_id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_contract(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    status: ContractStatus = ContractStatus.PROCESSING,
    progress_pct: int = 0,
    filename: str = "seed.pdf",
) -> int:
    """Insert a Contract owned by ``user_id`` and return its id."""

    async def _insert(session: AsyncSession) -> int:
        contract = Contract(
            user_id=user_id,
            filename=filename,
            # file_key is unique + non-null; make it unique per seed.
            file_key=f"contracts/{user_id}/{filename}-{progress_pct}-{status.value}",
            status=status,
            progress_pct=progress_pct,
        )
        session.add(contract)
        await session.commit()
        await session.refresh(contract)
        return contract.id

    return _query(sessionmaker, _insert)


def _fake_subscriber_factory(messages: list[dict[str, Any]]):
    """Build a fake ``_subscriber_factory`` yielding ``messages`` (no Redis).

    Matches the real seam's signature ``(redis_url, channel) -> async context
    manager`` whose body yields an async iterator of decoded message dicts.
    """

    @asynccontextmanager
    async def _factory(redis_url: str, channel: str):
        async def _iter() -> AsyncIterator[dict[str, Any]]:
            for message in messages:
                yield message

        yield _iter()

    return _factory


# ---------------------------------------------------------------------------
# GET /contracts/{id}/status -- polling fallback (Requirement 4.5)
# ---------------------------------------------------------------------------


def test_status_owner_gets_200_with_current_status_and_progress(
    client: TestClient, sessionmaker
) -> None:
    user_id, token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    contract_id = _seed_contract(
        sessionmaker,
        user_id=user_id,
        status=ContractStatus.PROCESSING,
        progress_pct=42,
    )

    resp = client.get(f"/contracts/{contract_id}/status", headers=_auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contract_id"] == contract_id
    assert body["status"] == ContractStatus.PROCESSING.value
    assert body["progress_pct"] == 42


def test_status_non_owner_gets_404(client: TestClient, sessionmaker) -> None:
    owner_id, _owner_token = _register_and_login(client, "owns@example.com", "pw-owns-123")
    contract_id = _seed_contract(sessionmaker, user_id=owner_id, progress_pct=10)

    _other_id, other_token = _register_and_login(
        client, "intruder@example.com", "pw-int-123"
    )
    resp = client.get(f"/contracts/{contract_id}/status", headers=_auth(other_token))

    # Uniform 404 so other tenants' contract ids are never revealed.
    assert resp.status_code == 404, resp.text


def test_status_missing_contract_gets_404(client: TestClient) -> None:
    _user_id, token = _register_and_login(client, "ghost@example.com", "pw-ghost-123")

    resp = client.get("/contracts/999999/status", headers=_auth(token))

    assert resp.status_code == 404, resp.text


def test_status_without_auth_gets_401(client: TestClient) -> None:
    resp = client.get("/contracts/1/status")

    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# WS /ws/contracts/{id} -- ownership denial matrix (Requirement 4.8)
# ---------------------------------------------------------------------------


def test_ws_unauthenticated_no_token_denied_4401(client: TestClient) -> None:
    """No token -> socket closed pre-accept with 4401 (denial)."""

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/contracts/1"):
            pass
    assert exc_info.value.code == WS_CLOSE_UNAUTHENTICATED


def test_ws_invalid_token_denied_4401(client: TestClient) -> None:
    """A garbage token -> 4401 (treated as unauthenticated)."""

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/contracts/1?token=not-a-real-jwt"):
            pass
    assert exc_info.value.code == WS_CLOSE_UNAUTHENTICATED


def test_ws_valid_token_non_owner_denied_4403(client: TestClient, sessionmaker) -> None:
    """A valid token whose user does not own the contract -> 4403."""

    owner_id, _owner_token = _register_and_login(client, "wsowns@example.com", "pw-owns-123")
    contract_id = _seed_contract(sessionmaker, user_id=owner_id)

    _other_id, other_token = _register_and_login(
        client, "wsintruder@example.com", "pw-int-123"
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/contracts/{contract_id}?token={other_token}"):
            pass
    assert exc_info.value.code == WS_CLOSE_FORBIDDEN


def test_ws_valid_token_missing_contract_denied_4403(client: TestClient) -> None:
    """A valid token but no such contract -> 4403 (same as non-owned)."""

    _user_id, token = _register_and_login(client, "wsghost@example.com", "pw-ghost-123")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/contracts/999999?token={token}"):
            pass
    assert exc_info.value.code == WS_CLOSE_FORBIDDEN


# ---------------------------------------------------------------------------
# WS /ws/contracts/{id} -- owner accepted + forwarding (Requirements 4.4, 4.8)
# ---------------------------------------------------------------------------


def test_ws_owner_receives_forwarded_progress_and_stream_closes_on_complete(
    client: TestClient, sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner connects and synthetic progress messages are forwarded.

    The stream terminates after a COMPLETE status, so the server closes the
    socket once the terminal message has been delivered.
    """

    user_id, token = _register_and_login(client, "wsowner@example.com", "pw-owner-123")
    contract_id = _seed_contract(
        sessionmaker, user_id=user_id, status=ContractStatus.PROCESSING, progress_pct=0
    )

    messages = [
        {"stage": "segment", "progress_pct": 25, "status": ContractStatus.PROCESSING.value},
        {"stage": "classify", "progress_pct": 60, "status": ContractStatus.PROCESSING.value},
        {"stage": "generate_tasks", "progress_pct": 100, "status": ContractStatus.COMPLETE.value},
    ]
    monkeypatch.setattr(
        progress_router, "_subscriber_factory", _fake_subscriber_factory(messages)
    )

    with client.websocket_connect(f"/ws/contracts/{contract_id}?token={token}") as ws:
        received = [ws.receive_json() for _ in messages]
        # Each forwarded message matches what the subscriber yielded, verbatim.
        assert received == messages
        # After the terminal COMPLETE message the server closes the stream.
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_owner_receives_forwarded_progress_and_stream_closes_on_failed(
    client: TestClient, sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terminal FAILED status also ends the forwarded stream."""

    user_id, token = _register_and_login(client, "wsfail@example.com", "pw-fail-123")
    contract_id = _seed_contract(
        sessionmaker, user_id=user_id, status=ContractStatus.PROCESSING, progress_pct=10
    )

    messages = [
        {"stage": "extract", "progress_pct": 15, "status": ContractStatus.PROCESSING.value},
        {"stage": "extract", "progress_pct": 15, "status": ContractStatus.FAILED.value},
    ]
    monkeypatch.setattr(
        progress_router, "_subscriber_factory", _fake_subscriber_factory(messages)
    )

    with client.websocket_connect(f"/ws/contracts/{contract_id}?token={token}") as ws:
        assert ws.receive_json() == messages[0]
        assert ws.receive_json() == messages[1]
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
