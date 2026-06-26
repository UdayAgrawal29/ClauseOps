"""Property-based test for progress bounds (spec task 9.3).

**Property 7: Progress bounds** — Validates Requirement 4.2:
Every published/stored ``progress_pct`` is an integer in [0, 100].

The progress hook (:func:`app.processing.progress.make_progress_hook`) publishes
``{stage, progress_pct, status}`` to a Contract's Redis channel and updates
``contracts.progress_pct``. Whatever numeric value an upstream stage supplies —
including out-of-range integers, fractional floats, or junk — the *published*
and *persisted* ``progress_pct`` must always be an ``int`` in [0, 100].

These tests drive the hook with a fake/captured Redis client (no broker needed)
and assert the invariant across a wide input space via Hypothesis. The persisted
half is exercised through an in-memory async SQLite session, which also runs the
Contract model's ``@validates``/CheckConstraint guard, so a clamped value that
the validator would reject would surface as a failure here.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.data.enums import ContractStatus
from app.processing import progress as progress_mod
from app.processing.ml import STAGE_PROGRESS


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Records published (channel, payload) pairs; ``publish`` never connects."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1


# Arbitrary "progress" inputs an upstream stage might (wrongly) supply:
# out-of-range integers, fractional/extreme floats, and non-numeric junk.
_progress_values = st.one_of(
    st.integers(min_value=-1000, max_value=1000),
    st.floats(
        allow_nan=False,
        allow_infinity=False,
        min_value=-1000.0,
        max_value=1000.0,
    ),
    st.none(),
    st.text(max_size=8),
)

# Stage names: the canonical pipeline stages plus arbitrary labels. The synthetic
# "complete" stage is excluded here because it is forced to 100 by contract and
# is covered by its own assertion below.
_stage_names = st.one_of(
    st.sampled_from(list(STAGE_PROGRESS.keys())),
    st.text(max_size=12).filter(lambda s: s != progress_mod.COMPLETE_STAGE),
)


# ---------------------------------------------------------------------------
# Property 7 — published progress_pct is always an int in [0, 100]
# ---------------------------------------------------------------------------


@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(stage=_stage_names, raw=_progress_values)
def test_published_progress_pct_is_int_in_bounds(monkeypatch, stage, raw):
    """**Validates: Requirements 4.2**

    For any stage name and any numeric/junk input, the published message and the
    value handed to the DB update are an ``int`` in [0, 100].
    """

    fake = _FakeRedis()
    captured: list[tuple[int, int, ContractStatus]] = []
    monkeypatch.setattr(progress_mod, "_get_redis", lambda: fake)
    monkeypatch.setattr(
        progress_mod,
        "_update_contract_progress",
        lambda cid, pct, status: captured.append((cid, pct, status)),
    )

    hook = progress_mod.make_progress_hook(42)
    hook(stage, raw)

    # Exactly one published message, on the contract's channel.
    assert len(fake.published) == 1
    channel, payload = fake.published[0]
    assert channel == "contract:progress:42"

    msg = json.loads(payload)
    pct = msg["progress_pct"]
    assert isinstance(pct, int)
    assert 0 <= pct <= 100

    # The DB update receives the same clamped integer.
    assert captured == [(42, pct, captured[0][2])]
    assert isinstance(captured[0][1], int)
    assert 0 <= captured[0][1] <= 100


@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(raw=_progress_values)
def test_complete_stage_is_always_100(monkeypatch, raw):
    """**Validates: Requirements 4.2**

    The synthetic 'complete' stage publishes ``progress_pct == 100`` (an int in
    [0, 100]) regardless of the numeric value supplied.
    """

    fake = _FakeRedis()
    monkeypatch.setattr(progress_mod, "_get_redis", lambda: fake)
    monkeypatch.setattr(
        progress_mod, "_update_contract_progress", lambda cid, pct, status: None
    )

    hook = progress_mod.make_progress_hook(1)
    hook(progress_mod.COMPLETE_STAGE, raw)

    msg = json.loads(fake.published[0][1])
    assert msg["progress_pct"] == 100
    assert isinstance(msg["progress_pct"], int)


@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(raw=_progress_values)
def test_publish_failed_progress_pct_is_int_in_bounds(monkeypatch, raw):
    """**Validates: Requirements 4.2**

    The FAILED broadcast's ``progress_pct`` is also an int in [0, 100].
    """

    fake = _FakeRedis()
    monkeypatch.setattr(progress_mod, "_get_redis", lambda: fake)

    progress_mod.publish_failed(7, progress_pct=raw)

    msg = json.loads(fake.published[0][1])
    assert isinstance(msg["progress_pct"], int)
    assert 0 <= msg["progress_pct"] <= 100
    assert msg["status"] == "FAILED"


# ---------------------------------------------------------------------------
# Property 7 — the persisted progress_pct is an int in [0, 100]
# ---------------------------------------------------------------------------


@settings(
    max_examples=75,
    deadline=None,  # DB engine setup/teardown per example; no fixed deadline.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(raw=_progress_values)
def test_persisted_progress_pct_is_int_in_bounds(raw):
    """**Validates: Requirements 4.2**

    Feeding an arbitrary value through the hook's clamp and persisting it via the
    real async DB path stores an ``int`` in [0, 100]. The Contract model's
    validator/CheckConstraint would reject anything outside the domain, so a
    successful write across all inputs confirms the clamp keeps values valid.
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.data.database import Base
    from app.data.models import Contract, User

    clamped = progress_mod._clamp_pct(raw)
    # The clamp itself must already satisfy the invariant.
    assert isinstance(clamped, int)
    assert 0 <= clamped <= 100

    async def scenario() -> None:
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

            # Persist the clamped value via the production DB seam.
            async with factory() as session:
                await progress_mod._aupdate_progress_in_session(
                    session, contract_id, clamped, ContractStatus.PROCESSING
                )

            async with factory() as session:
                refreshed = await session.get(Contract, contract_id)
                assert isinstance(refreshed.progress_pct, int)
                assert 0 <= refreshed.progress_pct <= 100
                assert refreshed.progress_pct == clamped
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Canonical stage progress table is itself within bounds
# ---------------------------------------------------------------------------


def test_stage_progress_table_values_are_ints_in_bounds():
    """**Validates: Requirements 4.2**

    Every canonical ``STAGE_PROGRESS`` value is an int in [0, 100].
    """

    assert STAGE_PROGRESS, "STAGE_PROGRESS must not be empty"
    for stage, pct in STAGE_PROGRESS.items():
        assert isinstance(pct, int), f"{stage} progress is not an int"
        assert 0 <= pct <= 100, f"{stage} progress {pct} out of [0, 100]"
