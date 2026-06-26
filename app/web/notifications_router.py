"""In-app notification HTTP endpoints (design "Component 6").

This module serves the owner-scoped notification views (task 13.1). For the MVP
the platform delivers in-app notifications only (email is deferred), so these
endpoints are how a user sees and acknowledges the messages the Reminder
Scheduler (task 13.2) produces:

* ``GET /notifications`` -- return the authenticated owner's notifications,
  newest-first, with ``limit``/``offset`` pagination (Requirements 9.4, 2.1).
* ``PATCH /notifications/{id}`` -- mark a notification read (``read = true``)
  and return the updated row. Ownership is enforced so a missing or non-owned
  id returns a uniform 404 (Requirement 2.1).

Notifications carry a direct ``user_id`` column, so every query is scoped to the
caller through :func:`~app.data.ownership.notifications_for_user`, and the
single-row mutation goes through :func:`~app.data.ownership.require_owned`,
which never reads or mutates a row the caller does not own. The router lives in
its own module and is mounted by :func:`app.web.main.create_app`.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Body, Path, Query

from app.data.models import Notification
from app.data.ownership import notifications_for_user, require_owned
from app.web.dependencies import CurrentUser, DbSession
from app.web.schemas import NotificationRead, NotificationUpdate

# Pagination guards for the list endpoint.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=list[NotificationRead])
async def list_notifications(
    current_user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Notification]:
    """List the authenticated owner's notifications, newest-first (Requirements 9.4, 2.1).

    The query is scoped to the caller via
    :func:`~app.data.ownership.notifications_for_user`, so a notification
    belonging to another user is never returned. Results are ordered newest
    first (``created_at`` descending, ``id`` descending as a stable tiebreaker
    for rows sharing a timestamp) and paginated with ``limit``/``offset``
    (``limit`` capped at :data:`_MAX_LIMIT`).
    """

    stmt = (
        notifications_for_user(current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.patch("/notifications/{notification_id}", response_model=NotificationRead)
async def mark_notification_read(
    current_user: CurrentUser,
    session: DbSession,
    notification_id: Annotated[int, Path(ge=1)],
    payload: Annotated[Optional[NotificationUpdate], Body()] = None,
) -> Notification:
    """Mark a notification read and return the updated row (Requirements 9.4, 2.1).

    Ownership is enforced through :func:`~app.data.ownership.require_owned`,
    which raises :class:`~app.data.ownership.OwnershipError` (mapped to a uniform
    404 by the app) when the id does not exist or belongs to another user, so a
    non-owned row is never read or mutated. The request body is optional; when
    omitted the notification is marked read (``read = true``), and an explicit
    body ``{"read": false}`` may be used to clear the flag.
    """

    notification = await require_owned(
        session, Notification, notification_id, current_user.id
    )
    notification.read = payload.read if payload is not None else True
    await session.commit()
    await session.refresh(notification)
    return notification


__all__ = ["router"]
