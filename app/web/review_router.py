"""Review-queue HTTP endpoint (design "Component 6", Requirement 8.3).

This small, self-contained router serves the *review queue*: the authenticated
owner's tasks that the ML pipeline flagged as uncertain (``requires_review`` is
true) — for example an inferred party or an unresolved conditional/relative
deadline. The Grounded Viewer gives these items a distinct dashed/amber
treatment, so this endpoint is what the review UI consumes.

It is intentionally kept in its own module (rather than folded into the general
``GET /tasks`` task list) and exposed at the dedicated path ``GET /review-queue``
so it never collides with ``GET /tasks`` or ``GET /tasks/{id}``.

The query is scoped to the caller via
:func:`~app.data.ownership.tasks_for_user` (a join through ``contracts.user_id``)
plus a ``Task.requires_review IS TRUE`` filter, so only the owner's flagged
tasks are ever returned (Requirements 8.3, 2.1). Results are ordered
deterministically — tasks with a ``due_date`` first (soonest first), then the
date-less ones, with ``id`` as a stable tiebreaker — and paginated with
``limit``/``offset``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.data.models import Task
from app.data.ownership import tasks_for_user
from app.web.dependencies import CurrentUser, DbSession
from app.web.schemas import TaskRead

# Pagination guards, mirroring the contracts list endpoint.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

router = APIRouter(tags=["review"])


@router.get("/review-queue", response_model=list[TaskRead])
async def list_review_queue(
    current_user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Task]:
    """Return the owner's tasks flagged ``requires_review`` (Requirement 8.3).

    Scoped to the caller through
    :func:`~app.data.ownership.tasks_for_user`, then narrowed to
    ``requires_review`` tasks only, so another tenant's flagged tasks and the
    owner's own non-flagged tasks are both excluded. Ordering is deterministic:
    tasks carrying a ``due_date`` come first (soonest first, nulls last), with
    ``id`` as a stable tiebreaker; ``limit``/``offset`` paginate the result.
    """

    stmt = (
        tasks_for_user(current_user.id)
        .where(Task.requires_review.is_(True))
        # ``due_date IS NULL`` sorts False (0) before True (1), so dated tasks
        # come first; this expresses "nulls last" portably (incl. SQLite).
        .order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.id.asc())
        .limit(limit)
        .offset(offset)
    )

    result = await session.execute(stmt)
    return list(result.scalars().all())


__all__ = ["router"]
