"""Periodic reminder scan (Celery Beat) with guaranteed single delivery.

This module implements task 13.2 (design.md "Component 7: Reminder Scheduler
(Celery Beat)" and the "Reminder double-send" error-handling note). It provides:

* :func:`scan_and_deliver` — the pure, async core logic. Given an
  :class:`~sqlalchemy.ext.asyncio.AsyncSession`, it selects every reminder that
  is **due** (``remind_at <= now``) and **unsent** (``sent == False``), and for
  each one delivers an in-app :class:`~app.data.models.Notification` to the
  owning user (resolved transitively via ``reminder -> task -> contract ->
  user_id``), marks the reminder ``sent = True`` and stamps ``sent_at = now``.
  This function is unit-testable against an async SQLite database.

* :func:`run_reminder_scan` — a synchronous wrapper that runs the core logic via
  ``asyncio.run`` with a short-lived async engine per call, matching the
  DB-in-Celery convention used by :mod:`app.processing.ml`.

* :func:`scan_reminders` — the Celery task wrapper, registered on the **light**
  ``default`` queue (its name is *not* in the ``app.processing.ml.*`` namespace,
  so the ML routing rule never sends it to the heavy ``ml`` queue).

* A Celery Beat schedule entry (``celery_app.conf.beat_schedule``) that runs the
  scan roughly every 15 minutes on the ``default`` queue.

Single delivery / idempotence
------------------------------
Two mechanisms together guarantee each reminder is delivered **at most once**,
even if the scan runs repeatedly or two scans overlap:

1. The selection filters on ``sent == False`` so an already-sent reminder is
   never re-selected (Requirement 9.3).
2. Delivery uses a **guarded UPDATE** — ``UPDATE reminders SET sent=true,
   sent_at=now WHERE id=:id AND sent=false``. The accompanying Notification is
   created only when that UPDATE actually claimed the row (``rowcount == 1``).
   If a concurrent scan already claimed it, the UPDATE matches zero rows and no
   duplicate notification is produced. Setting ``sent`` and ``sent_at`` in the
   same statement also satisfies the ``sent_at IS NULL OR sent = true`` CHECK
   constraint.

In-app only: the MVP delivers in-app notifications exclusively; the email
channel is deliberately not touched here.

Import safety: importing this module wires Celery configuration only — it makes
no Redis or database connection. All DB access is deferred to call time inside
:func:`run_reminder_scan`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import get_settings
from app.data.enums import ReminderChannel
from app.data.models import Contract, Notification, Reminder, Task
from app.processing.celery_app import DEFAULT_QUEUE, celery_app

logger = logging.getLogger(__name__)

# How often Celery Beat runs the scan, in seconds (~15 minutes).
REMINDER_SCAN_INTERVAL_SECONDS: float = 15 * 60

# Unique Beat schedule key for the periodic scan.
REMINDER_BEAT_SCHEDULE_NAME = "scan-due-reminders"

# Fully-qualified Celery task name. It is intentionally NOT under the
# ``app.processing.ml.*`` namespace so the ML route rule leaves it on the light
# ``default`` queue.
REMINDER_TASK_NAME = "app.processing.reminders.scan_reminders"

# The ``type`` recorded on the in-app Notification rows we create. The reminder
# itself is delivered on the in-app channel (no email in the MVP).
NOTIFICATION_TYPE = "reminder"


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""

    return datetime.now(timezone.utc)


def _build_message(title: Optional[str], due_date) -> str:
    """Compose a human-readable reminder message that references the task."""

    name = (title or "").strip() or "an upcoming obligation"
    if due_date is not None:
        return f"Reminder: \u201c{name}\u201d is due on {due_date}."
    return f"Reminder: \u201c{name}\u201d is due soon."


async def scan_and_deliver(
    session: AsyncSession, *, now: Optional[datetime] = None
) -> int:
    """Deliver every due, unsent reminder exactly once within ``session``.

    Selects reminders where ``remind_at <= now`` and ``sent == False``, resolves
    each reminder's owning user via ``reminder -> task -> contract -> user_id``,
    and for each one atomically claims the reminder with a guarded UPDATE before
    creating the owner's in-app Notification. Already-sent and not-yet-due
    reminders are skipped (Requirements 9.1, 9.2, 9.3).

    Args:
        session: An async session bound to the target database.
        now: Optional reference time (timezone-aware). Defaults to ``utcnow()``.
            Injectable so tests can pin "the current time" deterministically.

    Returns:
        The number of reminders actually delivered in this run.
    """

    reference = now or _utcnow()

    # (1) Select due + unsent reminders, joined to their owner. Filtering on
    # ``sent == False`` means an already-sent reminder is never re-selected.
    candidates = (
        select(
            Reminder.id,
            Reminder.task_id,
            Contract.user_id,
            Task.title,
            Task.due_date,
        )
        .join(Task, Reminder.task_id == Task.id)
        .join(Contract, Task.contract_id == Contract.id)
        .where(Reminder.remind_at <= reference)
        .where(Reminder.sent.is_(False))
        .order_by(Reminder.id)
    )

    rows = (await session.execute(candidates)).all()

    delivered = 0
    for reminder_id, task_id, user_id, title, due_date in rows:
        # (2) Guarded UPDATE: claim the row only while it is still unsent. The
        # ``WHERE sent = false`` clause makes this safe against a concurrent
        # scan — at most one transaction observes ``rowcount == 1``. Setting
        # ``sent`` and ``sent_at`` together satisfies the table CHECK.
        guard = (
            update(Reminder)
            .where(Reminder.id == reminder_id)
            .where(Reminder.sent.is_(False))
            .values(sent=True, sent_at=reference)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(guard)

        if result.rowcount != 1:
            # Another scan already claimed this reminder; do not double-deliver.
            continue

        # Deliver in-app only (Requirement 9.2). The reminder's channel stays
        # IN_APP; we never send email here.
        session.add(
            Notification(
                user_id=user_id,
                task_id=task_id,
                type=NOTIFICATION_TYPE,
                message=_build_message(title, due_date),
            )
        )
        delivered += 1

    await session.commit()
    return delivered


async def _arun_reminder_scan() -> int:
    """Run :func:`scan_and_deliver` against a fresh short-lived async engine.

    Creates and disposes a dedicated engine so the connection pool is never
    shared across event loops (each Celery task gets its own ``asyncio.run``),
    mirroring :mod:`app.processing.ml`'s DB-in-Celery convention.
    """

    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await scan_and_deliver(session)
    finally:
        await engine.dispose()


def run_reminder_scan() -> int:
    """Synchronous entry point: deliver due reminders, return delivered count."""

    return asyncio.run(_arun_reminder_scan())


@celery_app.task(name=REMINDER_TASK_NAME)
def scan_reminders() -> int:
    """Celery task wrapper for the periodic reminder scan (``default`` queue).

    Returns the number of reminders delivered so the result is observable in the
    Celery result backend and in tests.
    """

    delivered = run_reminder_scan()
    logger.info("reminder scan delivered %d in-app notification(s)", delivered)
    return delivered


def register_beat_schedule() -> None:
    """Register the periodic reminder scan on Celery Beat (~ every 15 minutes).

    Routed explicitly to the light ``default`` queue. Registering the schedule
    only mutates in-memory Celery configuration; it does not connect to Redis or
    the database, so importing this module stays side-effect-free w.r.t. I/O.
    """

    schedule = dict(celery_app.conf.beat_schedule or {})
    schedule[REMINDER_BEAT_SCHEDULE_NAME] = {
        "task": REMINDER_TASK_NAME,
        "schedule": REMINDER_SCAN_INTERVAL_SECONDS,
        "options": {"queue": DEFAULT_QUEUE},
    }
    celery_app.conf.beat_schedule = schedule


# Wire the Beat schedule at import time. This is pure configuration (no I/O).
register_beat_schedule()


__all__ = [
    "scan_and_deliver",
    "run_reminder_scan",
    "scan_reminders",
    "register_beat_schedule",
    "REMINDER_SCAN_INTERVAL_SECONDS",
    "REMINDER_BEAT_SCHEDULE_NAME",
    "REMINDER_TASK_NAME",
    "NOTIFICATION_TYPE",
]
