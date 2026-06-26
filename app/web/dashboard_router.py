"""Dashboard summary and calendar HTTP endpoints (design "Component 6").

This module serves the read-only aggregate views that back the deadline-first
dashboard and the calendar (task 10.3):

* ``GET /dashboard/summary`` -- per-owner task counts grouped by priority and by
  status, the count of tasks flagged ``requires_review``, and a short
  "upcoming deadlines" list (the soonest tasks due on or after today)
  (Requirement 6.2).
* ``GET /calendar?from=&to=`` -- the owner's tasks whose ``due_date`` falls
  within the inclusive ``[from, to]`` window, ordered by ``due_date``
  (Requirement 6.3).

Every query is scoped to the authenticated user through
:func:`~app.data.ownership.tasks_for_user`, so a user only ever sees aggregates
and deadlines derived from their own tasks (Requirement 2.1). The endpoints live
in a dedicated router (kept separate from the task-list router) and are mounted
by :func:`app.web.main.create_app`.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.data.models import Task
from app.data.ownership import tasks_for_user
from app.web.dependencies import CurrentUser, DbSession
from app.web.schemas import DashboardSummary, DeadlineEntry, TaskRead

# Bucket label for tasks that carry no priority, so they still count toward the
# dashboard totals rather than being silently dropped.
_UNSET_PRIORITY = "UNSET"

# Bounds for how many upcoming deadlines the dashboard returns.
_DEFAULT_UPCOMING = 5
_MAX_UPCOMING = 50

router = APIRouter(tags=["dashboard"])


def _owned_task_ids(user_id: int):
    """A scalar subquery of the ids of tasks owned by ``user_id``.

    Reuses :func:`tasks_for_user` (Task joined to Contract scoped to the owner)
    so the aggregate queries below stay scoped through the single ownership
    helper rather than re-deriving the join.
    """

    return tasks_for_user(user_id).with_only_columns(Task.id).scalar_subquery()


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def dashboard_summary(
    current_user: CurrentUser,
    session: DbSession,
    upcoming_limit: Annotated[
        int,
        Query(
            ge=1,
            le=_MAX_UPCOMING,
            description="How many upcoming deadlines to return.",
        ),
    ] = _DEFAULT_UPCOMING,
) -> DashboardSummary:
    """Return the authenticated owner's aggregated dashboard summary (Requirement 6.2).

    Computes, scoped to the caller via :func:`tasks_for_user`:

    * counts of tasks grouped by priority (null priority bucketed as ``UNSET``);
    * counts of tasks grouped by status;
    * the number of tasks flagged ``requires_review``;
    * the next ``upcoming_limit`` tasks with a ``due_date`` on or after today,
      ordered ascending by ``due_date``.
    """

    owned_ids = _owned_task_ids(current_user.id)

    # Counts grouped by priority (null -> UNSET bucket).
    priority_rows = await session.execute(
        select(Task.priority, func.count(Task.id))
        .where(Task.id.in_(owned_ids))
        .group_by(Task.priority)
    )
    counts_by_priority: dict[str, int] = {}
    for priority, count in priority_rows.all():
        key = priority if priority is not None else _UNSET_PRIORITY
        counts_by_priority[key] = counts_by_priority.get(key, 0) + count

    # Counts grouped by status (status is always non-null).
    status_rows = await session.execute(
        select(Task.status, func.count(Task.id))
        .where(Task.id.in_(owned_ids))
        .group_by(Task.status)
    )
    counts_by_status: dict[str, int] = {
        task_status.value: count for task_status, count in status_rows.all()
    }

    # Count of tasks flagged for review.
    requires_review_count = await session.scalar(
        select(func.count(Task.id)).where(
            Task.id.in_(owned_ids), Task.requires_review.is_(True)
        )
    )

    # Upcoming deadlines: soonest tasks due today or later.
    today = date.today()
    upcoming_stmt = (
        tasks_for_user(current_user.id)
        .where(Task.due_date.is_not(None), Task.due_date >= today)
        .order_by(Task.due_date.asc(), Task.id.asc())
        .limit(upcoming_limit)
    )
    upcoming_rows = (await session.execute(upcoming_stmt)).scalars().all()
    upcoming_deadlines = [
        DeadlineEntry.model_validate(task) for task in upcoming_rows
    ]

    return DashboardSummary(
        counts_by_priority=counts_by_priority,
        counts_by_status=counts_by_status,
        requires_review_count=int(requires_review_count or 0),
        upcoming_deadlines=upcoming_deadlines,
    )


@router.get("/calendar", response_model=list[TaskRead])
async def calendar(
    current_user: CurrentUser,
    session: DbSession,
    from_date: Annotated[
        date, Query(alias="from", description="Inclusive window start date.")
    ],
    to_date: Annotated[
        date, Query(alias="to", description="Inclusive window end date.")
    ],
) -> list[Task]:
    """Return the owner's tasks due within the inclusive ``[from, to]`` window (Requirement 6.3).

    Both ``from`` and ``to`` are required dates. The window is inclusive on both
    ends, and an inverted window (``from`` after ``to``) is rejected with a 400
    rather than silently returning nothing. Only tasks with a non-null
    ``due_date`` inside the window are returned, scoped to the caller via
    :func:`tasks_for_user` and ordered by ``due_date`` (then id for stability).
    """

    if from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'from' date must be on or before 'to' date",
        )

    stmt = (
        tasks_for_user(current_user.id)
        .where(
            Task.due_date.is_not(None),
            Task.due_date >= from_date,
            Task.due_date <= to_date,
        )
        .order_by(Task.due_date.asc(), Task.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


__all__ = ["router"]
