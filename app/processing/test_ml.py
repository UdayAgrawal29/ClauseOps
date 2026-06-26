"""Unit tests for the ML pipeline task chain (spec task 8.2).

These tests cover the orchestration that imports and runs the existing
``clauseops`` pipeline (without rewriting it):

* Import safety — importing :mod:`app.processing.ml` triggers no Redis
  connection and no heavy model loading (``clauseops``/``torch``/``docling`` stay
  unimported).
* Chain orchestration — :func:`run_pipeline` calls the six clauseops stages in
  the fixed order, assembles the per-clause data correctly, fires the progress
  hook once per stage, and returns a fully populated :class:`PipelineResult`.
* Extract stage — :func:`_extract_pdf_to_tempfile` round-trips bytes out of the
  storage backend (no network).
* PROCESSING transition — the status helper sets ``status = PROCESSING``
  (Requirement 4.3) and surfaces missing contracts as :class:`PipelineError`.
* Task wiring — :func:`process_contract` marks PROCESSING, runs the chain, hits
  the 8.3/8.4 seams, and always cleans up the extracted temp file.

The chain stages are driven with lightweight stand-in callables injected via the
``pipeline`` seam, so the real (heavy, model-backed) clauseops functions are not
needed to verify the orchestration logic.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from app.processing import ml


# ---------------------------------------------------------------------------
# Lightweight stand-ins mirroring the real clauseops shapes
# ---------------------------------------------------------------------------


@dataclass
class _Chunk:
    """Minimal stand-in for ``clauseops`` ``ClauseChunk`` (only fields we read)."""

    clause_id: str
    heading: str
    body_text: str


def _make_fake_pipeline(calls: list[str]):
    """Build a pipeline dict whose stages record their invocation order.

    Each callable appends its stage name to ``calls`` and returns a value shaped
    like the real clauseops output so :func:`ml.run_pipeline` can thread them
    through the chain.
    """

    chunks = [
        _Chunk(clause_id="c1", heading="1. Payment", body_text="Buyer shall pay."),
        _Chunk(clause_id="c2", heading="2. Term", body_text="Term is one year."),
    ]

    def segment_contract(pdf_path):
        calls.append("segment")
        assert isinstance(pdf_path, str)
        return chunks

    def classify_clauses(received):
        calls.append("classify")
        assert received is chunks
        return [
            {"clause_id": "c1", "clause_type": "PAYMENT", "confidence": 0.9, "needs_review": False},
            {"clause_id": "c2", "clause_type": "TERM", "confidence": 0.8, "needs_review": True},
        ]

    def extract_entities_from_contract(received):
        calls.append("ner")
        assert received is chunks
        return [
            {"clause_id": "c1", "entities": [{"text": "Buyer"}], "entity_summary": {"PARTY": ["Buyer"]}, "relations": [{"r": 1}]},
            {"clause_id": "c2", "entities": [], "entity_summary": {}, "relations": []},
        ]

    def classify_contract_obligations(clauses_data):
        calls.append("obligations")
        # Must receive the merged per-clause dicts, not the raw chunks.
        assert clauses_data[0]["clause_type"] == "PAYMENT"
        assert clauses_data[0]["relations"] == [{"r": 1}]
        return [["ob1"], []]

    def normalize_contract_dates(clauses_data, anchor_date):
        calls.append("normalize_dates")
        assert anchor_date is None
        assert clauses_data[0]["body_text"] == "Buyer shall pay."
        return [["deadline1"], []]

    def generate_tasks_for_contract(clauses_data, contract_name, anchor_date):
        calls.append("generate_tasks")
        assert contract_name == "contract.pdf"
        assert clauses_data[1]["clause_type"] == "TERM"
        return ["task1", "task2", "task3"]

    pipeline = {
        "segment_contract": segment_contract,
        "classify_clauses": classify_clauses,
        "extract_entities_from_contract": extract_entities_from_contract,
        "classify_contract_obligations": classify_contract_obligations,
        "normalize_contract_dates": normalize_contract_dates,
        "generate_tasks_for_contract": generate_tasks_for_contract,
    }
    return pipeline, chunks


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------


def test_import_does_not_load_clauseops_or_heavy_models():
    """Importing the task module must not pull in clauseops or heavy ML deps."""

    # ml is already imported at the top of this test module.
    assert "clauseops" not in sys.modules
    assert "torch" not in sys.modules
    assert "docling" not in sys.modules


def test_pipeline_stage_order_is_fixed():
    """The canonical stage order matches the design's seven-stage chain."""

    assert ml.PIPELINE_STAGES == (
        "extract",
        "segment",
        "classify",
        "ner",
        "obligations",
        "normalize_dates",
        "generate_tasks",
    )
    # progress is monotonically increasing and bounded below 100 (100 is 8.4).
    pcts = list(ml.STAGE_PROGRESS.values())
    assert pcts == sorted(pcts)
    assert all(0 <= p < 100 for p in pcts)


