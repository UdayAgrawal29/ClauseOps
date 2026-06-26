"""Property-based test for the grounding round-trip (spec task 8.5).

**Property 1: Grounding round-trip** — Validates Requirements 5.2.

The signature ClauseOps guarantee is that whenever a party/action span is
*present* on a persisted Task, the stored character offsets index back into
``source_text`` to *exactly* that span::

    source_text[agent_start:agent_end]  == obligated_party   (party present)
    source_text[action_start:action_end] == action           (action present)

and that when a span is **absent** (empty/``None``) or is **not** a verbatim
substring of ``source_text``, no offset is fabricated (the offset pair is
``None``), so the viewer never makes a false grounding claim.

This module exercises the property at two levels:

* **Offset level** — directly against :func:`app.processing.spans.compute_span_offsets`,
  the pure function that the persistence path delegates to.
* **End-to-end** — through :func:`app.processing.ml._compute_span_offsets_seam`
  + :func:`app.processing.ml._apersist_result_in_session` against an in-memory
  async SQLite database, then reloading the persisted ``Task`` rows and
  re-checking the round-trip on the values that actually landed in the DB.

The generators deliberately mix three regimes so the property is meaningful:

1. the span is a random verbatim substring of ``source_text`` (round-trip must
   hold), 2. the span is absent (``None``/empty → offsets ``None``), and 3. the
   span is some arbitrary string that is usually *not* a substring (→ offsets
   ``None`` unless it happens to occur, in which case the round-trip still holds).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.processing import ml
from app.processing.spans import compute_span_offsets

# Reuse the clauseops stand-ins already proven out in the persistence tests so
# the end-to-end variant mirrors the real TaskRecord/ObligationRecord shapes.
from app.processing.test_ml import (
    _DeadlineRec,
    _OblRec,
    _TaskRec,
    _new_sqlite_engine,
    _setup_user_and_contract,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Keep the alphabet small so random substrings actually recur and the
# "not-a-substring" arm sometimes lands inside the text — both make the property
# stronger rather than weaker.
_TEXT = st.text(alphabet="ab cde", min_size=0, max_size=40)


@st.composite
def _text_and_span(draw: st.DrawFn) -> tuple[str, Optional[str]]:
    """Generate ``(source_text, span)`` across the three regimes above."""

    source_text = draw(_TEXT)
    regime = draw(st.integers(min_value=0, max_value=2))

    if regime == 0 and source_text:
        # A verbatim substring of source_text (the round-trip-must-hold case).
        start = draw(st.integers(min_value=0, max_value=len(source_text)))
        end = draw(st.integers(min_value=start, max_value=len(source_text)))
        span: Optional[str] = source_text[start:end]
    elif regime == 1:
        # Absent span -> offsets must be None.
        span = draw(st.sampled_from([None, ""]))
    else:
        # Arbitrary string: usually not a substring, occasionally is.
        span = draw(_TEXT)

    return source_text, span


# ---------------------------------------------------------------------------
# Offset-level property (pure compute_span_offsets)
# ---------------------------------------------------------------------------


@settings(max_examples=300)
@given(
    data=st.tuples(_text_and_span(), _text_and_span()),
)
def test_compute_span_offsets_round_trips_or_declines(data):
    """compute_span_offsets either round-trips a present span or yields None.

    **Validates: Requirements 5.2**
    """

    (source_text, party), (_, action) = data
    # Compute against a single source_text (the party text), mirroring how a
    # task's party and action are both grounded in the SAME source_text.
    offsets = compute_span_offsets(source_text, party, action, deadline_raw=None)

    # Party: present-and-found -> exact round-trip; otherwise no offset.
    if offsets.agent_start is not None:
        assert offsets.agent_end is not None
        assert source_text[offsets.agent_start : offsets.agent_end] == party
    else:
        # No fabricated grounding: absent, empty, or not a substring.
        assert not party or party not in source_text

    # Action grounds in the same source_text.
    if offsets.action_start is not None:
        assert offsets.action_end is not None
        assert source_text[offsets.action_start : offsets.action_end] == action
    else:
        assert not action or action not in source_text


# ---------------------------------------------------------------------------
# End-to-end property (seam + persistence + reload)
# ---------------------------------------------------------------------------


@st.composite
def _persistable_tasks(draw: st.DrawFn) -> list[tuple[str, Optional[str], Optional[str]]]:
    """Generate a small batch of ``(source_text, party, action)`` triples."""

    count = draw(st.integers(min_value=1, max_value=4))
    triples: list[tuple[str, Optional[str], Optional[str]]] = []
    for _ in range(count):
        source_text, party = draw(_text_and_span())
        # Action is grounded in the SAME source_text as the party.
        regime = draw(st.integers(min_value=0, max_value=2))
        if regime == 0 and source_text:
            start = draw(st.integers(min_value=0, max_value=len(source_text)))
            end = draw(st.integers(min_value=start, max_value=len(source_text)))
            action: Optional[str] = source_text[start:end]
        elif regime == 1:
            action = draw(st.sampled_from([None, ""]))
        else:
            action = draw(_TEXT)
        triples.append((source_text, party, action))
    return triples


async def _persist_and_reload(triples: list[tuple[str, Optional[str], Optional[str]]]):
    """Build a PipelineResult from triples, persist it, return the Task rows."""

    from sqlalchemy import select

    from app.data.models import Task

    engine = _new_sqlite_engine()
    try:
        factory, contract_id = await _setup_user_and_contract(engine)

        clauses_data = []
        obligations = []
        deadlines = []
        tasks = []
        for index, (source_text, party, action) in enumerate(triples):
            clause_id = f"c{index}"
            clauses_data.append(
                {
                    "clause_id": clause_id,
                    "heading": "",
                    "body_text": source_text,
                    "clause_type": "OBLIGATION",
                    "confidence": 0.5,
                }
            )
            # The seam recovers the action from the originating obligation; supply
            # one carrying the generated action string so the persisted action is
            # exactly what offsets were computed against.
            obligations.append(
                [_OblRec(clause_id=clause_id, obligation_type="OBLIGATION", action=action or "")]
            )
            deadlines.append([])
            tasks.append(
                _TaskRec(
                    task_id=f"t{index}",
                    clause_id=clause_id,
                    title=f"Task {index}",
                    obligated_party=party or "",
                    obligation_type="OBLIGATION",
                    date_type="ABSOLUTE",
                    due_date=None,
                    requires_review=False,
                    source_text=source_text,
                )
            )

        result = ml.PipelineResult(
            contract_id=contract_id,
            contract_name="deal.pdf",
            obligations=obligations,
            deadlines=deadlines,
            tasks=tasks,
            clauses_data=clauses_data,
        )
        ml._compute_span_offsets_seam(result)

        async with factory() as session:
            await ml._apersist_result_in_session(session, result)

        async with factory() as session:
            rows = (
                await session.execute(
                    select(Task).where(Task.contract_id == contract_id)
                )
            ).scalars().all()
            # Detach plain values so we can assert after the session closes.
            return [
                (
                    r.source_text,
                    r.obligated_party,
                    r.action,
                    r.agent_start,
                    r.agent_end,
                    r.action_start,
                    r.action_end,
                )
                for r in rows
            ]
    finally:
        await engine.dispose()


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(triples=_persistable_tasks())
def test_persisted_task_spans_round_trip(triples):
    """Every persisted Task with non-null offsets round-trips to its span.

    **Validates: Requirements 5.2**
    """

    rows = asyncio.run(_persist_and_reload(triples))

    assert len(rows) == len(triples)
    for source_text, party, action, a_start, a_end, act_start, act_end in rows:
        if a_start is not None:
            assert a_end is not None
            assert source_text[a_start:a_end] == party
        if act_start is not None:
            assert act_end is not None
            assert source_text[act_start:act_end] == action
