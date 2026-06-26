"""Property-based test for Property 8 — "No guessed deadlines" (Requirement 8.2).

Property 8 (No guessed deadlines):
    For any Task whose relative/conditional deadline is unresolved,
    ``requires_review`` is true and ``due_date`` is null.

Concretely, the persistence path in :mod:`app.processing.ml` treats a deadline as
*unresolved* when the task's ``date_type`` is RELATIVE or CONDITIONAL
(case-insensitive) and the upstream ``due_date`` is ``None``
(``_has_unresolved_deadline``). When persisting such a task,
``_apersist_result_in_session`` MUST force ``requires_review = True`` and keep
``due_date = NULL`` — it must never fabricate a concrete calendar date.

This test generates arbitrary tasks (varying ``date_type`` across
{ABSOLUTE, RELATIVE, RECURRING, CONDITIONAL} and case variants, ``due_date`` over
{None, a concrete date}, and the upstream ``requires_review`` flag), persists them
into a fresh in-memory async SQLite database via the real persistence seam, reloads
the rows, and asserts the property holds for every task.

**Validates: Requirements 8.2**
"""

from __future__ import annotations

import asyncio
from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st

from app.processing import ml
# Reuse the existing clauseops stand-ins / DB harness from the 8.4 unit tests so
# the generated records mirror the real ``TaskRecord`` shape exactly.
from app.processing.test_ml import (
    _TaskRec,
    _new_sqlite_engine,
    _setup_user_and_contract,
)


# date_type base values the pipeline can emit. RELATIVE/CONDITIONAL are the two
# types whose missing due_date means "unresolved" (Req 8.2); ABSOLUTE/RECURRING
# are not treated as unresolved.
_DATE_TYPE_BASES = ["ABSOLUTE", "RELATIVE", "RECURRING", "CONDITIONAL"]


def _case_variants(value: str) -> list[str]:
    """Return assorted case renderings so the test exercises case-insensitivity."""

    return [value.upper(), value.lower(), value.title(), value.swapcase()]


@st.composite
def _date_type(draw: st.DrawFn) -> str:
    base = draw(st.sampled_from(_DATE_TYPE_BASES))
    return draw(st.sampled_from(_case_variants(base)))


# A concrete due date or no due date at all.
_due_dates = st.one_of(
    st.none(),
    st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)),
)


@st.composite
def _task_spec(draw: st.DrawFn) -> dict:
    return {
        "date_type": draw(_date_type()),
        "due_date": draw(_due_dates),
        "requires_review": draw(st.booleans()),
    }


def _is_unresolved(date_type: str, due_date) -> bool:
    """Mirror ml._has_unresolved_deadline for the expected-value computation."""

    return (date_type or "").upper() in ml._UNRESOLVED_DATE_TYPES and due_date is None


@settings(max_examples=50, deadline=None)
@given(specs=st.lists(_task_spec(), min_size=1, max_size=5))
def test_unresolved_deadlines_are_flagged_and_never_guessed(specs: list[dict]):
    """No persisted Task ever carries a guessed due_date for an unresolved deadline.

    **Validates: Requirements 8.2**
    """

    from sqlalchemy import select

    from app.data.models import Task

    async def scenario():
        engine = _new_sqlite_engine()
        try:
            factory, contract_id = await _setup_user_and_contract(engine)

            # Build one task (and a backing clause) per generated spec. Titles are
            # unique per index so we can match each persisted row to its source.
            clauses_data = []
            tasks = []
            expected: dict[str, dict] = {}
            for index, spec in enumerate(specs):
                clause_id = f"c{index}"
                title = f"Task {index}"
                source_text = "The Buyer shall perform the obligation as stated."
                clauses_data.append(
                    {
                        "clause_id": clause_id,
                        "heading": f"{index}. Clause",
                        "body_text": source_text,
                        "clause_type": "GENERAL",
                        "confidence": 0.5,
                    }
                )
                tasks.append(
                    _TaskRec(
                        task_id=f"t{index}",
                        clause_id=clause_id,
                        title=title,
                        obligated_party="The Buyer",
                        obligation_type="OBLIGATION",
                        date_type=spec["date_type"],
                        due_date=spec["due_date"],
                        requires_review=spec["requires_review"],
                        source_text=source_text,
                    )
                )
                expected[title] = spec

            result = ml.PipelineResult(
                contract_id=contract_id,
                contract_name="deal.pdf",
                clauses_data=clauses_data,
                tasks=tasks,
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

            assert len(rows) == len(specs)

            for row in rows:
                spec = expected[row.title]
                unresolved = _is_unresolved(spec["date_type"], spec["due_date"])

                if unresolved:
                    # Property 8: unresolved relative/conditional deadline must be
                    # flagged for review and must NOT carry any due_date.
                    assert row.requires_review is True, (
                        f"unresolved task {row.title!r} (date_type="
                        f"{spec['date_type']!r}) was not flagged for review"
                    )
                    assert row.due_date is None, (
                        f"unresolved task {row.title!r} fabricated a due_date "
                        f"{row.due_date!r} (upstream due_date was None)"
                    )
                else:
                    # Sanity: a resolved ABSOLUTE/RECURRING deadline (or a
                    # RELATIVE/CONDITIONAL with a concrete date) retains its
                    # upstream due_date untouched.
                    assert row.due_date == spec["due_date"], (
                        f"resolved task {row.title!r} (date_type="
                        f"{spec['date_type']!r}) lost its due_date: "
                        f"{row.due_date!r} != {spec['due_date']!r}"
                    )
                    # And its review flag stays as upstream supplied it.
                    assert row.requires_review is bool(spec["requires_review"])
        finally:
            await engine.dispose()

    asyncio.run(scenario())
