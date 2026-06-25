"""Heavy ``ml``-queue pipeline task chain that wraps the existing ``clauseops`` package.

This module implements task 8.2: the orchestration that *imports and runs* the
existing ``clauseops`` pipeline end to end — it never rewrites the pipeline. The
stages run in the order fixed by the design (design.md "Component 4: Celery ML
Worker") and Requirement 4.1::

    extract -> segment -> classify -> ner -> obligations -> normalize_dates -> generate_tasks

Each stage is a thin adapter over a *verified* ``clauseops`` public callable,
obtained from :func:`app.processing.celery_app.get_pipeline` so the heavy models
are warm-loaded once per worker process and reused across every stage of a run.

Scope of THIS task (8.2):

* Orchestrate the chain and produce the in-memory :class:`PipelineResult`.
* Set the Contract ``status`` to ``PROCESSING`` for the duration of the run
  (Requirement 4.3).

Explicit seams are left for the adjacent tasks so they plug in without
reshaping this module:

* **Task 8.3 — span offsets**: computed from ``PipelineResult.tasks`` (each a
  ``clauseops`` ``TaskRecord`` carrying ``source_text``/``obligated_party``/
  ``action``). See :func:`_compute_span_offsets_seam`.
* **Task 8.4 — persistence + status COMPLETE/FAILED + review flagging**:
  consumes :class:`PipelineResult`. See :func:`_persist_result_seam` and the
  ``try/except`` skeleton in :func:`process_contract`.
* **Task 9.1 — progress publishing**: every stage invokes a ``ProgressHook``;
  the default is a no-op. 9.1 supplies a hook that publishes ``{stage,
  progress_pct, status}`` to Redis pub/sub and updates ``contracts.progress_pct``.

Import safety / no network:

* Importing this module pulls in only Celery wiring and SQLAlchemy — it does NOT
  import ``clauseops`` and does NOT load any model. All ``clauseops`` access is
  deferred to :func:`app.processing.celery_app.get_pipeline` which imports lazily
  inside the worker process.
* The ``ml`` worker makes no outbound network calls: it reads the PDF from the
  configured :class:`~app.storage.base.StorageBackend` (local filesystem for the
  MVP) and talks only to the local database.

Database access inside the Celery task:

The application uses async SQLAlchemy 2.0. Celery's prefork worker runs tasks
synchronously, so each DB touch is wrapped in :func:`asyncio.run`. To avoid
reusing a connection pool bound to a previous (now-closed) event loop, the async
helpers create and dispose a short-lived engine per call. This is the simplest
approach that reuses the existing async stack without introducing a second
(synchronous) database driver.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.data.enums import ContractStatus, ReminderChannel, TaskStatus
from app.data.models import Clause, Contract, Reminder, Task
from app.processing.celery_app import (
    ML_TASK_NAMESPACE,
    celery_app,
    get_pipeline,
)
from app.processing.progress import make_progress_hook, publish_failed
from app.processing.spans import compute_span_offsets
from app.storage import get_storage_backend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage definitions and progress seam (task 9.1 plugs in here)
# ---------------------------------------------------------------------------

# Ordered stage -> cumulative ``progress_pct`` after the stage completes. The
# final jump to 100 belongs to the COMPLETE transition owned by task 8.4, so the
# last orchestrated stage stops short of 100.
STAGE_PROGRESS: "OrderedDict[str, int]" = OrderedDict(
    (
        ("extract", 5),
        ("segment", 25),
        ("classify", 45),
        ("ner", 60),
        ("obligations", 75),
        ("normalize_dates", 85),
        ("generate_tasks", 95),
    )
)

# The canonical stage order (Requirement 4.1 / design Component 4).
PIPELINE_STAGES: tuple[str, ...] = tuple(STAGE_PROGRESS.keys())

# A progress hook receives (stage_name, progress_pct). Task 9.1 supplies a real
# implementation; the default below does nothing so 8.2 stays self-contained.
ProgressHook = Callable[[str, int], None]


def _noop_progress(stage: str, progress_pct: int) -> None:
    """Default progress hook: record at debug level only (no Redis, no DB).

    Task 9.1 replaces this with a publisher that pushes ``{stage, progress_pct,
    status}`` to the Contract's Redis pub/sub channel and updates
    ``contracts.progress_pct``.
    """

    logger.debug("pipeline stage complete: %s (%d%%)", stage, progress_pct)


class PipelineError(RuntimeError):
    """Raised when the pipeline cannot run (e.g. missing contract or file).

    Task 8.4 catches failures around the chain to set ``status = FAILED`` and
    populate ``error_message``; this typed error makes that handling explicit.
    """


# ---------------------------------------------------------------------------
# In-memory pipeline result (consumed by tasks 8.3 and 8.4)
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """All in-memory artifacts produced by one end-to-end pipeline run.

    Holds the raw ``clauseops`` outputs (no DB rows yet). Task 8.3 reads
    ``tasks`` to compute span offsets; task 8.4 reads ``clauses``/``tasks`` (and
    optionally ``obligations``/``deadlines``) to persist rows and set the final
    status.

    Attributes:
        contract_id: The Contract being processed.
        contract_name: The original filename, threaded into task generation.
        clauses: ``list[ClauseChunk]`` from ``segment_contract``.
        classifications: ``list[dict]`` from ``classify_clauses`` (aligned).
        entities: ``list[dict]`` from ``extract_entities_from_contract`` (aligned).
        obligations: ``list[list[ObligationRecord]]`` from
            ``classify_contract_obligations`` (aligned to clauses).
        deadlines: ``list[list[DeadlineRecord]]`` from ``normalize_contract_dates``.
        tasks: ``list[TaskRecord]`` from ``generate_tasks_for_contract``.
        clauses_data: the merged per-clause dicts fed to the obligation/date/
            task-generation stages (clause_id, heading, body_text, clause_type,
            entities, entity_summary, relations, ...).
        task_spans: one normalized dict per entry in ``tasks`` (same order),
            produced by task 8.3's :func:`_compute_span_offsets_seam`. Each dict
            pairs a task's grounding strings (``source_text``/``obligated_party``/
            ``action``/``deadline_raw``) with the character offsets computed from
            them, ready for task 8.4 to persist onto ``tasks`` rows. Empty until
            the span seam runs.
    """

    contract_id: int
    contract_name: str
    clauses: list[Any] = field(default_factory=list)
    classifications: list[dict] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    obligations: list[Any] = field(default_factory=list)
    deadlines: list[Any] = field(default_factory=list)
    tasks: list[Any] = field(default_factory=list)
    clauses_data: list[dict] = field(default_factory=list)
    task_spans: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage adapters over the real clauseops callables (no rewrite)
# ---------------------------------------------------------------------------


def _build_clauses_data(
    clauses: list[Any],
    classifications: list[dict],
    entities: list[dict],
) -> list[dict]:
    """Merge segmentation, classification, and NER outputs into per-clause dicts.

    The obligation/date/task-generation ``clauseops`` callables all consume a
    ``clauses_data`` list of dicts, each carrying the fields they read
    (``clause_id``, ``heading``, ``body_text``, ``clause_type``, ``entities``,
    ``entity_summary``, ``relations``). The three source lists are positionally
    aligned (one entry per ``ClauseChunk``).
    """

    merged: list[dict] = []
    for index, chunk in enumerate(clauses):
        classification = classifications[index] if index < len(classifications) else {}
        ner = entities[index] if index < len(entities) else {}
        merged.append(
            {
                "clause_id": getattr(chunk, "clause_id", classification.get("clause_id", f"clause_{index}")),
                "heading": getattr(chunk, "heading", "") or "",
                "body_text": getattr(chunk, "body_text", "") or "",
                "clause_type": classification.get("clause_type", ""),
                "confidence": classification.get("confidence", 0.0),
                "needs_review": classification.get("needs_review", False),
                "entities": ner.get("entities", []),
                "entity_summary": ner.get("entity_summary", {}),
                "relations": ner.get("relations", []),
            }
        )
    return merged


def run_pipeline(
    pdf_path: str,
    contract_name: str,
    contract_id: int,
    *,
    pipeline: Optional[dict[str, Callable]] = None,
    progress: Optional[ProgressHook] = None,
    anchor_date: Optional[date] = None,
) -> PipelineResult:
    """Run the clauseops chain end to end and return the in-memory result.

    Stages run strictly in the order ``segment -> classify -> ner -> obligations
    -> normalize_dates -> generate_tasks`` (the ``extract`` stage having already
    materialized ``pdf_path``). A ``progress`` hook is invoked after each stage
    so task 9.1 can publish per-stage updates without changing this orchestration.

    Args:
        pdf_path: Local filesystem path to the contract PDF (produced by the
            ``extract`` stage in :func:`process_contract`).
        contract_name: Original filename, passed to task generation.
        contract_id: The Contract id (carried through for downstream persistence).
        pipeline: Optional pre-resolved stage callables. Defaults to
            :func:`get_pipeline` (the warm-loaded, process-cached callables). The
            seam lets tests drive the orchestration with lightweight stand-ins
            and lets the worker reuse warm models.
        progress: Optional progress hook (defaults to a no-op).
        anchor_date: Optional contract effective/signing date; when ``None`` the
            ``clauseops`` date logic auto-detects it.

    Returns:
        A populated :class:`PipelineResult`.
    """

    stages = pipeline if pipeline is not None else get_pipeline()
    notify = progress or _noop_progress

    # extract has already happened (pdf_path exists); record its progress so the
    # stage sequence the client sees matches the design's seven-stage chain.
    notify("extract", STAGE_PROGRESS["extract"])

    # --- segment ---------------------------------------------------------
    clauses = stages["segment_contract"](pdf_path)
    notify("segment", STAGE_PROGRESS["segment"])

    # --- classify --------------------------------------------------------
    classifications = stages["classify_clauses"](clauses)
    notify("classify", STAGE_PROGRESS["classify"])

    # --- ner -------------------------------------------------------------
    entities = stages["extract_entities_from_contract"](clauses)
    notify("ner", STAGE_PROGRESS["ner"])

    # Merge once; the remaining stages all consume this per-clause view.
    clauses_data = _build_clauses_data(clauses, classifications, entities)

    # --- obligations -----------------------------------------------------
    obligations = stages["classify_contract_obligations"](clauses_data)
    notify("obligations", STAGE_PROGRESS["obligations"])

    # --- normalize_dates -------------------------------------------------
    deadlines = stages["normalize_contract_dates"](clauses_data, anchor_date)
    notify("normalize_dates", STAGE_PROGRESS["normalize_dates"])

    # --- generate_tasks --------------------------------------------------
    tasks = stages["generate_tasks_for_contract"](
        clauses_data, contract_name, anchor_date
    )
    notify("generate_tasks", STAGE_PROGRESS["generate_tasks"])

    return PipelineResult(
        contract_id=contract_id,
        contract_name=contract_name,
        clauses=list(clauses),
        classifications=list(classifications),
        entities=list(entities),
        obligations=list(obligations),
        deadlines=list(deadlines),
        tasks=list(tasks),
        clauses_data=clauses_data,
    )


# ---------------------------------------------------------------------------
# Database helpers (async, run via asyncio.run in the sync Celery task)
# ---------------------------------------------------------------------------


async def _amark_processing_in_session(
    session: AsyncSession, contract_id: int
) -> Optional[tuple[str, str]]:
    """Atomically claim a contract for processing within ``session``.

    Performs a guarded ``UPDATE ... WHERE status IN ('PENDING','FAILED')`` so
    that only one worker can transition a contract into PROCESSING; a duplicate
    enqueue, a redelivery, or ``run_pending.py`` racing a live worker cannot both
    claim it (the second sees ``rowcount == 0``). The update also clears
    ``error_message`` and resets ``progress_pct`` so a previously FAILED contract
    can be reprocessed cleanly (a plain ORM ``status`` assignment would trip the
    Contract validator while ``error_message`` is still set).

    Returns ``(file_key, filename)`` when this call won the claim; ``None`` when
    the contract is already PROCESSING/COMPLETE (so the caller should skip).
    Raises :class:`PipelineError` when the contract does not exist.
    """

    result = await session.execute(
        update(Contract)
        .where(
            Contract.id == contract_id,
            Contract.status.in_(
                [ContractStatus.PENDING, ContractStatus.FAILED]
            ),
        )
        .values(
            status=ContractStatus.PROCESSING,
            error_message=None,
            progress_pct=0,
        )
    )
    await session.commit()

    if result.rowcount != 1:
        # Either it doesn't exist, or it's already PROCESSING/COMPLETE.
        existing = await session.get(Contract, contract_id)
        if existing is None:
            raise PipelineError(f"Contract {contract_id} not found")
        return None

    row = (
        await session.execute(
            select(Contract.file_key, Contract.filename).where(
                Contract.id == contract_id
            )
        )
    ).one()
    return row[0], row[1]


async def _aload_and_mark_processing(contract_id: int) -> Optional[tuple[str, str]]:
    """Claim the contract for processing; return (file_key, filename) or None.

    Creates and disposes a dedicated NullPool async engine so no connection pool
    is shared across event loops (each Celery task gets a fresh
    :func:`asyncio.run`) and no idle connections linger.
    """

    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await _amark_processing_in_session(session, contract_id)
    finally:
        await engine.dispose()


def _mark_processing_and_load(contract_id: int) -> Optional[tuple[str, str]]:
    """Synchronous wrapper: claim the contract and return (file_key, filename)."""

    return asyncio.run(_aload_and_mark_processing(contract_id))


# ---------------------------------------------------------------------------
# Extract stage: materialize the stored PDF to a local temp path (no network)
# ---------------------------------------------------------------------------


def _extract_pdf_to_tempfile(file_key: str) -> str:
    """Fetch the contract bytes from storage and write them to a temp PDF.

    ``segment_contract`` consumes a filesystem path, so the ``extract`` stage
    streams the object out of the :class:`StorageBackend` (local filesystem for
    the MVP — no outbound network call) into a temporary ``.pdf`` file. The
    caller is responsible for unlinking the returned path.
    """

    storage = get_storage_backend()
    data = storage.get(file_key)
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="clauseops_ml_")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        # Don't leak the descriptor/file if writing fails.
        Path(tmp_path).unlink(missing_ok=True)
        raise
    return tmp_path


# ---------------------------------------------------------------------------
# Seams for adjacent tasks (kept as no-ops here so 8.2 stays focused)
# ---------------------------------------------------------------------------


def _flatten_records(grouped: list[Any]) -> list[Any]:
    """Flatten the clauseops obligation/deadline outputs to a flat record list.

    ``classify_contract_obligations`` / ``normalize_contract_dates`` are
    documented as ``list[list[...]]`` (one inner list per clause), but we accept
    a already-flat ``list[record]`` too so the seam is robust to either shape.
    """

    flat: list[Any] = []
    for item in grouped or []:
        if isinstance(item, (list, tuple)):
            flat.extend(item)
        else:
            flat.append(item)
    return flat


def _index_by_clause(records: list[Any]) -> dict[Any, list[Any]]:
    """Group records by their ``clause_id`` attribute (preserving order)."""

    by_clause: dict[Any, list[Any]] = {}
    for rec in records:
        clause_id = getattr(rec, "clause_id", None)
        by_clause.setdefault(clause_id, []).append(rec)
    return by_clause


def _resolve_action(task: Any, obligations_by_clause: dict[Any, list[Any]]) -> str:
    """Best-effort recover the verbatim action span for ``task``.

    ``TaskRecord`` (clauseops) does **not** carry the extracted action span — it
    lives on the originating ``ObligationRecord`` (``action`` / legacy
    ``action_verb``). We therefore correlate the task back to an obligation in
    the same clause, preferring one with a matching ``obligation_type`` and an
    action that is actually present (verbatim) in the task's ``source_text``.

    Correctness note: whatever string this returns is the SAME string the
    offsets are computed against and that task 8.4 persists as ``Task.action``,
    so the grounding round-trip (``source_text[action_start:action_end] ==
    action``) holds by construction regardless of which candidate is chosen. A
    direct ``task.action`` attribute, if a future ``TaskRecord`` grows one, wins.
    """

    direct = (getattr(task, "action", "") or "").strip()
    if direct:
        return direct

    source_text = getattr(task, "source_text", "") or ""
    candidates = obligations_by_clause.get(getattr(task, "clause_id", None), [])
    task_type = getattr(task, "obligation_type", None)
    same_type = [
        o for o in candidates if getattr(o, "obligation_type", None) == task_type
    ] or candidates

    def _action_of(obl: Any) -> str:
        return (getattr(obl, "action", "") or getattr(obl, "action_verb", "") or "").strip()

    # Prefer an action that grounds (is a substring of source_text).
    for obl in same_type:
        action = _action_of(obl)
        if action and action in source_text:
            return action
    # Fall back to the first non-empty action (offset will be None if absent).
    for obl in same_type:
        action = _action_of(obl)
        if action:
            return action
    return ""


def _resolve_deadline_raw(
    task: Any, deadlines_by_clause: dict[Any, list[Any]]
) -> Optional[str]:
    """Best-effort recover the deadline raw-text span for ``task``.

    Like the action, the deadline raw text is not stored on ``TaskRecord``; it
    lives on the ``DeadlineRecord`` (``raw_text``). Correlate by clause,
    preferring a deadline whose ``date_type`` matches the task and whose raw text
    grounds in ``source_text``.
    """

    source_text = getattr(task, "source_text", "") or ""
    candidates = deadlines_by_clause.get(getattr(task, "clause_id", None), [])
    if not candidates:
        return None

    task_date_type = getattr(task, "date_type", None)
    same_type = [
        d for d in candidates if getattr(d, "date_type", None) == task_date_type
    ] or candidates

    for deadline in same_type:
        raw = (getattr(deadline, "raw_text", "") or "").strip()
        if raw and raw in source_text:
            return raw
    for deadline in same_type:
        raw = (getattr(deadline, "raw_text", "") or "").strip()
        if raw:
            return raw
    return None


def _compute_span_offsets_seam(result: PipelineResult) -> None:
    """Task 8.3 — compute grounding span offsets for every generated task.

    Iterates ``result.tasks`` (each a clauseops ``TaskRecord``) and, for each,
    derives the grounding strings — ``source_text``, ``obligated_party``, the
    verbatim ``action`` (recovered from the originating obligation, since
    ``TaskRecord`` doesn't carry it), and the deadline raw text — then computes
    character offsets into ``source_text`` via :func:`compute_span_offsets`
    (``source_text.find(span)``).

    The normalized result is stored on ``result.task_spans`` (one dict per task,
    same order) so task 8.4 can persist ``agent_start/end``/``action_start/end``
    (and the carried ``action``/``deadline`` spans) onto ``tasks`` rows. Per
    Requirements 5.2/5.3 each emitted offset pair either round-trips exactly to
    its span or is ``None`` (never fabricated), and present offsets satisfy
    ``0 <= start <= end <= len(source_text)``.

    Idempotent: rebuilds ``result.task_spans`` from scratch on each call.
    """

    obligations_by_clause = _index_by_clause(_flatten_records(result.obligations))
    deadlines_by_clause = _index_by_clause(_flatten_records(result.deadlines))

    task_spans: list[dict] = []
    for task in result.tasks:
        source_text = getattr(task, "source_text", "") or ""
        party = (getattr(task, "obligated_party", "") or "")
        action = _resolve_action(task, obligations_by_clause)
        deadline_raw = _resolve_deadline_raw(task, deadlines_by_clause)

        offsets = compute_span_offsets(source_text, party, action, deadline_raw)

        task_spans.append(
            {
                "task_id": getattr(task, "task_id", None),
                "clause_id": getattr(task, "clause_id", None),
                "source_text": source_text,
                "obligated_party": party,
                "action": action,
                "deadline_raw": deadline_raw,
                "agent_start": offsets.agent_start,
                "agent_end": offsets.agent_end,
                "action_start": offsets.action_start,
                "action_end": offsets.action_end,
                "deadline_start": offsets.deadline_start,
                "deadline_end": offsets.deadline_end,
            }
        )

    result.task_spans = task_spans
    return None


# ---------------------------------------------------------------------------
# Task 8.4 — persistence, status tracking, and review flagging
# ---------------------------------------------------------------------------

# date_type values for which a *missing* ``due_date`` means the deadline could
# not be resolved (Requirement 8.2). RECURRING/ABSOLUTE legitimately resolve (or
# have no single due date) and are not treated as "unresolved".
_UNRESOLVED_DATE_TYPES: frozenset[str] = frozenset({"CONDITIONAL", "RELATIVE"})


def _clamp_unit(value: Any) -> float:
    """Coerce ``value`` to a float in [0, 1] (the ``tasks`` validator domain).

    ``confidence``/``agent_score`` are NOT NULL floats constrained to the unit
    interval. We clamp rather than fail so a slightly out-of-range upstream score
    never aborts an otherwise-complete run.
    """

    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if result < 0.0:
        return 0.0
    if result > 1.0:
        return 1.0
    return result


def _clamp_confidence(value: Any) -> Optional[float]:
    """Coerce ``value`` to a float in [0, 1], or ``None`` (the ``clauses`` domain)."""

    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return min(1.0, max(0.0, result))


def _has_unresolved_deadline(task: Any) -> bool:
    """Return True when ``task`` carries an unresolved relative/conditional date.

    The clauseops date normalizer leaves ``due_date`` (``normalized_date``)
    ``None`` and marks the deadline for review when a relative or conditional
    date cannot be resolved to a concrete calendar date (no anchor, "upon
    completion of Phase 2", etc.). We mirror that here: a task whose
    ``date_type`` is RELATIVE or CONDITIONAL and whose ``due_date`` is ``None``
    has an unresolved deadline (Requirement 8.2).
    """

    date_type = (getattr(task, "date_type", "") or "").upper()
    return date_type in _UNRESOLVED_DATE_TYPES and getattr(task, "due_date", None) is None


def _to_reminder_datetime(value: Any) -> datetime:
    """Normalize a ``date``/``datetime`` reminder value to a tz-aware datetime.

    ``TaskRecord.reminder_dates`` are ``date`` objects; ``reminders.remind_at``
    is a timezone-aware ``DateTime``. We anchor bare dates at UTC midnight.
    """

    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    raise TypeError(f"unsupported reminder value: {value!r}")


def _iter_clause_specs(result: PipelineResult) -> Iterator[dict]:
    """Yield a normalized clause dict per clause, preferring the merged view.

    ``run_pipeline`` always populates ``clauses_data`` (the merged per-clause
    dicts). We fall back to the raw ``ClauseChunk`` list only if that view is
    empty, so persistence is robust to either shape.
    """

    if result.clauses_data:
        for cdata in result.clauses_data:
            yield {
                "clause_id": cdata.get("clause_id"),
                "heading": (cdata.get("heading") or None),
                "clause_type": (cdata.get("clause_type") or None),
                "body_text": (cdata.get("body_text") or ""),
                "confidence": cdata.get("confidence"),
            }
    else:
        for chunk in result.clauses:
            yield {
                "clause_id": getattr(chunk, "clause_id", None),
                "heading": (getattr(chunk, "heading", None) or None),
                "clause_type": (getattr(chunk, "clause_type", None) or None),
                "body_text": (getattr(chunk, "body_text", "") or ""),
                "confidence": None,
            }


async def _apersist_result_in_session(
    session: AsyncSession, result: PipelineResult
) -> None:
    """Persist clauses/tasks/reminders and mark the Contract COMPLETE atomically.

    All rows are scoped to the owning user transitively through ``contract_id``
    (clauses/tasks/reminders carry no ``user_id`` of their own — they belong to
    the contract, which belongs to the user; Requirement 2.3). Everything is
    staged and committed once at the end so a failure anywhere leaves NO partial
    artifacts and never a COMPLETE contract with half-written rows
    (Requirement 4.7).

    Persistence steps:

    1. Validate the contract exists (raise :class:`PipelineError` otherwise).
    2. Persist :class:`Clause` rows (``clause_index`` = position) and flush to
       assign primary keys, building a clauseops ``clause_id`` -> DB id map.
    3. Persist :class:`Task` rows mapping ``TaskRecord`` fields + the computed
       span offsets / resolved ``action`` from ``result.task_spans`` (same order
       as ``result.tasks``), linking ``clause_id`` to the persisted Clause id.
       Uncertain extractions are flagged ``requires_review`` and unresolved
       relative/conditional deadlines persist ``due_date = NULL`` (Req 8.1/8.2).
    4. Persist :class:`Reminder` rows from each task's ``reminder_dates``.
    5. Set the Contract ``status = COMPLETE``, ``progress_pct = 100``,
       ``completed_at = now`` (Requirement 4.6) and commit once.
    """

    contract = await session.get(Contract, result.contract_id)
    if contract is None:
        raise PipelineError(f"Contract {result.contract_id} not found")

    # (1b) IDEMPOTENCY: remove any derived rows from a prior run of this contract
    # before re-inserting, so a redelivery / re-dispatch never duplicates
    # clauses/tasks/reminders. Deleting tasks cascades (DB ON DELETE CASCADE) to
    # their reminders and notifications; clauses are deleted next. All within the
    # same transaction that flips the contract to COMPLETE below.
    await session.execute(delete(Task).where(Task.contract_id == contract.id))
    await session.execute(delete(Clause).where(Clause.contract_id == contract.id))
    await session.flush()

    # (2) clauses -> flush -> clauseops clause_id -> DB id map.
    clause_rows: list[tuple[Any, Clause]] = []
    for index, spec in enumerate(_iter_clause_specs(result)):
        clause = Clause(
            contract_id=contract.id,
            clause_index=index,
            heading=spec["heading"],
            clause_type=spec["clause_type"],
            body_text=spec["body_text"],
            confidence=_clamp_confidence(spec["confidence"]),
        )
        session.add(clause)
        clause_rows.append((spec["clause_id"], clause))

    await session.flush()  # assign Clause primary keys (no commit yet).
    clause_id_map: dict[Any, int] = {
        clause_id: clause.id for clause_id, clause in clause_rows if clause_id is not None
    }

    # (3)/(4) tasks (+offsets, +resolved action) and their reminders.
    spans = result.task_spans
    for position, task in enumerate(result.tasks):
        span = spans[position] if position < len(spans) else {}

        unresolved = _has_unresolved_deadline(task)
        # Req 8.1: flag inferred party / unresolved deadline (TaskRecord already
        # carries the upstream review flag for inferred agents & abstained
        # actions); Req 8.2: unresolved relative/conditional deadlines too.
        requires_review = bool(getattr(task, "requires_review", False)) or unresolved
        # Req 8.2: never fabricate an anchor/due date for an unresolved deadline.
        due_date = None if unresolved else getattr(task, "due_date", None)

        source_text = (
            span.get("source_text")
            if span
            else (getattr(task, "source_text", "") or "")
        ) or ""

        task_row = Task(
            contract_id=contract.id,
            clause_id=clause_id_map.get(getattr(task, "clause_id", None)),
            title=(getattr(task, "title", "") or ""),
            description=getattr(task, "description", None),
            obligated_party=(getattr(task, "obligated_party", None) or None),
            beneficiary=getattr(task, "beneficiary", None),
            obligation_type=(getattr(task, "obligation_type", None) or None),
            action=(span.get("action") or None),
            agent_start=span.get("agent_start"),
            agent_end=span.get("agent_end"),
            action_start=span.get("action_start"),
            action_end=span.get("action_end"),
            due_date=due_date,
            date_type=(getattr(task, "date_type", None) or None),
            priority=(getattr(task, "priority", None) or None),
            status=TaskStatus.PENDING,
            requires_review=requires_review,
            confidence=_clamp_unit(getattr(task, "confidence", 0.0)),
            agent_score=_clamp_unit(getattr(task, "agent_score", 0.0)),
            source_text=source_text,
        )
        session.add(task_row)

        for reminder_value in (getattr(task, "reminder_dates", None) or []):
            session.add(
                Reminder(
                    task=task_row,
                    remind_at=_to_reminder_datetime(reminder_value),
                    channel=ReminderChannel.IN_APP,
                    sent=False,
                )
            )

    # (5) Final COMPLETE transition (Requirement 4.6). error_message stays NULL,
    # which the Contract validators require for any non-FAILED status.
    contract.status = ContractStatus.COMPLETE
    contract.progress_pct = 100
    contract.completed_at = datetime.now(timezone.utc)

    await session.commit()


async def _apersist_success(result: PipelineResult) -> None:
    """Persist a successful run via a short-lived async engine (DB-in-Celery)."""

    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await _apersist_result_in_session(session, result)
    finally:
        await engine.dispose()


def _persist_result_seam(result: PipelineResult, progress: ProgressHook) -> None:
    """Task 8.4 — persist clauses/tasks/reminders and set status COMPLETE.

    On full success this stores the clauses, tasks (with grounding span offsets
    and the resolved verbatim ``action``), and reminders for the run — all scoped
    to the owning user through the contract (Requirement 2.3) — flags uncertain
    extractions with ``requires_review`` (Req 8.1/8.2), and marks the Contract
    COMPLETE at ``progress_pct = 100`` (Requirement 4.6). The FAILED path lives in
    :func:`process_contract` so a stage error never persists partial artifacts as
    COMPLETE (Requirement 4.7).
    """

    asyncio.run(_apersist_success(result))
    # The final jump to 100/COMPLETE is published via the progress hook (task
    # 9.1 supplies a Redis-publishing implementation; the default is a no-op).
    progress("complete", 100)
    return None


async def _amark_failed_in_session(
    session: AsyncSession, contract_id: int, error_message: str
) -> None:
    """Set ``status = FAILED`` and populate ``error_message`` within ``session``.

    Order matters: the Contract validators only allow ``error_message`` to be
    non-null when ``status == FAILED``, so the status is set first.
    """

    contract = await session.get(Contract, contract_id)
    if contract is None:
        raise PipelineError(f"Contract {contract_id} not found")
    contract.status = ContractStatus.FAILED
    contract.error_message = error_message or "Pipeline failed"
    await session.commit()


async def _amark_failed(contract_id: int, error_message: str) -> None:
    """Mark a contract FAILED via a short-lived async engine (DB-in-Celery)."""

    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await _amark_failed_in_session(session, contract_id, error_message)
    finally:
        await engine.dispose()


def _mark_failed(contract_id: int, error_message: str) -> None:
    """Synchronous wrapper: set ``status = FAILED`` and populate ``error_message``."""

    asyncio.run(_amark_failed(contract_id, error_message))


def _format_error_message(exc: BaseException) -> str:
    """Render an exception into a concise, human-readable ``error_message``."""

    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


# ---------------------------------------------------------------------------
# Celery task — the ml-queue entry point
# ---------------------------------------------------------------------------


@celery_app.task(name=f"{ML_TASK_NAMESPACE}.process_contract", bind=True)
def process_contract(self, contract_id: int) -> dict:  # noqa: ANN001 - celery self
    """Process one contract through the clauseops chain on the heavy ``ml`` queue.

    Registered under the ``app.processing.ml.*`` namespace so Celery routing
    sends it to the dedicated ``ml`` queue. The task:

    1. Sets the Contract ``status = PROCESSING`` (Requirement 4.3).
    2. Runs the ``extract`` stage (stored PDF -> local temp file; no network).
    3. Runs the chain via :func:`run_pipeline`, reusing warm-loaded models.
    4. Computes grounding span offsets (8.3), persists clauses/tasks/reminders
       and sets ``status = COMPLETE`` on success (8.4); on any stage error sets
       ``status = FAILED`` with an ``error_message`` and persists no partial
       artifacts as COMPLETE (Requirement 4.7). Progress publishing is the 9.1
       seam.

    Returns a small JSON-serializable summary of the run (the heavy artifacts
    stay in process for 8.3/8.4 to consume).
    """

    # Task 9.1: a real progress hook bound to ``contract_id`` publishes
    # ``{stage, progress_pct, status}`` to the Contract's Redis channel and
    # updates ``contracts.progress_pct``/``status`` on every stage (Req 4.2).
    # Publishing is best-effort, so this never adds a hard broker dependency to
    # a run: a Redis outage degrades to DB-only progress without failing.
    progress: ProgressHook = make_progress_hook(contract_id)

    # (1) Atomically claim the contract (PENDING/FAILED -> PROCESSING) and fetch
    # the stored-file key. If the claim is lost (already PROCESSING/COMPLETE, e.g.
    # a duplicate enqueue or redelivery), skip without reprocessing so derived
    # rows are never duplicated.
    claim = _mark_processing_and_load(contract_id)
    if claim is None:
        logger.info(
            "contract %s already claimed/processed; skipping duplicate run",
            contract_id,
        )
        return {"contract_id": contract_id, "status": "skipped"}
    file_key, contract_name = claim

    tmp_pdf: Optional[str] = None
    try:
        # (2) extract: stream the stored PDF to a local temp path.
        tmp_pdf = _extract_pdf_to_tempfile(file_key)

        # (3) run the chain end to end (segment .. generate_tasks).
        result = run_pipeline(
            tmp_pdf,
            contract_name,
            contract_id,
            progress=progress,
        )

        # (4a) SEAM 8.3 — compute grounding span offsets from result.tasks.
        _compute_span_offsets_seam(result)

        # (4b) SEAM 8.4 — persist artifacts + set status COMPLETE.
        _persist_result_seam(result, progress)

        return {
            "contract_id": contract_id,
            "status": ContractStatus.COMPLETE.value,
            "clause_count": len(result.clauses),
            "task_count": len(result.tasks),
            "stages": list(PIPELINE_STAGES),
        }
    except Exception as exc:
        # Requirement 4.7: any stage error -> FAILED + error_message, and NO
        # partial artifacts persisted as COMPLETE. Persistence + the COMPLETE
        # transition happen together in _persist_result_seam, so reaching here
        # means nothing was committed as COMPLETE. Marking FAILED is best-effort
        # so a secondary DB problem can't mask the original failure, and the
        # failure is broadcast on the progress channel for connected clients.
        try:
            _mark_failed(contract_id, _format_error_message(exc))
        except Exception:  # pragma: no cover - defensive logging only
            logger.exception("could not mark contract %s as FAILED", contract_id)
        # Best-effort publish of the FAILED transition (Req 4.7). A broker
        # outage must not mask the original error, so this never raises.
        publish_failed(contract_id)
        raise
    finally:
        # Always clean up the extracted temp PDF.
        if tmp_pdf:
            Path(tmp_pdf).unlink(missing_ok=True)


__all__ = [
    "PIPELINE_STAGES",
    "STAGE_PROGRESS",
    "ProgressHook",
    "PipelineError",
    "PipelineResult",
    "run_pipeline",
    "process_contract",
]