# ---------------------------------------------------------------------------
# clauses_data assembly
# ---------------------------------------------------------------------------


def test_build_clauses_data_merges_segmentation_classification_and_ner():
    chunks = [_Chunk(clause_id="c1", heading="H", body_text="B")]
    classifications = [{"clause_id": "c1", "clause_type": "PAYMENT", "confidence": 0.7, "needs_review": True}]
    entities = [{"clause_id": "c1", "entities": [{"x": 1}], "entity_summary": {"PARTY": ["A"]}, "relations": [{"y": 2}]}]

    merged = ml._build_clauses_data(chunks, classifications, entities)

    assert merged == [
        {
            "clause_id": "c1",
            "heading": "H",
            "body_text": "B",
            "clause_type": "PAYMENT",
            "confidence": 0.7,
            "needs_review": True,
            "entities": [{"x": 1}],
            "entity_summary": {"PARTY": ["A"]},
            "relations": [{"y": 2}],
        }
    ]


def test_build_clauses_data_tolerates_misaligned_lengths():
    """Missing classification/NER entries fall back to safe defaults."""

    chunks = [_Chunk(clause_id="c1", heading="", body_text="body")]
    merged = ml._build_clauses_data(chunks, classifications=[], entities=[])

    assert merged[0]["clause_id"] == "c1"
    assert merged[0]["clause_type"] == ""
    assert merged[0]["entities"] == []
    assert merged[0]["entity_summary"] == {}
    assert merged[0]["relations"] == []


# ---------------------------------------------------------------------------
# run_pipeline orchestration
# ---------------------------------------------------------------------------


def test_run_pipeline_runs_stages_in_order_and_populates_result():
    calls: list[str] = []
    pipeline, chunks = _make_fake_pipeline(calls)
    progress_events: list[tuple[str, int]] = []

    result = ml.run_pipeline(
        "C:/tmp/contract.pdf",
        "contract.pdf",
        contract_id=42,
        pipeline=pipeline,
        progress=lambda stage, pct: progress_events.append((stage, pct)),
    )

    # Stages executed in the exact chain order (extract already done by caller).
    assert calls == ["segment", "classify", "ner", "obligations", "normalize_dates", "generate_tasks"]

    # Progress fired once per stage including the leading extract, with the
    # design's cumulative percentages.
    assert progress_events == [
        ("extract", 5),
        ("segment", 25),
        ("classify", 45),
        ("ner", 60),
        ("obligations", 75),
        ("normalize_dates", 85),
        ("generate_tasks", 95),
    ]

    # Result carries every in-memory artifact for tasks 8.3/8.4.
    assert result.contract_id == 42
    assert result.contract_name == "contract.pdf"
    assert result.clauses is not chunks and result.clauses == chunks
    assert len(result.classifications) == 2
    assert len(result.entities) == 2
    assert result.obligations == [["ob1"], []]
    assert result.deadlines == [["deadline1"], []]
    assert result.tasks == ["task1", "task2", "task3"]
    assert len(result.clauses_data) == 2


def test_run_pipeline_defaults_to_noop_progress():
    """Omitting the progress hook must not raise (default no-op)."""

    calls: list[str] = []
    pipeline, _ = _make_fake_pipeline(calls)
    result = ml.run_pipeline("p.pdf", "contract.pdf", 1, pipeline=pipeline)
    assert result.tasks == ["task1", "task2", "task3"]


# ---------------------------------------------------------------------------
# Extract stage (storage round-trip, no network)
# ---------------------------------------------------------------------------


