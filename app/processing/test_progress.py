"""Unit tests for per-stage progress publishing (spec task 9.1).

These exercise :mod:`app.processing.progress` in isolation. Redis is replaced
with a tiny in-memory fake and the database write is stubbed, so no running
Redis or Postgres is required. They cover the behaviours Requirements 4.2/4.7
hinge on:

* a message is published once per stage to the Contract's channel,
* the published ``progress_pct`` is always an integer in [0, 100],
* the synthetic ``"complete"`` stage reports status COMPLETE at 100,
* the DB progress update is invoked with the clamped value and status,
* publishing is best-effort (a Redis failure does not crash, DB still runs),
* the FAILED broadcast publishes ``status: FAILED``.

Import safety (importing the module must not require a running Redis) is also
asserted. The progress-bounds *property* test is task 9.3; the WebSocket
endpoint is task 9.2.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.data.enums import ContractStatus
from app.processing import progress as progress_mod


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Records published (channel, payload) pairs; ``publish`` never connects."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(progress_mod, "_get_redis", lambda: client)
    return client


@pytest.fixture
def captured_db(monkeypatch):
    """Stub the DB progress write and capture its calls."""

    calls: list[tuple[int, int, ContractStatus]] = []
    monkeypatch.setattr(
        progress_mod,
        "_update_contract_progress",
        lambda cid, pct, status: calls.append((cid, pct, status)),
    )
    return calls


# ---------------------------------------------------------------------------
# Channel naming
# ---------------------------------------------------------------------------


def test_contract_progress_channel_convention():
    assert progress_mod.contract_progress_channel(42) == "contract:progress:42"


# ---------------------------------------------------------------------------
# Import safety — no Redis connection at import or hook-construction time
# ---------------------------------------------------------------------------


def test_constructing_hook_does_not_connect_to_redis(monkeypatch):
    """Building a hook performs no I/O; Redis is only touched on publish."""

    monkeypatch.setattr(
        progress_mod,
        "_get_redis",
        lambda: (_ for _ in ()).throw(AssertionError("must not connect")),
    )
    # No publish happens here, so _get_redis must not be called.
    hook = progress_mod.make_progress_hook(7)
    assert callable(hook)


# ---------------------------------------------------------------------------
# Per-stage publishing + DB update
# ---------------------------------------------------------------------------


def test_hook_publishes_message_per_stage(fake_redis, captured_db):
    """Each stage call publishes one PROCESSING message to the right channel."""

    from app.processing.ml import STAGE_PROGRESS

    hook = progress_mod.make_progress_hook(11)
    for stage, pct in STAGE_PROGRESS.items():
        hook(stage, pct)

    # One published message per stage, all on the contract's channel.
    assert len(fake_redis.published) == len(STAGE_PROGRESS)
    for channel, payload in fake_redis.published:
        assert channel == "contract:progress:11"
        msg = json.loads(payload)
        assert msg["status"] == "PROCESSING"
        # progress_pct is always an int in [0, 100].
        assert isinstance(msg["progress_pct"], int)
        assert 0 <= msg["progress_pct"] <= 100
        assert msg["stage"] in STAGE_PROGRESS

    # The DB progress update is invoked once per stage with the same values.
    assert len(captured_db) == len(STAGE_PROGRESS)
    assert [c for (_cid, _pct, _st) in captured_db for c in [_cid]] == [11] * len(
        STAGE_PROGRESS
    )


def test_hook_message_shape_and_values(fake_redis, captured_db):
    hook = progress_mod.make_progress_hook(5)
    hook("segment", 25)

    channel, payload = fake_redis.published[0]
    assert channel == "contract:progress:5"
    assert json.loads(payload) == {
        "stage": "segment",
        "progress_pct": 25,
        "status": "PROCESSING",
    }
    # DB update mirrors the published value + PROCESSING status.
    assert captured_db == [(5, 25, ContractStatus.PROCESSING)]


@pytest.mark.parametrize(
    "raw, expected",
    [
        (-3, 0),       # below range -> clamped to 0
        (150, 100),    # above range -> clamped to 100
        (5.6, 6),      # fractional -> rounded
        (4.4, 4),      # fractional -> rounded down
        ("oops", 0),   # non-numeric -> 0
        (None, 0),     # missing -> 0
    ],
)
def test_progress_pct_is_clamped_and_rounded_to_int(fake_redis, captured_db, raw, expected):
    hook = progress_mod.make_progress_hook(1)
    hook("classify", raw)

    msg = json.loads(fake_redis.published[0][1])
    assert msg["progress_pct"] == expected
    assert isinstance(msg["progress_pct"], int)
    assert captured_db[0][1] == expected


def test_complete_stage_reports_status_complete_at_100(fake_redis, captured_db):
    """The synthetic 'complete' stage is COMPLETE at 100 regardless of input."""

    hook = progress_mod.make_progress_hook(9)
    hook(progress_mod.COMPLETE_STAGE, 0)  # value ignored -> forced to 100

    msg = json.loads(fake_redis.published[0][1])
    assert msg == {"stage": "complete", "progress_pct": 100, "status": "COMPLETE"}
    assert captured_db == [(9, 100, ContractStatus.COMPLETE)]


def test_custom_default_status_is_used_for_non_complete_stages(fake_redis, captured_db):
    hook = progress_mod.make_progress_hook(3, status=ContractStatus.PENDING)
    hook("extract", 5)
    assert json.loads(fake_redis.published[0][1])["status"] == "PENDING"
    assert captured_db == [(3, 5, ContractStatus.PENDING)]


# ---------------------------------------------------------------------------
# Best-effort publishing — Redis failure must not crash, DB still runs
# ---------------------------------------------------------------------------


def test_redis_failure_does_not_crash_and_db_still_updates(monkeypatch, captured_db):
    class _BoomRedis:
        def publish(self, channel, payload):
            raise RuntimeError("redis down")

    monkeypatch.setattr(progress_mod, "_get_redis", lambda: _BoomRedis())

    hook = progress_mod.make_progress_hook(8)
    # Must not raise even though publishing fails.
    hook("ner", 60)

    # The DB progress update is still attempted with the clamped value.
    assert captured_db == [(8, 60, ContractStatus.PROCESSING)]


def test_publish_failed_broadcasts_failed_status(fake_redis):
    progress_mod.publish_failed(17)

    channel, payload = fake_redis.published[0]
    assert channel == "contract:progress:17"
    msg = json.loads(payload)
    assert msg["status"] == "FAILED"
    assert msg["stage"] == "failed"
    assert isinstance(msg["progress_pct"], int)
    assert 0 <= msg["progress_pct"] <= 100


def test_publish_failed_is_best_effort(monkeypatch):
    monkeypatch.setattr(
        progress_mod,
        "_get_redis",
        lambda: (_ for _ in ()).throw(RuntimeError("redis down")),
    )
    # Must not raise.
    progress_mod.publish_failed(1)


# ---------------------------------------------------------------------------
# DB progress write against a real (in-memory) async SQLite session
# ---------------------------------------------------------------------------


def test_aupdate_progress_in_session_writes_pct_and_status():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.data.database import Base
    from app.data.models import Contract, User

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            async with factory() as session:
                user = User(email="u@example.com", password_hash="h")
                session.add(user)
                await session.commit()
                contract = Contract(
                    user_id=user.id,
                    filename="deal.pdf",
                    file_key="contracts/u1/deal.pdf",
                    status=ContractStatus.PROCESSING,
                )
                session.add(contract)
                await session.commit()
                contract_id = contract.id

            # Update progress mid-run.
            async with factory() as session:
                await progress_mod._aupdate_progress_in_session(
                    session, contract_id, 45, ContractStatus.PROCESSING
                )

            async with factory() as session:
                refreshed = await session.get(Contract, contract_id)
                assert refreshed.progress_pct == 45
                assert refreshed.status == ContractStatus.PROCESSING

            # COMPLETE transition through the same path.
            async with factory() as session:
                await progress_mod._aupdate_progress_in_session(
                    session, contract_id, 100, ContractStatus.COMPLETE
                )

            async with factory() as session:
                refreshed = await session.get(Contract, contract_id)
                assert refreshed.progress_pct == 100
                assert refreshed.status == ContractStatus.COMPLETE
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_aupdate_progress_in_session_missing_contract_is_noop():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.data.database import Base

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                # No contract exists -> must not raise.
                await progress_mod._aupdate_progress_in_session(
                    session, 999, 50, ContractStatus.PROCESSING
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Wiring into the ML worker (process_contract)
# ---------------------------------------------------------------------------


def test_process_contract_binds_real_hook_and_publishes_failure(monkeypatch, tmp_path):
    """The worker uses the bound progress hook and broadcasts FAILED on error."""

    from app.processing import ml

    monkeypatch.setattr(ml, "_mark_processing_and_load", lambda cid: ("k", "x.pdf"))
    fake_pdf = tmp_path / "extracted.pdf"
    fake_pdf.write_bytes(b"%PDF data")
    monkeypatch.setattr(ml, "_extract_pdf_to_tempfile", lambda key: str(fake_pdf))

    def boom(*args, **kwargs):
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(ml, "run_pipeline", boom)
    monkeypatch.setattr(ml, "_mark_failed", lambda cid, msg: None)

    published_failures: list[int] = []
    monkeypatch.setattr(
        ml, "publish_failed", lambda cid, **kw: published_failures.append(cid)
    )

    with pytest.raises(RuntimeError, match="stage exploded"):
        ml.process_contract.run(123)

    # Req 4.7: the failure is broadcast on the contract's progress channel.
    assert published_failures == [123]
