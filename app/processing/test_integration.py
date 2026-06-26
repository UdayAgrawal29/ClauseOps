"""End-to-end integration tests for the ML processing flow (spec task 21.1).

These tests exercise the FULL wiring of the contract analysis path:

    upload (a seeded PENDING Contract + a stored PDF)
        -> process_contract  (the Celery ``ml``-queue entry point)
            -> _mark_processing_and_load   (PENDING -> PROCESSING)
            -> _extract_pdf_to_tempfile    (stream stored bytes; no network)
            -> run_pipeline                (the clauseops stage chain)
            -> _compute_span_offsets_seam  (grounding offsets, task 8.3)
            -> _persist_result_seam        (clauses/tasks/reminders + COMPLETE)

against a REAL database, verifying that the infrastructure is wired together
correctly for 1-3 representative contracts.

Why this is NOT the heavy pipeline
----------------------------------
Running the real ``clauseops`` chain loads gigabytes of models (Docling,
spaCy-trf, transformers, torch) and takes ~40s/contract -- unsuitable for CI.
Per the design's Testing Strategy, integration tests verify the *wiring* while
the property/unit tests cover the pure logic. So here we inject a lightweight
STUB pipeline (via ``monkeypatch`` of :func:`app.processing.ml.get_pipeline`)
that returns realistic, in-memory clauseops-shaped records and makes NO outbound
network calls. The stub keys off a marker embedded in each contract's PDF bytes
so a single global stub can serve several distinct representative contracts.

Database (CI-friendly default)
------------------------------
The DB-in-Celery code (``ml`` / ``progress``) builds a short-lived async engine
from ``settings.database_url`` on every DB touch. We point persistence at the
test database by setting ``CLAUSEOPS_DATABASE_URL`` (env, prefix ``CLAUSEOPS_``)
to a **file-backed** ``sqlite+aiosqlite`` URL and clearing the ``get_settings``
LRU cache. A file-backed (not ``:memory:``) SQLite DB is required because each
short-lived engine is a fresh connection and must see the same data.

To run against a live PostgreSQL instead, set ``CLAUSEOPS_DATABASE_URL`` to an
async URL such as ``postgresql+asyncpg://user:pass@host:5432/db`` before running
these tests (and ensure the schema exists). SQLite is the self-contained
default so the suite needs no external services.

Requirements covered: 4.1 (the fixed stage chain runs end to end), 4.6 (status
transitions PENDING -> PROCESSING -> COMPLETE on success; FAILED with an
``error_message`` on a stage error, with no partial COMPLETE), 5.4 (the
persisted analysis -- clauses/tasks/reminders -- is scoped to the owning user
through the contract, with grounding span offsets that round-trip).
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.data.database import Base
from app.data.enums import ContractStatus, ReminderChannel, TaskStatus
from app.data.models import Clause, Contract, Reminder, Task, User
from app.processing import ml, progress


# ---------------------------------------------------------------------------
# Lightweight stand-ins mirroring the real clauseops record shapes
# ---------------------------------------------------------------------------


@dataclass
class _Chunk:
    """Stand-in for a ``clauseops`` ``ClauseChunk`` (only the read fields)."""

    clause_id: str
    heading: str
    body_text: str


@dataclass
class _OblRec:
    """Stand-in for a ``clauseops`` ``ObligationRecord`` (the action source)."""

    clause_id: str
    obligation_type: str
    action: str


@dataclass
class _DeadlineRec:
    """Stand-in for a ``clauseops`` ``DeadlineRecord`` (deadline raw-text source)."""

    clause_id: str
    date_type: str
    raw_text: str


@dataclass
class _TaskRec:
    """Stand-in for a ``clauseops`` ``TaskRecord`` (the fields persistence reads)."""

    task_id: str
    clause_id: str
    title: str
    obligated_party: str
    obligation_type: str
    date_type: str
    due_date: Optional[date]
    requires_review: bool
    source_text: str
    description: Optional[str] = None
    beneficiary: Optional[str] = None
    priority: str = "MEDIUM"
    reminder_dates: list = field(default_factory=list)
    confidence: float = 0.0
    agent_score: float = 0.0


@dataclass
class _Fixture:
    """A realistic, fully in-memory analysis for one representative contract."""

    marker: str
    clauses: list[_Chunk]
    classifications: list[dict]
    entities: list[dict]
    obligations: list[list[_OblRec]]
    deadlines: list[list[_DeadlineRec]]
    tasks: list[_TaskRec]
    # When set, the named stage raises to drive the FAILED path.
    fail_stage: Optional[str] = None


# ---------------------------------------------------------------------------
# Representative synthetic contracts (1-3). Small %PDF byte payloads are fine
# because segmentation is stubbed; the marker selects the fixture.
# ---------------------------------------------------------------------------


def _pdf_bytes(marker: str) -> bytes:
    """Build a minimal, valid-looking PDF payload carrying a fixture ``marker``.

    Real segmentation is stubbed, so the bytes only need the ``%PDF`` magic and
    a marker the stub can read back to choose the right in-memory analysis.
    """

    return f"%PDF-1.4\n% clauseops-fixture:{marker}\n".encode("utf-8")


def _marker_from_pdf(pdf_path: str) -> str:
    """Recover the fixture marker the stub embedded in the stored PDF bytes."""

    text = Path(pdf_path).read_bytes().decode("utf-8", errors="ignore")
    token = "clauseops-fixture:"
    start = text.index(token) + len(token)
    return text[start:].splitlines()[0].strip()


# --- Fixture "alpha": two clauses, two grounded tasks, one with reminders. ---
_ALPHA = _Fixture(
    marker="alpha",
    clauses=[
        _Chunk("c1", "1. Payment", "The Buyer shall pay the invoice within thirty (30) days."),
        _Chunk("c2", "2. Delivery", "The Seller shall deliver the goods to the Buyer."),
    ],
    classifications=[
        {"clause_id": "c1", "clause_type": "PAYMENT", "confidence": 0.92, "needs_review": False},
        {"clause_id": "c2", "clause_type": "DELIVERY", "confidence": 0.81, "needs_review": False},
    ],
    entities=[
        {"clause_id": "c1", "entities": [{"text": "Buyer"}], "entity_summary": {"PARTY": ["Buyer"]}, "relations": []},
        {"clause_id": "c2", "entities": [{"text": "Seller"}], "entity_summary": {"PARTY": ["Seller"]}, "relations": []},
    ],
    obligations=[
        [_OblRec("c1", "OBLIGATION", "pay the invoice")],
        [_OblRec("c2", "OBLIGATION", "deliver the goods")],
    ],
    deadlines=[
        [_DeadlineRec("c1", "RELATIVE", "thirty (30) days")],
        [],
    ],
    tasks=[
        _TaskRec(
            task_id="t1",
            clause_id="c1",
            title="Pay invoice",
            obligated_party="The Buyer",
            obligation_type="OBLIGATION",
            date_type="RELATIVE",
            due_date=date(2030, 1, 31),
            requires_review=False,
            source_text="The Buyer shall pay the invoice within thirty (30) days.",
            priority="HIGH",
            reminder_dates=[date(2030, 1, 24), date(2030, 1, 28)],
            confidence=0.88,
            agent_score=0.77,
        ),
        _TaskRec(
            task_id="t2",
            clause_id="c2",
            title="Deliver goods",
            obligated_party="The Seller",
            obligation_type="OBLIGATION",
            date_type="ABSOLUTE",
            due_date=date(2030, 2, 15),
            requires_review=False,
            source_text="The Seller shall deliver the goods to the Buyer.",
            priority="MEDIUM",
        ),
    ],
)

# --- Fixture "beta": a single clause/task contract (a second representative). -
_BETA = _Fixture(
    marker="beta",
    clauses=[_Chunk("b1", "1. Insurance", "Vendor must maintain insurance coverage at all times.")],
    classifications=[{"clause_id": "b1", "clause_type": "INSURANCE", "confidence": 0.7, "needs_review": True}],
    entities=[{"clause_id": "b1", "entities": [{"text": "Vendor"}], "entity_summary": {"PARTY": ["Vendor"]}, "relations": []}],
    obligations=[[_OblRec("b1", "OBLIGATION", "maintain insurance coverage")]],
    deadlines=[[]],
    tasks=[
        _TaskRec(
            task_id="bt1",
            clause_id="b1",
            title="Maintain insurance",
            obligated_party="Vendor",
            obligation_type="OBLIGATION",
            date_type="ABSOLUTE",
            due_date=date(2030, 6, 1),
            requires_review=False,
            source_text="Vendor must maintain insurance coverage at all times.",
            priority="LOW",
            reminder_dates=[date(2030, 5, 25)],
            confidence=0.66,
            agent_score=0.55,
        )
    ],
)

# --- Fixture "boom": forces a stage error to drive the FAILED path. ---
_BOOM = _Fixture(
    marker="boom",
    clauses=[_Chunk("x1", "1. X", "Some clause body.")],
    classifications=[],
    entities=[],
    obligations=[],
    deadlines=[],
    tasks=[],
    fail_stage="classify_clauses",
)

_FIXTURES = {f.marker: f for f in (_ALPHA, _BETA, _BOOM)}


# ---------------------------------------------------------------------------
# The injected STUB pipeline (no models, no network)
# ---------------------------------------------------------------------------


def _make_stub_pipeline():
    """Build a clauseops-shaped pipeline whose stages serve fixtures by marker.

    ``segment_contract`` reads the marker out of the stored PDF bytes and stashes
    the chosen :class:`_Fixture` in ``state``; the later stages read it back. The
    runs are sequential (one ``process_contract`` at a time), so a single mutable
    holder is safe. Any stage named in ``fixture.fail_stage`` raises to drive the
    FAILED path. No stage performs any I/O beyond reading the local temp PDF.
    """

    state: dict[str, _Fixture] = {}

    def _maybe_fail(stage: str) -> None:
        fixture = state.get("fixture")
        if fixture is not None and fixture.fail_stage == stage:
            raise RuntimeError(f"forced failure in stage {stage}")

    def segment_contract(pdf_path):
        marker = _marker_from_pdf(pdf_path)
        fixture = _FIXTURES[marker]
        state["fixture"] = fixture
        _maybe_fail("segment_contract")
        return list(fixture.clauses)

    def classify_clauses(_clauses):
        _maybe_fail("classify_clauses")
        return list(state["fixture"].classifications)

    def extract_entities_from_contract(_clauses):
        _maybe_fail("extract_entities_from_contract")
        return list(state["fixture"].entities)

    def classify_contract_obligations(_clauses_data):
        _maybe_fail("classify_contract_obligations")
        return [list(group) for group in state["fixture"].obligations]

    def normalize_contract_dates(_clauses_data, _anchor_date):
        _maybe_fail("normalize_contract_dates")
        return [list(group) for group in state["fixture"].deadlines]

    def generate_tasks_for_contract(_clauses_data, _contract_name, _anchor_date):
        _maybe_fail("generate_tasks_for_contract")
        return list(state["fixture"].tasks)

    return {
        "segment_contract": segment_contract,
        "classify_clauses": classify_clauses,
        "extract_entities_from_contract": extract_entities_from_contract,
        "classify_contract_obligations": classify_contract_obligations,
        "normalize_contract_dates": normalize_contract_dates,
        "generate_tasks_for_contract": generate_tasks_for_contract,
    }


# ---------------------------------------------------------------------------
# Test environment: file-backed SQLite DB + local storage, wired via settings
# ---------------------------------------------------------------------------


@pytest.fixture()
def integration_env(tmp_path, monkeypatch):
    """Point persistence + storage at a self-contained, CI-friendly test rig.

    * A file-backed ``sqlite+aiosqlite`` DB (so the many short-lived engines the
      DB-in-Celery code opens all observe the same data).
    * A temp-dir :class:`LocalFilesystemStorage` root.
    * ``get_settings`` LRU cache cleared so ``ml`` / ``progress`` pick up the env.
    * The stub pipeline injected via ``ml.get_pipeline``.
    * Redis publishing replaced by an in-memory capture so the test makes no
      outbound network calls and can assert the progress/status broadcasts.

    Yields a small namespace with the session factory, captured progress
    messages, and a ``db_url`` for documentation/debugging.
    """

    db_path = tmp_path / "integration.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    storage_root = tmp_path / "storage"

    monkeypatch.setenv("CLAUSEOPS_DATABASE_URL", db_url)
    monkeypatch.setenv("CLAUSEOPS_STORAGE_BACKEND", "local")
    monkeypatch.setenv("CLAUSEOPS_STORAGE_ROOT", str(storage_root))
    get_settings.cache_clear()

    # Inject the stub pipeline (run_pipeline calls get_pipeline() when no
    # explicit pipeline is passed, which is the case inside process_contract).
    monkeypatch.setattr(ml, "get_pipeline", _make_stub_pipeline)

    # Capture Redis publishes instead of hitting a broker (no network).
    published: list[dict] = []
    monkeypatch.setattr(
        progress, "_publish", lambda channel, message: published.append({"channel": channel, **message})
    )

    # Create the schema once on the file DB.
    async def _create_schema() -> None:
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_create_schema())

    factory = async_sessionmaker(
        create_async_engine(db_url), expire_on_commit=False
    )

    class _Env:
        pass

    env = _Env()
    env.db_url = db_url
    env.session_factory = factory
    env.published = published
    env.storage_root = storage_root

    yield env

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Seeding helpers (the "upload" half: an owned PENDING contract + stored PDF)
# ---------------------------------------------------------------------------


async def _aseed_contract(factory, *, owner_email: str, marker: str) -> tuple[int, int]:
    """Seed a user + a PENDING contract and store its PDF; return (user_id, id).

    Mirrors what ``POST /contracts`` persists: an owned Contract in ``PENDING``
    with a randomized ``file_key`` whose bytes live in the configured storage
    backend. The bytes carry the fixture marker so the stub pipeline can serve
    the matching representative analysis.
    """

    from app.storage import get_storage_backend

    async with factory() as session:
        user = User(email=owner_email, password_hash="hash")
        session.add(user)
        await session.commit()
        user_id = user.id

        file_key = f"contracts/{user_id}/{marker}.pdf"
        contract = Contract(
            user_id=user_id,
            filename=f"{marker}.pdf",
            file_key=file_key,
            status=ContractStatus.PENDING,
        )
        session.add(contract)
        await session.commit()
        contract_id = contract.id

    # Store the PDF bytes through the configured (local) backend.
    get_storage_backend().put(file_key, _pdf_bytes(marker))
    return user_id, contract_id


def _seed_contract(factory, *, owner_email: str, marker: str) -> tuple[int, int]:
    return asyncio.run(_aseed_contract(factory, owner_email=owner_email, marker=marker))


async def _aload_full(factory, contract_id: int):
    """Load a contract plus its clauses/tasks/reminders for assertions."""

    async with factory() as session:
        contract = await session.get(Contract, contract_id)
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
        task_ids = [t.id for t in tasks]
        reminders = []
        if task_ids:
            reminders = (
                await session.execute(select(Reminder).where(Reminder.task_id.in_(task_ids)))
            ).scalars().all()
        return contract, list(clauses), list(tasks), list(reminders)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_end_to_end_upload_to_persisted_analysis(integration_env):
    """Full wiring: a PENDING upload runs through the chain to a COMPLETE analysis.

    Verifies (Req 4.1/4.6/5.4):
    * the contract ends COMPLETE at 100% with ``completed_at`` set, no error,
    * clauses/tasks/reminders persist and are scoped to the owner via contract,
    * Task grounding span offsets round-trip into ``source_text``,
    * reminders are derived (in-app, unsent), and the PROCESSING->COMPLETE
      transitions are broadcast on the progress channel.
    """

    factory = integration_env.session_factory
    owner_id, contract_id = _seed_contract(
        factory, owner_email="owner@example.com", marker="alpha"
    )

    # --- run the heavy ml-queue task synchronously (stubbed chain) ---
    summary = ml.process_contract.run(contract_id)

    assert summary["contract_id"] == contract_id
    assert summary["status"] == ContractStatus.COMPLETE.value
    assert summary["clause_count"] == 2
    assert summary["task_count"] == 2
    # Req 4.1: the canonical seven-stage chain ran in order.
    assert summary["stages"] == list(ml.PIPELINE_STAGES)

    contract, clauses, tasks, reminders = asyncio.run(_aload_full(factory, contract_id))

    # Req 4.6: terminal COMPLETE state.
    assert contract.status == ContractStatus.COMPLETE
    assert contract.progress_pct == 100
    assert contract.completed_at is not None
    assert contract.error_message is None

    # Clauses persisted in document order with their analysis fields.
    assert [c.clause_index for c in clauses] == [0, 1]
    assert clauses[0].heading == "1. Payment"
    assert clauses[0].clause_type == "PAYMENT"
    assert abs(clauses[0].confidence - 0.92) < 1e-9

    # Tasks persisted (ordered by title: Deliver goods, Pay invoice).
    deliver, pay = tasks
    assert deliver.title == "Deliver goods"
    assert pay.title == "Pay invoice"
    assert pay.status == TaskStatus.PENDING

    # Clause linkage maps the clauseops string id -> persisted Clause DB id.
    assert pay.clause_id == clauses[0].id
    assert deliver.clause_id == clauses[1].id

    # Req 5.4 (grounding): span offsets round-trip into source_text.
    assert pay.source_text[pay.agent_start:pay.agent_end] == "The Buyer"
    assert pay.source_text[pay.action_start:pay.action_end] == "pay the invoice"
    assert pay.action == "pay the invoice"
    assert deliver.source_text[deliver.agent_start:deliver.agent_end] == "The Seller"
    assert deliver.source_text[deliver.action_start:deliver.action_end] == "deliver the goods"

    # Reminders derived from t1's reminder_dates only (in-app, unsent).
    assert len(reminders) == 2
    assert all(r.channel == ReminderChannel.IN_APP for r in reminders)
    assert all(r.sent is False for r in reminders)
    assert all(r.task_id == pay.id for r in reminders)

    # Req 4.6: the progress channel saw PROCESSING stages then the COMPLETE jump.
    statuses = [m["status"] for m in integration_env.published]
    assert ContractStatus.PROCESSING.value in statuses
    assert integration_env.published[-1]["status"] == ContractStatus.COMPLETE.value
    assert integration_env.published[-1]["progress_pct"] == 100


def test_status_transitions_pending_processing_complete(integration_env):
    """PENDING -> PROCESSING (observed mid-run) -> COMPLETE (Req 4.6).

    A spy stage injected at the front of the chain reads the contract's status
    straight from the DB while the chain runs, capturing the PROCESSING state
    that is otherwise overwritten by the terminal COMPLETE transition.
    """

    factory = integration_env.session_factory
    _owner_id, contract_id = _seed_contract(
        factory, owner_email="owner2@example.com", marker="beta"
    )

    # Seed-time status is PENDING.
    contract, *_ = asyncio.run(_aload_full(factory, contract_id))
    assert contract.status == ContractStatus.PENDING

    observed: dict[str, ContractStatus] = {}
    base_pipeline = _make_stub_pipeline()
    real_segment = base_pipeline["segment_contract"]

    def spying_segment(pdf_path):
        # By the time the chain runs, _mark_processing_and_load has committed
        # PROCESSING; read it back from the real DB to prove the transition.
        async def _read():
            async with factory() as session:
                row = await session.get(Contract, contract_id)
                return row.status

        observed["during_chain"] = asyncio.run(_read())
        return real_segment(pdf_path)

    base_pipeline["segment_contract"] = spying_segment

    # Point get_pipeline at our spy-enabled pipeline for this run, restoring the
    # fixture's stub afterwards.
    ml_get_pipeline = ml.get_pipeline
    ml.get_pipeline = lambda: base_pipeline
    try:
        ml.process_contract.run(contract_id)
    finally:
        ml.get_pipeline = ml_get_pipeline

    assert observed["during_chain"] == ContractStatus.PROCESSING

    contract, _clauses, tasks, _reminders = asyncio.run(_aload_full(factory, contract_id))
    assert contract.status == ContractStatus.COMPLETE
    assert contract.progress_pct == 100
    assert len(tasks) == 1


def test_stage_error_yields_failed_with_no_partial_complete(integration_env):
    """A forced stage error -> FAILED + error_message, no partial analysis (Req 4.6/4.7).

    The "boom" fixture raises inside the ``classify`` stage. ``process_contract``
    must re-raise, mark the contract FAILED with a populated ``error_message``,
    and persist NO clauses/tasks (nothing committed as COMPLETE).
    """

    factory = integration_env.session_factory
    _owner_id, contract_id = _seed_contract(
        factory, owner_email="owner3@example.com", marker="boom"
    )

    with pytest.raises(RuntimeError, match="forced failure in stage classify_clauses"):
        ml.process_contract.run(contract_id)

    contract, clauses, tasks, reminders = asyncio.run(_aload_full(factory, contract_id))

    # Req 4.6/4.7: terminal FAILED state with an error message...
    assert contract.status == ContractStatus.FAILED
    assert contract.error_message
    assert "classify_clauses" in contract.error_message
    assert contract.completed_at is None
    # ...and absolutely no partially-persisted analysis.
    assert clauses == []
    assert tasks == []
    assert reminders == []

    # The failure was broadcast on the progress channel (Req 4.7).
    assert integration_env.published[-1]["status"] == ContractStatus.FAILED.value


def test_persisted_analysis_is_scoped_to_owning_user(integration_env):
    """Persisted clauses/tasks are reachable only through the owning user (Req 5.4).

    Two users each upload and process a contract. Joining each owned analysis
    back through ``contracts.user_id`` must return only that user's rows, with no
    cross-tenant bleed -- the wiring that the read APIs rely on for isolation.
    """

    factory = integration_env.session_factory
    alice_id, alice_contract = _seed_contract(
        factory, owner_email="alice@example.com", marker="alpha"
    )
    bob_id, bob_contract = _seed_contract(
        factory, owner_email="bob@example.com", marker="beta"
    )

    ml.process_contract.run(alice_contract)
    ml.process_contract.run(bob_contract)

    async def _tasks_for_user(user_id: int) -> list[Task]:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(Task)
                    .join(Contract, Task.contract_id == Contract.id)
                    .where(Contract.user_id == user_id)
                )
            ).scalars().all()
            return list(rows)

    alice_tasks = asyncio.run(_tasks_for_user(alice_id))
    bob_tasks = asyncio.run(_tasks_for_user(bob_id))

    # Alice owns the two alpha tasks; Bob owns the single beta task.
    assert {t.title for t in alice_tasks} == {"Pay invoice", "Deliver goods"}
    assert {t.title for t in bob_tasks} == {"Maintain insurance"}

    # No task is reachable from the wrong owner (every task maps to one contract,
    # and each contract to exactly one user).
    assert all(t.contract_id == alice_contract for t in alice_tasks)
    assert all(t.contract_id == bob_contract for t in bob_tasks)


def test_pipeline_makes_no_outbound_network_calls(integration_env):
    """The stubbed run loads no heavy models and makes no outbound calls (design constraint).

    The representative run must not pull in ``clauseops``/``torch``/``docling``
    (the heavy, model-backed, network-capable deps); the stub serves everything
    in-memory and Redis publishing is captured locally, so the run is hermetic.
    """

    factory = integration_env.session_factory
    _owner_id, contract_id = _seed_contract(
        factory, owner_email="owner4@example.com", marker="alpha"
    )

    ml.process_contract.run(contract_id)

    assert "clauseops" not in sys.modules
    assert "torch" not in sys.modules
    assert "docling" not in sys.modules