def test_extract_pdf_to_tempfile_roundtrips_storage_bytes(monkeypatch, tmp_path):
    """The extract stage streams stored bytes into a local temp PDF."""

    from app.storage.local import LocalFilesystemStorage

    backend = LocalFilesystemStorage(str(tmp_path / "store"))
    payload = b"%PDF-1.7 fake contract bytes"
    backend.put("contracts/u1/abc.pdf", payload)

    monkeypatch.setattr(ml, "get_storage_backend", lambda: backend)

    tmp_pdf = ml._extract_pdf_to_tempfile("contracts/u1/abc.pdf")
    try:
        assert Path(tmp_pdf).suffix == ".pdf"
        assert Path(tmp_pdf).read_bytes() == payload
    finally:
        Path(tmp_pdf).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# PROCESSING transition (Requirement 4.3) against an async SQLite session
# ---------------------------------------------------------------------------


def test_mark_processing_sets_status_and_returns_file_key():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.data.database import Base
    from app.data.enums import ContractStatus
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
                    status=ContractStatus.PENDING,
                )
                session.add(contract)
                await session.commit()
                contract_id = contract.id

            async with factory() as session:
                file_key, filename = await ml._amark_processing_in_session(session, contract_id)

            assert file_key == "contracts/u1/deal.pdf"
            assert filename == "deal.pdf"

            async with factory() as session:
                refreshed = await session.get(Contract, contract_id)
                assert refreshed.status == ContractStatus.PROCESSING
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_mark_processing_raises_for_missing_contract():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.data.database import Base

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                with pytest.raises(ml.PipelineError):
                    await ml._amark_processing_in_session(session, 999)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# process_contract task wiring (DB + extract + chain stubbed at the seams)
# ---------------------------------------------------------------------------


def test_process_contract_marks_processing_runs_chain_and_cleans_up(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    # Stub the DB touch (covered separately) -> returns (file_key, filename).
    monkeypatch.setattr(
        ml, "_mark_processing_and_load", lambda cid: ("contracts/u1/x.pdf", "x.pdf")
    )

    # Stub extract to produce a real temp file we can assert is cleaned up.
    fake_pdf = tmp_path / "extracted.pdf"
    fake_pdf.write_bytes(b"%PDF data")

    def fake_extract(file_key):
        assert file_key == "contracts/u1/x.pdf"
        seen["extracted"] = str(fake_pdf)
        return str(fake_pdf)

    monkeypatch.setattr(ml, "_extract_pdf_to_tempfile", fake_extract)

    # Stub run_pipeline to avoid loading clauseops, returning a small result.
    def fake_run_pipeline(pdf_path, contract_name, contract_id, progress=None):
        seen["ran"] = (pdf_path, contract_name, contract_id)
        return ml.PipelineResult(
            contract_id=contract_id,
            contract_name=contract_name,
            clauses=[1, 2],
            tasks=["t1"],
        )

    monkeypatch.setattr(ml, "run_pipeline", fake_run_pipeline)

    # Track that the 8.3 / 8.4 seams are invoked.
    monkeypatch.setattr(ml, "_compute_span_offsets_seam", lambda r: seen.__setitem__("offsets", True))
    monkeypatch.setattr(ml, "_persist_result_seam", lambda r, p: seen.__setitem__("persist", True))

    summary = ml.process_contract.run(7)

    assert seen["ran"] == (str(fake_pdf), "x.pdf", 7)
    assert seen["offsets"] is True
    assert seen["persist"] is True
    assert summary["contract_id"] == 7
    assert summary["status"] == "COMPLETE"
    assert summary["clause_count"] == 2
    assert summary["task_count"] == 1
    assert summary["stages"] == list(ml.PIPELINE_STAGES)

    # The extracted temp file is always cleaned up.
    assert not fake_pdf.exists()


def test_process_contract_cleans_up_temp_file_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ml, "_mark_processing_and_load", lambda cid: ("k", "x.pdf")
    )
    fake_pdf = tmp_path / "extracted.pdf"
    fake_pdf.write_bytes(b"%PDF data")
    monkeypatch.setattr(ml, "_extract_pdf_to_tempfile", lambda key: str(fake_pdf))

    def boom(*args, **kwargs):
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(ml, "run_pipeline", boom)

    # Capture the FAILED marking instead of touching a real database.
    marked: dict[str, object] = {}
    monkeypatch.setattr(
        ml, "_mark_failed", lambda cid, msg: marked.update(cid=cid, msg=msg)
    )
    # Persistence must never run when a stage errors (no partial COMPLETE).
    monkeypatch.setattr(
        ml,
        "_persist_result_seam",
        lambda r, p: pytest.fail("persist must not run on stage error"),
    )

    with pytest.raises(RuntimeError, match="stage exploded"):
        ml.process_contract.run(1)

    # Requirement 4.7: the contract is marked FAILED with an error message.
    assert marked["cid"] == 1
    assert "stage exploded" in str(marked["msg"])

    # Even on failure the extracted PDF is removed (the finally block).
    assert not fake_pdf.exists()


