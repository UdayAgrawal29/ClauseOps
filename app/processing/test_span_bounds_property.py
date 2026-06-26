"""Property test for span-offset bounds (spec task 8.6).

**Property 2: Span-offset bounds** — Validates Requirements 5.3.

Every Task span satisfies ``0 <= start <= end <= len(source_text)``. No offset
can index outside the source text or invert. ``None`` pairs are valid "absent"
markers (Requirement 5.2 only constrains spans that are *present*), so they are
simply skipped.

This is exercised at two layers:

* the pure computation :func:`app.processing.spans.compute_span_offsets`, across
  arbitrary source text and arbitrary party/action/deadline spans (a mix of real
  substrings, empty strings, ``None``, and unrelated random text); and
* the *persisted* ``tasks`` rows, by driving the real
  :func:`app.processing.ml._compute_span_offsets_seam` +
  :func:`app.processing.ml._apersist_result_in_session` against an in-memory
  async SQLite database and asserting the bounds hold on every non-null
  ``agent_*`` / ``action_*`` pair that was written.

The companion grounding round-trip property (Property 1, task 8.5) lives
separately; here we assert *only* the bounds invariant.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date as _date
from typing import Optional

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.processing import ml
from app.processing.spans import compute_span_offsets


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Source text: arbitrary unicode, including the empty string.
_source_text = st.text(max_size=80)


@st.composite
def _span_for(draw, source_text: str) -> Optional[str]:
    """Draw a span string with a realistic mix of shapes.

    The choices deliberately span the input space the computation must handle:

    * ``None`` and the empty string (absent markers),
    * a genuine substring of ``source_text`` (present span -> must be in bounds),
    * arbitrary unrelated text (usually *not* a substring -> absent marker).
    """

    kind = draw(st.sampled_from(["none", "empty", "substring", "random"]))
    if kind == "none":
        return None
    if kind == "empty":
        return ""
    if kind == "substring" and source_text:
        start = draw(st.integers(min_value=0, max_value=len(source_text) - 1))
        end = draw(st.integers(min_value=start + 1, max_value=len(source_text)))
        return source_text[start:end]
    # "random" (or "substring" against empty source) -> arbitrary text.
    return draw(st.text(max_size=20))


@st.composite
def _text_and_spans(draw):
    source_text = draw(_source_text)
    party = draw(_span_for(source_text))
    action = draw(_span_for(source_text))
    deadline = draw(_span_for(source_text))
    return source_text, party, action, deadline


# ---------------------------------------------------------------------------
# Property 2 — pure computation
# ---------------------------------------------------------------------------


@settings(max_examples=300)
@given(_text_and_spans())
def test_compute_span_offsets_respects_bounds(data):
    """Every non-None offset pair stays within ``[0, len(source_text)]``.

    **Validates: Requirements 5.3**
    """

    source_text, party, action, deadline = data
    result = compute_span_offsets(source_text, party, action, deadline)
    n = len(source_text)

    for start, end in (
        (result.agent_start, result.agent_end),
        (result.action_start, result.action_end),
        (result.deadline_start, result.deadline_end),
    ):
        # None pairs are valid "absent" markers — skip them (Req 5.2).
        if start is None or end is None:
            # An offset pair is either fully present or fully absent.
            assert start is None and end is None
            continue
        assert 0 <= start <= end <= n


# ---------------------------------------------------------------------------
# Property 2 — persisted Task rows
# ---------------------------------------------------------------------------


@dataclass
class _TaskRec:
    """Stand-in for clauseops ``TaskRecord`` (only the fields persistence reads)."""

    task_id: str
    clause_id: str
    title: str
    obligated_party: Optional[str]
    obligation_type: str
    date_type: str
    due_date: Optional[_date]
    requires_review: bool
    source_text: str
    action: Optional[str] = None
    reminder_dates: list = None  # type: ignore[assignment]
    confidence: float = 0.0
    agent_score: float = 0.0

    def __post_init__(self):
        if self.reminder_dates is None:
            self.reminder_dates = []


async def _persist_and_load_offsets(source_text, party, action):
    """Persist one generated task and return its (agent/action) offset pairs.

    Builds a ``PipelineResult`` with a single stand-in ``TaskRecord`` (deadline
    omitted per the task instructions), runs the real 8.3 span seam and the 8.4
    persistence against a fresh in-memory async SQLite DB, then reads the
    written ``tasks`` row back.
    """

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.data.database import Base
    from app.data.enums import ContractStatus
    from app.data.models import Contract, Task, User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as session:
            user = User(email="owner@example.com", password_hash="h")
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

        task = _TaskRec(
            task_id="t1",
            clause_id="c1",
            title="Generated task",
            obligated_party=party,
            obligation_type="OBLIGATION",
            date_type="ABSOLUTE",
            due_date=None,
            requires_review=False,
            source_text=source_text,
            action=action,
        )
        result = ml.PipelineResult(
            contract_id=contract_id,
            contract_name="deal.pdf",
            clauses_data=[
                {
                    "clause_id": "c1",
                    "heading": "",
                    "body_text": source_text,
                    "clause_type": "PAYMENT",
                    "confidence": 0.5,
                }
            ],
            tasks=[task],
        )
        ml._compute_span_offsets_seam(result)

        async with factory() as session:
            await ml._apersist_result_in_session(session, result)

        async with factory() as session:
            row = (
                await session.execute(
                    select(Task).where(Task.contract_id == contract_id)
                )
            ).scalars().one()
            return (
                (row.agent_start, row.agent_end),
                (row.action_start, row.action_end),
                len(row.source_text),
            )
    finally:
        await engine.dispose()


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_text_and_spans())
def test_persisted_task_offsets_respect_bounds(data):
    """Persisted ``agent_*`` / ``action_*`` offsets stay within bounds.

    **Validates: Requirements 5.3**
    """

    source_text, party, action, _deadline = data  # deadline omitted for DB variant

    agent, act, n = asyncio.run(
        _persist_and_load_offsets(source_text, party, action)
    )

    for start, end in (agent, act):
        if start is None or end is None:
            assert start is None and end is None
            continue
        assert 0 <= start <= end <= n
