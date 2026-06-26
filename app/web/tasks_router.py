"""Task list HTTP endpoints (design "Component 6: Task & Dashboard APIs").

This module implements the deadline-first task list (task 10.2):

* ``GET /tasks`` -- return the authenticated owner's tasks, optionally narrowed
  by ``priority``, ``status``, ``due_before``/``due_after``, ``requires_review``,
  and ``contract_id``. Every query is scoped to the caller via
  :func:`~app.data.ownership.tasks_for_user`, so only the owner's tasks are ever
  returned (Requirements 6.1, 2.1).

Provided filters are combined with ``AND``. Results are paginated with
``limit``/``offset`` (capped) and ordered deterministically: tasks with a
``due_date`` come first in ascending date order (soonest deadline first, the
"deadline-first" principle), tasks without a ``due_date`` come last, and ``id``
breaks any remaining ties.
"""

from __future__ import annotations

import enum
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.data.enums import TaskStatus
from app.data.models import AuditLog, Task
from app.data.ownership import require_owned, tasks_for_user
from app.web.dependencies import CurrentUser, DbSession
from app.web.schemas import TaskRead, TaskUpdate

# Pagination guards for the list endpoint.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

# The user-correctable (non-status) subset of a Task. Supplying any of these on
# a ``PATCH /tasks/{id}`` is treated as a field correction (Requirement 7.3),
# recorded in ``corrected_fields`` with ``is_user_corrected`` set. ``status`` is
# handled separately (Requirements 7.1/7.2) and is deliberately excluded here.
_CORRECTABLE_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "obligated_party",
    "beneficiary",
    "action",
    "due_date",
    "date_type",
    "priority",
)

router = APIRouter(tags=["tasks"])


def _json_safe(value: Any) -> Any:
    """Coerce a value to something JSON-serializable for audit/override records.

    ``due_date`` is a :class:`datetime.date` and ``status`` a
    :class:`~app.data.enums.TaskStatus`; both are normalized so the resulting
    dicts can be stored in the JSON ``corrected_fields`` column and reused by
    the audit log (task 12.2) without a custom serializer.
    """

    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return value