# ---------------------------------------------------------------------------
# Task 8.4 — persistence, status tracking, and review flagging
# ---------------------------------------------------------------------------

from datetime import date as _date  # noqa: E402


@dataclass
class _TaskRec:
    """Stand-in for clauseops ``TaskRecord`` (only the fields 8.4 reads)."""

    task_id: str
    clause_id: str
    title: str
    obligated_party: str
    obligation_type: str
    date_type: str
    due_date: Optional[_date]
    requires_review: bool
    source_text: str
    description: Optional[str] = None
    beneficiary: Optional[str] = None
    priority: str = "MEDIUM"
    reminder_dates: list = None  # type: ignore[assignment]
    confidence: float = 0.0
    agent_score: float = 0.0

    def __post_init__(self):
        if self.reminder_dates is None:
            self.reminder_dates = []


@dataclass
class _OblRec:
    """Stand-in for clauseops ``ObligationRecord`` (action source)."""

    clause_id: str
    obligation_type: str
    action: str


@dataclass
class _DeadlineRec:
    """Stand-in for clauseops ``DeadlineRecord`` (deadline raw-text source)."""

    clause_id: str
    date_type: str
    raw_text: str


def _new_sqlite_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine("sqlite+aiosqlite:///:memory:")


async def _setup_user_and_contract(engine):
    """Create the schema plus a PROCESSING contract; return its id."""

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.data.database import Base
    from app.data.enums import ContractStatus
    from app.data.models import Contract, User

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
        return factory, contract.id


def _build_result_with_spans(contract_id):
    """Build a PipelineResult covering resolved/unresolved deadlines, then offsets."""

    clauses_data = [
        {
            "clause_id": "c1",
            "heading": "1. Payment",
            "body_text": "The Buyer shall pay the invoice within thirty (30) days.",
            "clause_type": "PAYMENT",
            "confidence": 0.91,
        },
        {
            "clause_id": "c2",
            "heading": "2. Delivery",
            "body_text": "The Seller shall deliver upon completion of Phase 2.",
            "clause_type": "DELIVERY",
            "confidence": 0.4,
        },
    ]
    obligations = [
        [_OblRec(clause_id="c1", obligation_type="OBLIGATION", action="pay the invoice")],
        [_OblRec(clause_id="c2", obligation_type="OBLIGATION", action="deliver")],
    ]
    deadlines = [
        [_DeadlineRec(clause_id="c1", date_type="RELATIVE", raw_text="thirty (30) days")],
        [_DeadlineRec(clause_id="c2", date_type="CONDITIONAL", raw_text="upon completion of Phase 2")],
    ]
    tasks = [
        # Resolved relative deadline -> due_date set, not flagged.
        _TaskRec(
            task_id="t1",
            clause_id="c1",
            title="Pay invoice",
            obligated_party="The Buyer",
            obligation_type="OBLIGATION",
            date_type="RELATIVE",
            due_date=_date(2030, 1, 31),
            requires_review=False,
            source_text="The Buyer shall pay the invoice within thirty (30) days.",
            priority="HIGH",
            reminder_dates=[_date(2030, 1, 24), _date(2030, 1, 28)],
            confidence=0.88,
            agent_score=0.77,
        ),
        # Unresolved conditional deadline -> no due_date, must be flagged.
        _TaskRec(
            task_id="t2",
            clause_id="c2",
            title="Deliver goods",
            obligated_party="The Seller",
            obligation_type="OBLIGATION",
            date_type="CONDITIONAL",
            due_date=None,
            requires_review=True,
            source_text="The Seller shall deliver upon completion of Phase 2.",
            priority="MEDIUM",
            reminder_dates=[],
        ),
    ]
    result = ml.PipelineResult(
        contract_id=contract_id,
        contract_name="deal.pdf",
        clauses=[],
        classifications=[],
        entities=[],
        obligations=obligations,
        deadlines=deadlines,
        tasks=tasks,
        clauses_data=clauses_data,
    )
    ml._compute_span_offsets_seam(result)
    return result


