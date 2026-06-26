"""Progress Channel: WebSocket stream + status-polling fallback (design "Component 5").

This module implements task 9.2 -- the read side of the live-progress feature
whose publisher is :mod:`app.processing.progress`:

* ``GET /contracts/{id}/status`` -- the polling fallback (Requirement 4.5). An
  authenticated owner gets the Contract's current ``{ status, progress_pct }``
  (plus a ``stage`` field, kept ``None`` for parity with the streamed payload).
  A missing or non-owned contract resolves to a uniform 404 via
  :class:`~app.data.ownership.OwnershipError`.

* ``WS /ws/contracts/{id}`` -- the live stream (Requirements 4.4, 4.8). The
  socket authenticates the caller from a ``?token=`` query parameter (a normal
  ``Authorization: Bearer`` dependency is awkward over WebSockets), then enforces
  ownership of the contract **before** accepting the connection. Unauthenticated
  or non-owning callers are denied without the socket ever being accepted and
  without any subscription being made. Only after both checks pass does the
  endpoint accept the socket, subscribe to the Contract's Redis pub/sub channel
  (:func:`~app.processing.progress.contract_progress_channel`), and forward each
  ``{ stage, progress_pct, status }`` message to the client until the client
  disconnects or a terminal (COMPLETE/FAILED) status is seen.

Auth approach for the WebSocket
-------------------------------
FastAPI/Starlette WebSockets cannot reuse the HTTP ``HTTPBearer`` dependency
cleanly (browsers cannot set arbitrary headers on the WS handshake), so the
access token is passed as the ``token`` query parameter, e.g.
``/ws/contracts/12?token=<access-jwt>``. It is verified with
:func:`~app.auth.security.decode_access_token`; a missing/invalid/expired token
closes the socket with application close code ``4401`` (an unauthorized variant
of the ``1008`` policy-violation code). A valid token that does not own the
contract closes with ``4403``. The close happens *before* ``accept()``, so an
unauthorized client never receives a streaming connection.

Redis is connected **lazily** (only after auth + ownership succeed) via
``redis.asyncio``; importing this module never requires a running broker. The
subscriber is obtained through :data:`_subscriber_factory`, an indirection seam
that tests (task 9.4) can override to exercise forwarding without a live Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import TokenError, decode_access_token
from app.config import Settings
from app.data.database import get_session, get_sessionmaker
from app.data.enums import ContractStatus
from app.data.models import Contract
from app.data.ownership import get_owned_or_none, require_owned
from app.processing.progress import contract_progress_channel
from app.web.dependencies import CurrentUser, DbSession, get_settings_dep
from app.web.schemas import ContractStatusResponse

logger = logging.getLogger(__name__)

# Application-defined WebSocket close codes (4000-4999 is the private range).
# 4401 mirrors HTTP 401 (no/invalid credentials); 4403 mirrors HTTP 403/404
# (authenticated but not the owner). Both are variants of the 1008
# policy-violation close code.
WS_CLOSE_UNAUTHENTICATED = 4401
WS_CLOSE_FORBIDDEN = 4403

# Statuses that end the stream: once observed there is nothing more to forward.
_TERMINAL_STATUSES = {ContractStatus.COMPLETE.value, ContractStatus.FAILED.value}

router = APIRouter(tags=["progress"])


# ---------------------------------------------------------------------------
# Status-polling fallback (Requirement 4.5)
# ---------------------------------------------------------------------------


@router.get(
    "/contracts/{contract_id}/status",
    response_model=ContractStatusResponse,
)
async def get_contract_status(
    contract_id: int,
    current_user: CurrentUser,
    session: DbSession,
) -> ContractStatusResponse:
    """Return the Contract's current ``status``/``progress_pct`` (polling fallback).

    Requires authentication (via the standard bearer dependency) and ownership:
    :func:`~app.data.ownership.require_owned` raises
    :class:`~app.data.ownership.OwnershipError` for a missing or non-owned
    contract, which the app maps to a uniform 404 so other tenants' ids are
    never revealed (Requirement 2.2). This is the non-WebSocket path that lets a
    client read the same ``status``/``progress_pct`` it would otherwise stream
    (Requirement 4.5).
    """

    contract = await require_owned(session, Contract, contract_id, current_user.id)
    return ContractStatusResponse(
        contract_id=contract.id,
        status=contract.status,
        progress_pct=contract.progress_pct,
        stage=None,
    )


# ---------------------------------------------------------------------------
# Redis subscription seam (lazy; overridable in tests)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _redis_progress_subscriber(
    redis_url: str, channel: str
) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
    """Yield an async iterator of decoded progress messages for ``channel``.

    Connects to Redis **lazily** with ``redis.asyncio`` (imported here so module
    import never needs a broker), subscribes to ``channel``, and yields an async
    generator that decodes each JSON pub/sub payload into a dict. The Redis
    client and subscription are always torn down on exit.
    """

    import redis.asyncio as aioredis  # local import keeps module import broker-free

    client = aioredis.from_url(redis_url)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)

    async def _messages() -> AsyncIterator[dict[str, Any]]:
        try:
            async for raw in pubsub.listen():
                if not raw or raw.get("type") != "message":
                    continue
                data = raw.get("data")
                if isinstance(data, (bytes, bytearray)):
                    data = data.decode("utf-8")
                try:
                    yield json.loads(data)
                except (TypeError, ValueError):
                    logger.warning("dropping non-JSON progress message on %s", channel)
                    continue
        except asyncio.CancelledError:
            # Normal teardown when the pump is cancelled (client disconnected or
            # a terminal status was reached on the other branch). Re-raise so the
            # cancellation propagates cleanly.
            raise
        except Exception:
            # A broker read timeout / connection drop (incl. redis-py converting a
            # cancellation into a TimeoutError) ends the live stream quietly. The
            # client transparently falls back to status polling, so this is not an
            # error worth a stack trace.
            logger.debug(
                "progress subscription for %s ended early (broker read)", channel,
                exc_info=True,
            )
            return

    try:
        yield _messages()
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        finally:
            await client.aclose()


# Indirection seam: tests (task 9.4) override this to feed synthetic messages
# without a live Redis. Signature: (redis_url, channel) -> async context manager
# yielding an async iterator of decoded message dicts.
_subscriber_factory = _redis_progress_subscriber


# ---------------------------------------------------------------------------
# WebSocket endpoint (Requirements 4.4, 4.8)
# ---------------------------------------------------------------------------


async def _authenticate_ws(
    token: Optional[str], settings: Settings
) -> Optional[int]:
    """Return the user id encoded in ``token`` or ``None`` if it is unusable.

    A missing, malformed, expired, mis-signed, or non-access token yields
    ``None`` so the caller can deny the connection uniformly (Requirement 4.8).
    """

    if not token:
        return None
    try:
        payload = decode_access_token(token, settings=settings)
    except TokenError:
        return None
    return payload.user_id


@router.websocket("/ws/contracts/{contract_id}")
async def contract_progress_ws(
    websocket: WebSocket,
    contract_id: int,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    token: Annotated[Optional[str], Query()] = None,
) -> None:
    """Stream a Contract's live progress to an authenticated owner.

    Order of operations (Requirements 4.4, 4.8):

    1. **Authenticate** from the ``?token=`` JWT (a short-lived access token,
       typically minted by ``POST /auth/ws-ticket`` to avoid leaking the
       long-lived access token in the URL). No/invalid token closes with
       ``4401`` before accept.
    2. **Enforce ownership** of ``contract_id`` using a short-lived DB session
       that is released immediately after the check (so a long-lived stream
       never holds a pooled connection). Missing/non-owned closes with ``4403``.
    3. **Accept**, send a current-state snapshot, and — unless the contract is
       already terminal — subscribe to the Redis channel and forward each
       ``{ stage, progress_pct, status }`` message until the client disconnects
       or a terminal (COMPLETE/FAILED) status is observed.
    """

    # 1. Authenticate the handshake from the query-param token.
    user_id = await _authenticate_ws(token, settings)
    if user_id is None:
        await websocket.close(code=WS_CLOSE_UNAUTHENTICATED)
        return

    # 2. Enforce ownership using a SHORT-LIVED session, then release it so the
    # stream below never ties up a pooled DB connection for its whole lifetime.
    factory = get_sessionmaker()
    async with factory() as session:
        contract = await get_owned_or_none(session, Contract, contract_id, user_id)
        if contract is None:
            await websocket.close(code=WS_CLOSE_FORBIDDEN)
            return
        # Capture a current-state snapshot before the session closes.
        snapshot = {
            "stage": None,
            "progress_pct": contract.progress_pct,
            "status": contract.status.value,
        }

    # 3. Authorized: accept and send the snapshot first. Redis pub/sub has no
    # replay, so without this a client connecting to an already-finished (or
    # mid-gap) contract would receive nothing.
    await websocket.accept()
    try:
        await websocket.send_json(snapshot)
    except Exception:  # pragma: no cover - client vanished immediately
        await _safe_close(websocket)
        return

    # Already terminal? There is nothing more to stream.
    if snapshot["status"] in _TERMINAL_STATUSES:
        await _safe_close(websocket)
        return

    channel = contract_progress_channel(contract_id)
    try:
        async with _subscriber_factory(settings.redis_url, channel) as messages:
            await _forward_until_done(websocket, messages)
    except WebSocketDisconnect:
        # Client went away mid-stream; nothing more to do.
        return
    except Exception:  # pragma: no cover - defensive: never crash on broker error
        # A broker read timeout / connection drop is expected when nothing is
        # publishing yet (e.g. no worker running) or the client disconnects; the
        # client falls back to status polling, so log quietly without a trace.
        logger.debug("progress stream for contract %s ended early", contract_id, exc_info=True)
    finally:
        await _safe_close(websocket)


async def _forward_until_done(
    websocket: WebSocket, messages: AsyncIterator[dict[str, Any]]
) -> None:
    """Forward progress messages to ``websocket`` until done or disconnected.

    Runs the message pump and a disconnect watcher concurrently: whichever
    finishes first (a terminal status / exhausted stream, or the client
    disconnecting) cancels the other so the handler returns promptly.
    """

    forward_task = asyncio.ensure_future(_pump(websocket, messages))
    watch_task = asyncio.ensure_future(_watch_disconnect(websocket))
    done, pending = await asyncio.wait(
        {forward_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception:
            # We are tearing down the losing task (e.g. the Redis read pump after
            # the client disconnected). redis-py can surface a cancelled read as a
            # TimeoutError; swallow it — it is just teardown noise.
            pass
    # Surface a forwarding error (other than disconnect) if one occurred.
    for task in done:
        exc = task.exception()
        if exc is not None and not isinstance(exc, WebSocketDisconnect):
            raise exc


async def _pump(
    websocket: WebSocket, messages: AsyncIterator[dict[str, Any]]
) -> None:
    """Send each progress message to the client; stop on a terminal status."""

    async for message in messages:
        await websocket.send_json(message)
        if message.get("status") in _TERMINAL_STATUSES:
            return


async def _watch_disconnect(websocket: WebSocket) -> None:
    """Resolve when the client disconnects (so the forwarder can be cancelled)."""

    try:
        while True:
            event = await websocket.receive()
            if event.get("type") == "websocket.disconnect":
                return
    except WebSocketDisconnect:
        return


async def _safe_close(websocket: WebSocket) -> None:
    """Close the socket if still open, swallowing any already-closed error."""

    try:
        await websocket.close()
    except RuntimeError:
        # Already closed / closing -- safe to ignore.
        pass


__all__ = [
    "router",
    "get_contract_status",
    "contract_progress_ws",
    "WS_CLOSE_UNAUTHENTICATED",
    "WS_CLOSE_FORBIDDEN",
]
