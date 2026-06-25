"""Per-stage progress publishing for the ML worker (spec task 9.1).

This module supplies the *real* progress hook that the ``ml`` worker threads
through :func:`app.processing.ml.run_pipeline`. On every pipeline stage it:

* publishes a JSON message ``{stage, progress_pct, status}`` to the Contract's
  Redis pub/sub channel (consumed by the WebSocket endpoint in task 9.2), and
* updates the Contract's ``progress_pct`` (and ``status`` where appropriate) in
  the database.

It implements Requirement 4.2 (publish stage + integer ``progress_pct`` in
[0, 100] and update the Contract) and the publish half of Requirement 4.7
(broadcast the FAILED transition). See design.md "Component 5: Progress Channel".

Design choices:

* **Import safety / no network at import time.** Importing this module must NOT
  require a running Redis. The ``redis`` client is imported and connected
  *lazily*, on the first publish, and cached for reuse. The channel-name helper
  and the hook factory perform no I/O.
* **Best-effort publishing.** A Redis failure must never crash the pipeline: the
  publish is wrapped and logged, and the database progress update is still
  attempted afterwards. The database update is likewise defensive so a transient
  progress write can never abort an otherwise-successful run (the authoritative
  PROCESSING/COMPLETE/FAILED transitions are owned by
  :mod:`app.processing.ml`).
* **DB-in-Celery convention.** Celery's prefork worker runs tasks synchronously,
  so each DB touch is wrapped in :func:`asyncio.run` over a short-lived async
  engine (mirroring ``_aload_and_mark_processing`` / ``_amark_failed`` in
  :mod:`app.processing.ml`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.data.enums import ContractStatus
from app.data.models import Contract

logger = logging.getLogger(__name__)

# A progress hook receives (stage_name, progress_pct). Mirrors
# ``app.processing.ml.ProgressHook`` without importing it (avoids an import
# cycle, since ``ml`` imports this module).
ProgressHook = Callable[[str, int], None]

# The synthetic stage name the persistence seam emits for the final COMPLETE
# transition (``progress("complete", 100)`` in ``ml._persist_result_seam``).
COMPLETE_STAGE = "complete"

# Default stage name used when broadcasting a failure on the channel.
FAILED_STAGE = "failed"


# ---------------------------------------------------------------------------
# Channel naming (shared with the WebSocket endpoint in task 9.2)
# ---------------------------------------------------------------------------


def contract_progress_channel(contract_id: int) -> str:
    """Return the Redis pub/sub channel name for a Contract's progress stream.

    The convention ``contract:progress:{contract_id}`` is the single source of
    truth shared by the publisher (this module) and the WebSocket subscriber
    (task 9.2), so both ends always agree on the channel.
    """

    return f"contract:progress:{contract_id}"


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------


def _clamp_pct(value: Any) -> int:
    """Coerce ``value`` to an integer ``progress_pct`` in [0, 100].

    Non-numeric input falls back to 0; fractional input is rounded. Non-finite
    floats are coerced to a bound (``+inf`` -> 100, ``-inf`` -> 0) and ``NaN``
    falls back to 0. This keeps the published ``progress_pct`` (and the persisted
    column) within the domain the Contract validator enforces, so a slightly off
    (or outright junk) upstream value never aborts a run (Requirement 4.2).
    """

    try:
        as_float = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if math.isnan(as_float):
        return 0
    if math.isinf(as_float):
        return 100 if as_float > 0 else 0
    try:
        pct = int(round(as_float))
    except (ValueError, OverflowError):
        return 0
    return max(0, min(100, pct))


# ---------------------------------------------------------------------------
# Redis client (created lazily, cached, never connected at import time)
# ---------------------------------------------------------------------------

_redis_client: Optional[Any] = None


def _get_redis() -> Any:
    """Return a cached synchronous Redis client, connecting lazily.

    The ``redis`` package is imported here (not at module import) and the client
    is built from ``settings.redis_url`` only on first use, so importing this
    module never requires a running Redis.
    """

    global _redis_client
    if _redis_client is None:
        import redis  # local import keeps module import free of broker deps

        settings = get_settings()
        _redis_client = redis.from_url(settings.redis_url)
    return _redis_client


def _publish(channel: str, message: dict) -> None:
    """Best-effort publish ``message`` (as JSON) to ``channel``.

    Any failure (no broker, connection error, serialization issue) is logged and
    swallowed so progress reporting can never crash the pipeline (Req 4.2/4.7).
    """

    try:
        payload = json.dumps(message)
        _get_redis().publish(channel, payload)
    except Exception:  # pragma: no cover - defensive, exercised via fake client
        logger.warning("failed to publish progress to %s", channel, exc_info=True)


# ---------------------------------------------------------------------------
# Database progress update (async, run via asyncio.run in the sync Celery task)
# ---------------------------------------------------------------------------


async def _aupdate_progress_in_session(
    session: AsyncSession,
    contract_id: int,
    progress_pct: int,
    status: Optional[ContractStatus],
) -> None:
    """Update ``progress_pct`` (and ``status`` when given) within ``session``.

    A missing contract is a no-op (logged): progress is advisory and must not
    raise. Kept separate from engine management so it can be exercised against
    any async session.
    """

    contract = await session.get(Contract, contract_id)
    if contract is None:
        logger.warning("progress update skipped: contract %s not found", contract_id)
        return
    contract.progress_pct = progress_pct
    if status is not None:
        contract.status = status
    await session.commit()


async def _aupdate_progress(
    contract_id: int, progress_pct: int, status: Optional[ContractStatus]
) -> None:
    """Update progress via a short-lived async engine (DB-in-Celery convention)."""

    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await _aupdate_progress_in_session(
                session, contract_id, progress_pct, status
            )
    finally:
        await engine.dispose()


def _update_contract_progress(
    contract_id: int, progress_pct: int, status: Optional[ContractStatus]
) -> None:
    """Best-effort synchronous progress write: set ``progress_pct``/``status``.

    Wrapped so a transient DB problem during a progress update never aborts an
    otherwise-successful pipeline run; the authoritative status transitions are
    owned by :mod:`app.processing.ml`.
    """

    try:
        asyncio.run(_aupdate_progress(contract_id, progress_pct, status))
    except Exception:  # pragma: no cover - defensive logging only
        logger.warning(
            "failed to update progress for contract %s", contract_id, exc_info=True
        )


# ---------------------------------------------------------------------------
# Progress hook factory (the real hook the ML worker threads into the pipeline)
# ---------------------------------------------------------------------------


def make_progress_hook(
    contract_id: int, *, status: ContractStatus = ContractStatus.PROCESSING
) -> ProgressHook:
    """Build a :data:`ProgressHook` bound to ``contract_id``.

    The returned hook is called once per pipeline stage as ``hook(stage, pct)``.
    On each call it publishes ``{stage, progress_pct, status}`` to the Contract's
    Redis channel and updates ``contracts.progress_pct`` (and ``status``):

    * the synthetic stage ``"complete"`` is reported as ``COMPLETE`` at 100;
    * every other stage is reported with ``status`` (``PROCESSING`` by default).

    ``progress_pct`` is always an integer clamped to [0, 100] (Requirement 4.2).
    Publishing is best-effort; the DB update is attempted regardless of whether
    the publish succeeds.
    """

    channel = contract_progress_channel(contract_id)

    def hook(stage: str, progress_pct: int) -> None:
        if stage == COMPLETE_STAGE:
            stage_status = ContractStatus.COMPLETE
            pct = 100
        else:
            stage_status = status
            pct = _clamp_pct(progress_pct)

        message = {
            "stage": stage,
            "progress_pct": pct,
            "status": stage_status.value,
        }
        # Best-effort publish first, then always attempt the DB update.
        _publish(channel, message)
        _update_contract_progress(contract_id, pct, stage_status)

    return hook


def publish_failed(
    contract_id: int, *, stage: str = FAILED_STAGE, progress_pct: int = 0
) -> None:
    """Broadcast a ``{stage, progress_pct, status: FAILED}`` message (Req 4.7).

    Best-effort publish only: the authoritative FAILED transition (and
    ``error_message``) is persisted by ``app.processing.ml._mark_failed``; this
    helper just notifies any connected progress subscribers that the run failed.
    """

    message = {
        "stage": stage,
        "progress_pct": _clamp_pct(progress_pct),
        "status": ContractStatus.FAILED.value,
    }
    _publish(contract_progress_channel(contract_id), message)


__all__ = [
    "ProgressHook",
    "COMPLETE_STAGE",
    "FAILED_STAGE",
    "contract_progress_channel",
    "make_progress_hook",
    "publish_failed",
]