def test_persist_success_writes_clauses_tasks_reminders_and_completes():
    from app.data.enums import ContractStatus, ReminderChannel, TaskStatus
    from app.data.models import Clause, Contract, Reminder, Task

    async def scenario():
        engine = _new_sqlite_engine()
        try:
            factory, contract_id = await _setup_user_and_contract(engine)
            result = _build_result_with_spans(contract_id)

            async with factory() as session:
                await ml._apersist_result_in_session(session, result)

            async with factory() as session:
                from sqlalchemy import select

                clauses = (
                    await session.execute(
                        select(Clause).where(Clause.contract_id == contract_id).order_by(Clause.clause_index)
                    )
                ).scalars().all()
                tasks = (
                    await session.execute(
                        select(Task).where(Task.contract_id == contract_id).order_by(Task.title)
                    )
                ).scalars().all()
                reminders = (
                    await session.execute(select(Reminder))
                ).scalars().all()
                contract = await session.get(Contract, contract_id)

            # Clauses persisted with index/heading/type/body/confidence.
            assert [c.clause_index for c in clauses] == [0, 1]
            assert clauses[0].heading == "1. Payment"
            assert clauses[0].clause_type == "PAYMENT"
            assert clauses[0].body_text.startswith("The Buyer shall pay")
            assert abs(clauses[0].confidence - 0.91) < 1e-9

            # Tasks persisted: Deliver goods (t2) then Pay invoice (t1) by title.
            deliver, pay = tasks
            assert deliver.title == "Deliver goods"
            assert pay.title == "Pay invoice"

            # Clause linkage maps clauseops string id -> Clause DB id.
            assert pay.clause_id == clauses[0].id
            assert deliver.clause_id == clauses[1].id

            # Resolved deadline retains its due_date and is not flagged.
            assert pay.due_date == _date(2030, 1, 31)
            assert pay.requires_review is False
            assert pay.priority == "HIGH"
            assert abs(pay.confidence - 0.88) < 1e-9
            assert abs(pay.agent_score - 0.77) < 1e-9

            # Grounding round-trip holds for the persisted offsets (Req 5.2).
            assert pay.source_text[pay.agent_start:pay.agent_end] == "The Buyer"
            assert pay.source_text[pay.action_start:pay.action_end] == "pay the invoice"
            assert pay.action == "pay the invoice"

            # Reminders from reminder_dates (in_app, unsent) for t1 only.
            assert len(reminders) == 2
            assert all(r.channel == ReminderChannel.IN_APP for r in reminders)
            assert all(r.sent is False for r in reminders)
            assert all(r.task_id == pay.id for r in reminders)

            # Status COMPLETE at 100%, completed_at populated, no error.
            assert contract.status == ContractStatus.COMPLETE
            assert contract.progress_pct == 100
            assert contract.completed_at is not None
            assert contract.error_message is None
            assert pay.status == TaskStatus.PENDING
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_persist_flags_unresolved_deadline_with_no_due_date():
    """Req 8.2: unresolved relative/conditional deadline -> review + null due."""

    from app.data.models import Task

    async def scenario():
        engine = _new_sqlite_engine()
        try:
            factory, contract_id = await _setup_user_and_contract(engine)
            result = _build_result_with_spans(contract_id)

            async with factory() as session:
                await ml._apersist_result_in_session(session, result)

            async with factory() as session:
                from sqlalchemy import select

                deliver = (
                    await session.execute(
                        select(Task).where(Task.title == "Deliver goods")
                    )
                ).scalars().one()

            assert deliver.due_date is None
            assert deliver.requires_review is True
            assert deliver.date_type == "CONDITIONAL"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_persist_flags_unresolved_relative_even_when_upstream_not_flagged():
    """A RELATIVE deadline with no due_date is unresolved -> we still flag it."""

    from app.data.models import Task

    async def scenario():
        engine = _new_sqlite_engine()
        try:
            factory, contract_id = await _setup_user_and_contract(engine)
            task = _TaskRec(
                task_id="t9",
                clause_id="c1",
                title="Unanchored relative",
                obligated_party="The Buyer",
                obligation_type="OBLIGATION",
                date_type="RELATIVE",
                due_date=None,            # unresolved (no anchor)
                requires_review=False,    # upstream did NOT flag it
                source_text="The Buyer shall pay within ten days.",
            )
            result = ml.PipelineResult(
                contract_id=contract_id,
                contract_name="deal.pdf",
                clauses_data=[{"clause_id": "c1", "heading": "", "body_text": task.source_text, "clause_type": "PAYMENT", "confidence": 0.5}],
                tasks=[task],
            )
            ml._compute_span_offsets_seam(result)

            async with factory() as session:
                await ml._apersist_result_in_session(session, result)

            async with factory() as session:
                from sqlalchemy import select

                row = (await session.execute(select(Task))).scalars().one()

            assert row.requires_review is True
            assert row.due_date is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_persist_missing_contract_raises_and_writes_nothing():
    """A missing contract aborts persistence before any rows are committed."""

    from app.data.models import Clause, Task

    async def scenario():
        engine = _new_sqlite_engine()
        try:
            factory, contract_id = await _setup_user_and_contract(engine)
            result = _build_result_with_spans(contract_id)
            result.contract_id = contract_id + 999  # nonexistent

            async with factory() as session:
                with pytest.raises(ml.PipelineError):
                    await ml._apersist_result_in_session(session, result)

            async with factory() as session:
                from sqlalchemy import select

                clauses = (await session.execute(select(Clause))).scalars().all()
                tasks = (await session.execute(select(Task))).scalars().all()

            assert clauses == []
            assert tasks == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_persist_rolls_back_on_error_no_partial_complete():
    """A failure mid-persist leaves no partial rows and no COMPLETE contract."""

    from app.data.enums import ContractStatus
    from app.data.models import Clause, Contract, Task

    async def scenario():
        engine = _new_sqlite_engine()
        try:
            factory, contract_id = await _setup_user_and_contract(engine)
            # A bad reminder value makes _to_reminder_datetime raise after the
            # clauses have been flushed but before the COMPLETE commit.
            task = _TaskRec(
                task_id="t1",
                clause_id="c1",
                title="Pay",
                obligated_party="The Buyer",
                obligation_type="OBLIGATION",
                date_type="ABSOLUTE",
                due_date=_date(2030, 1, 1),
                requires_review=False,
                source_text="The Buyer shall pay.",
                reminder_dates=["not-a-date"],
            )
            result = ml.PipelineResult(
                contract_id=contract_id,
                contract_name="deal.pdf",
                clauses_data=[{"clause_id": "c1", "heading": "", "body_text": task.source_text, "clause_type": "PAYMENT", "confidence": 0.5}],
                tasks=[task],
            )
            ml._compute_span_offsets_seam(result)

            async with factory() as session:
                with pytest.raises(TypeError):
                    await ml._apersist_result_in_session(session, result)

            async with factory() as session:
                from sqlalchemy import select

                clauses = (await session.execute(select(Clause))).scalars().all()
                tasks = (await session.execute(select(Task))).scalars().all()
                contract = await session.get(Contract, contract_id)

            assert clauses == []          # rolled back
            assert tasks == []            # rolled back
            assert contract.status == ContractStatus.PROCESSING  # not COMPLETE
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_mark_failed_sets_status_and_error_message():
    """Req 4.7: FAILED with a populated error_message (status set before msg)."""

    from app.data.enums import ContractStatus
    from app.data.models import Contract

    async def scenario():
        engine = _new_sqlite_engine()
        try:
            factory, contract_id = await _setup_user_and_contract(engine)

            async with factory() as session:
                await ml._amark_failed_in_session(session, contract_id, "boom: kaput")

            async with factory() as session:
                contract = await session.get(Contract, contract_id)

            assert contract.status == ContractStatus.FAILED
            assert contract.error_message == "boom: kaput"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_format_error_message_includes_type_and_detail():
    assert ml._format_error_message(RuntimeError("stage exploded")) == "RuntimeError: stage exploded"
    assert ml._format_error_message(ValueError("")) == "ValueError"