@router.get("/tasks", response_model=list[TaskRead])
async def list_tasks(
    current_user: CurrentUser,
    session: DbSession,
    priority: Annotated[
        str | None, Query(description="Optional exact priority filter.")
    ] = None,
    status_filter: Annotated[
        TaskStatus | None,
        Query(alias="status", description="Optional task-status filter."),
    ] = None,
    due_before: Annotated[
        date | None,
        Query(description="Only tasks with a due_date on or before this date."),
    ] = None,
    due_after: Annotated[
        date | None,
        Query(description="Only tasks with a due_date on or after this date."),
    ] = None,
    requires_review: Annotated[
        bool | None,
        Query(description="Optional requires-review flag filter."),
    ] = None,
    contract_id: Annotated[
        int | None,
        Query(description="Restrict to tasks of a single owned contract."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Task]:
    """List the authenticated owner's tasks with optional filters (Requirements 6.1, 2.1).

    The query is scoped to the caller via
    :func:`~app.data.ownership.tasks_for_user`, so a task belonging to another
    user is never returned -- including when an explicit ``contract_id`` for a
    contract the caller does not own is supplied (the ownership join simply
    yields no rows). Each provided filter is combined with ``AND``:

    * ``priority`` -- exact match on the task's priority label.
    * ``status`` -- exact :class:`~app.data.enums.TaskStatus` match.
    * ``due_before`` / ``due_after`` -- inclusive bounds on ``due_date``; tasks
      with a null ``due_date`` are excluded when either bound is supplied.
    * ``requires_review`` -- match the review flag.
    * ``contract_id`` -- restrict to a single contract.

    Results are paginated (``limit`` capped at :data:`_MAX_LIMIT`) and ordered
    deadline-first: dated tasks ascending, undated tasks last, ``id`` as a
    stable tiebreaker.
    """

    stmt = tasks_for_user(current_user.id)

    if priority is not None:
        stmt = stmt.where(Task.priority == priority)
    if status_filter is not None:
        stmt = stmt.where(Task.status == status_filter)
    if due_before is not None:
        stmt = stmt.where(Task.due_date.is_not(None)).where(
            Task.due_date <= due_before
        )
    if due_after is not None:
        stmt = stmt.where(Task.due_date.is_not(None)).where(
            Task.due_date >= due_after
        )
    if requires_review is not None:
        stmt = stmt.where(Task.requires_review == requires_review)
    if contract_id is not None:
        stmt = stmt.where(Task.contract_id == contract_id)

    # Deadline-first, deterministic ordering that works on both PostgreSQL and
    # SQLite without relying on dialect-specific NULLS LAST: the boolean
    # "is the due_date null?" sorts False (0, dated) before True (1, undated),
    # then ascending due_date, then id as a stable tiebreaker.
    stmt = stmt.order_by(
        Task.due_date.is_(None),
        Task.due_date.asc(),
        Task.id.asc(),
    )
    stmt = stmt.limit(limit).offset(offset)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _record_task_audit(
    session: DbSession,
    *,
    user_id: int,
    task: Task,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    """Append exactly one :class:`~app.data.models.AuditLog` row for a mutation (Requirement 7.4).

    ``PATCH /tasks/{id}`` builds a ``before``/``after`` snapshot containing only
    the fields that actually changed (already JSON-safe via :func:`_json_safe`)
    and hands them to this hook together with the owning ``user_id``, the
    ``entity``/``entity_id`` (``"Task"`` / ``task.id``) and the ``action``.

    The new ``AuditLog`` is ``session.add``-ed (not committed) so it enlists in
    the *same* transaction as the mutation: the caller's ``session.commit()``
    persists the task change and its audit row atomically. The caller only
    invokes this hook when ``before``/``after`` is non-empty, so a no-op
    mutation never produces an audit row.
    """

    session.add(
        AuditLog(
            user_id=user_id,
            entity="Task",
            entity_id=task.id,
            action=action,
            before=before,
            after=after,
        )
    )


@router.patch("/tasks/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    current_user: CurrentUser,
    session: DbSession,
) -> Task:
    """Apply a status change and/or field corrections to an owned Task (Requirement 7).

    Ownership is enforced via :func:`~app.data.ownership.require_owned`, so a
    missing or non-owned ``task_id`` yields a uniform 404 and the row is never
    read or mutated (Requirement 2.2).

    Status (Requirements 7.1/7.2): because :class:`~app.web.schemas.TaskUpdate`
    types ``status`` as :class:`~app.data.enums.TaskStatus`, an invalid value is
    rejected by request validation (HTTP 422) *before* this handler runs, so the
    Task is left entirely unchanged on a bad status -- there is no partial
    application. A valid, present ``status`` is applied here.

    Field corrections (Requirement 7.3): any present correctable field whose
    value actually differs from the stored value is recorded in
    ``corrected_fields`` as ``{field: {"old": ..., "new": ...}}`` (merged with
    any prior corrections) and ``is_user_corrected`` is set to ``True``.

    A ``before``/``after`` snapshot of exactly the changed fields is assembled
    and handed to :func:`_record_task_audit` (the audit-log seam for task 12.2).
    """

    task = await require_owned(session, Task, task_id, current_user.id)

    # Only the fields explicitly present in the request body are considered;
    # omitting a field leaves it untouched.
    provided = payload.model_dump(exclude_unset=True)

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    # --- Status change (Requirement 7.1) -------------------------------------
    # The value is already a valid TaskStatus (validation guarantees it); apply
    # it only when it actually differs from the current status.
    if "status" in provided:
        new_status = provided["status"]
        if new_status != task.status:
            before["status"] = _json_safe(task.status)
            after["status"] = _json_safe(new_status)
            task.status = new_status

    # --- Field corrections (Requirement 7.3) ---------------------------------
    corrections: dict[str, Any] = dict(task.corrected_fields or {})
    corrected_any = False
    for field in _CORRECTABLE_FIELDS:
        if field not in provided:
            continue
        new_value = provided[field]
        old_value = getattr(task, field)
        if new_value == old_value:
            continue

        corrections[field] = {
            "old": _json_safe(old_value),
            "new": _json_safe(new_value),
        }
        before[field] = _json_safe(old_value)
        after[field] = _json_safe(new_value)
        setattr(task, field, new_value)
        corrected_any = True

    if corrected_any:
        # Reassign (not mutate-in-place) so SQLAlchemy detects the JSON change.
        task.corrected_fields = corrections
        task.is_user_corrected = True

    # Surface the changed-field snapshot to the audit-log seam (task 12.2).
    if before or after:
        await _record_task_audit(
            session,
            user_id=current_user.id,
            task=task,
            action="task.update",
            before=before,
            after=after,
        )

    await session.commit()
    await session.refresh(task)
    return task


__all__ = ["router"]
