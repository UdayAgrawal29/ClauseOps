"""Light smoke checks for the Progress Channel routes (task 9.2).

These are intentionally minimal -- the full ownership-denial matrix for the
WebSocket and the polling-fallback contract are covered by task 9.4. Here we
only confirm that:

* the routes register on the app (``GET /contracts/{id}/status`` and
  ``WS /ws/contracts/{id}``);
* the status endpoint requires authentication (no token -> 401); and
* an unauthenticated WebSocket connection is denied (the socket is closed
  before it is accepted, surfacing as a ``WebSocketDisconnect`` to the client),
  and never reaches Redis.

The app is exercised through a FastAPI ``TestClient`` over a throwaway SQLite
database, mirroring the fixture style of ``test_contracts``.
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
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.data.database import Base, get_session
from app.web.dependencies import get_settings_dep
from app.web.main import create_app
from app.web.progress_router import (
    WS_CLOSE_UNAUTHENTICATED,
    contract_progress_ws,
    get_contract_status,
)

TEST_SETTINGS = Settings(
    jwt_secret="test-access-secret",
    jwt_refresh_secret="test-refresh-secret",
    jwt_algorithm="HS256",
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

    with TestClient(app) as test_client:
        yield test_client


def test_progress_routes_are_registered() -> None:
    """Both the status endpoint and the WebSocket route mount on the app."""

    app = create_app()
    paths = {route.path for route in app.router.routes}
    assert "/contracts/{contract_id}/status" in paths
    assert "/ws/contracts/{contract_id}" in paths

    # The mounted endpoints are exactly the ones from the progress router.
    by_path = {getattr(r, "path", None): r for r in app.router.routes}
    assert by_path["/contracts/{contract_id}/status"].endpoint is get_contract_status
    assert by_path["/ws/contracts/{contract_id}"].endpoint is contract_progress_ws


def test_status_endpoint_requires_auth(client: TestClient) -> None:
    """No bearer token -> 401 from the protected status endpoint."""

    resp = client.get("/contracts/1/status")
    assert resp.status_code == 401, resp.text


def test_unauthenticated_websocket_is_denied(client: TestClient) -> None:
    """A WS connection without a token is closed before accept (denied).

    The client observes a ``WebSocketDisconnect`` because the server rejects the
    handshake (closes pre-accept). No Redis access happens on this path.
    """

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/contracts/1"):
            pass
    assert exc_info.value.code == WS_CLOSE_UNAUTHENTICATED


def test_websocket_with_invalid_token_is_denied(client: TestClient) -> None:
    """A WS connection with a garbage token is denied the same way."""

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/contracts/1?token=not-a-real-jwt"):
            pass
    assert exc_info.value.code == WS_CLOSE_UNAUTHENTICATED
