"""Unit tests for the periodic reminder scan (task 13.2).

These exercise :func:`app.processing.reminders.scan_and_deliver` against a real
async SQLite database (driven via ``asyncio.run``), seeding reminders that are
due/not-due and sent/unsent across multiple users. They assert that:

* every due + unsent reminder produces exactly one in-app Notification for its
  owner and is marked ``sent`` with a populated ``sent_at`` (Req 9.1, 9.2);
* not-yet-due and already-sent reminders are skipped (Req 9.1, 9.3);
* running the scan twice does not double-deliver (single delivery / Req 9.3);
* notifications are scoped to the correct owning user (resolved transitively via
  reminder -> task -> contract -> user_id).

A single temp-file SQLite database is used per test so that the NullPool-created
connections all see the same schema/rows within one event loop.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.data.database import Base
from app.data.enums import ReminderChannel
from app.data.models import Contract, Notification, Reminder, Task, User
from app.processing.reminders import (
    NOTIFICATION_TYPE,
    REMINDER_BEAT_SCHEDULE_NAME,
    REMINDER_SCAN_INTERVAL_SECONDS,
    REMINDER_TASK_NAME,
    scan_and_deliver,
)
from app.processing.celery_app import DEFAULT_QUEUE, celery_app

NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Database harness
# ---------------------------------------------------------------------------


async def _make_factory(url: str) -> async_sessionmaker[AsyncSession]:
    """Create the schema on ``url`` and return a session factory bound to it."""

    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def _seed_user_contract(
    session: AsyncSession, *, email: str
) -> tuple[int, int]:
    """Create a user and one contract; return ``(user_id, contract_id)``."""

    user = User(email=email, password_hash="x")
    session.add(user)
    await session.flush()

    contract = Contract(
        user_id=user.id,
        filename=f"{email}.pdf",
        file_key=f"key-{email}",
    )
    session.add(contract)
    await session.flush()
    return user.id, contract.id


async def _add_task(session: AsyncSession, contract_id: int, title: str) -> int:
    """Create a Task under ``contract_id``; return its id."""

    task = Task(contract_id=contract_id, title=title, source_text="")
    session.add(task)
    await session.flush()
    return task.id


async def _add_reminder(
    session: AsyncSession,
    task_id: int,
    *,
    remind_at: datetime,
    sent: bool = False,
) -> int:
    """Create a Reminder; mark it sent (with sent_at) when ``sent`` is True."""

    reminder = Reminder(
        task_id=task_id,
        remind_at=remind_at,
        channel=ReminderChannel.IN_APP,
        sent=sent,
        sent_at=remind_at if sent else None,
    )
    session.add(reminder)
    await session.flush()
    return reminder.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _temp_db_url() -> tuple[str, str]:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    return f"sqlite+aiosqlite:///{path}", path


def test_delivers_due_unsent_reminders_one_notification_each() -> None:
    """Due + unsent reminders each yield exactly one Notification, marked sent."""

    url, path = _temp_db_url()

    async def _run() -> None:
        factory = await _make_factory(url)
        async with factory() as session:
            user_id, contract_id = await _seed_user_contract(
                session, email="owner@example.com"
            )
            t1 = await _add_task(session, contract_id, "Deliver report")
            t2 = await _add_task(session, contract_id, "Renew license")
            # Two due, unsent reminders (one at, one before "now").
            r1 = await _add_reminder(session, t1, remind_at=NOW - timedelta(hours=1))
            r2 = await _add_reminder(session, t2, remind_at=NOW)
            await session.commit()

        async with factory() as session:
            delivered = await scan_and_deliver(session, now=NOW)
            assert delivered == 2

        async with factory() as session:
            notes = (await session.execute(select(Notification))).scalars().all()
            assert len(notes) == 2
            assert {n.user_id for n in notes} == {user_id}
            assert all(n.type == NOTIFICATION_TYPE for n in notes)
            assert {n.task_id for n in notes} == {t1, t2}
            assert all(n.message for n in notes)

            reminders = (await session.execute(select(Reminder))).scalars().all()
            assert all(r.sent is True for r in reminders)
            assert all(r.sent_at is not None for r in reminders)

    try:
        asyncio.run(_run())
    finally:
        os.unlink(path)


def test_skips_not_due_and_already_sent() -> None:
    """Future reminders and already-sent reminders are never delivered."""

    url, path = _temp_db_url()

    async def _run() -> None:
        factory = await _make_factory(url)
        async with factory() as session:
            _user_id, contract_id = await _seed_user_contract(
                session, email="owner@example.com"
            )
            t = await _add_task(session, contract_id, "Some obligation")
            due = await _add_reminder(session, t, remind_at=NOW - timedelta(minutes=5))
            future = await _add_reminder(session, t, remind_at=NOW + timedelta(days=1))
            already = await _add_reminder(
                session, t, remind_at=NOW - timedelta(days=1), sent=True
            )
            await session.commit()

        async with factory() as session:
            delivered = await scan_and_deliver(session, now=NOW)
            # Only the single due+unsent reminder is delivered.
            assert delivered == 1

        async with factory() as session:
            notes = (await session.execute(select(Notification))).scalars().all()
            assert len(notes) == 1

            reminders = {
                r.id: r
                for r in (await session.execute(select(Reminder))).scalars().all()
            }
            # Due one is now sent.
            assert reminders[due].sent is True
            assert reminders[due].sent_at is not None
            # Future one untouched.
            assert reminders[future].sent is False
            assert reminders[future].sent_at is None
            # Already-sent one keeps its original sent_at (not re-stamped here).
            assert reminders[already].sent is True

    try:
        asyncio.run(_run())
    finally:
        os.unlink(path)


def test_running_scan_twice_does_not_double_deliver() -> None:
    """Idempotence: a second scan over the same data delivers nothing more."""

    url, path = _temp_db_url()

    async def _run() -> None:
        factory = await _make_factory(url)
        async with factory() as session:
            _user_id, contract_id = await _seed_user_contract(
                session, email="owner@example.com"
            )
            t = await _add_task(session, contract_id, "Recurring obligation")
            await _add_reminder(session, t, remind_at=NOW - timedelta(hours=2))
            await _add_reminder(session, t, remind_at=NOW - timedelta(hours=1))
            await session.commit()

        async with factory() as session:
            first = await scan_and_deliver(session, now=NOW)
            assert first == 2

        # Second scan at a later time: the reminders are already sent, so they
        # must not be re-selected or re-delivered.
        async with factory() as session:
            second = await scan_and_deliver(session, now=NOW + timedelta(hours=1))
            assert second == 0

        async with factory() as session:
            count = (
                await session.execute(select(func.count()).select_from(Notification))
            ).scalar_one()
            assert count == 2

    try:
        asyncio.run(_run())
    finally:
        os.unlink(path)


def test_owner_scoping_across_users() -> None:
    """Each notification goes to the owning user of its reminder's contract."""

    url, path = _temp_db_url()

    async def _run() -> None:
        factory = await _make_factory(url)
        async with factory() as session:
            alice_id, alice_contract = await _seed_user_contract(
                session, email="alice@example.com"
            )
            bob_id, bob_contract = await _seed_user_contract(
                session, email="bob@example.com"
            )
            a_task = await _add_task(session, alice_contract, "Alice task")
            b_task = await _add_task(session, bob_contract, "Bob task")
            await _add_reminder(session, a_task, remind_at=NOW - timedelta(minutes=1))
            await _add_reminder(session, b_task, remind_at=NOW - timedelta(minutes=1))
            await session.commit()

        async with factory() as session:
            delivered = await scan_and_deliver(session, now=NOW)
            assert delivered == 2

        async with factory() as session:
            notes = (await session.execute(select(Notification))).scalars().all()
            owner_by_task = {n.task_id: n.user_id for n in notes}
            assert owner_by_task[a_task] == alice_id
            assert owner_by_task[b_task] == bob_id

    try:
        asyncio.run(_run())
    finally:
        os.unlink(path)


def test_empty_scan_delivers_nothing() -> None:
    """A scan with no due+unsent reminders delivers nothing and commits cleanly."""

    url, path = _temp_db_url()

    async def _run() -> None:
        factory = await _make_factory(url)
        async with factory() as session:
            delivered = await scan_and_deliver(session, now=NOW)
            assert delivered == 0

    try:
        asyncio.run(_run())
    finally:
        os.unlink(path)


def test_beat_schedule_registered_on_default_queue() -> None:
    """The reminder scan is registered on Beat (~15 min) routed to default."""

    entry = celery_app.conf.beat_schedule[REMINDER_BEAT_SCHEDULE_NAME]
    assert entry["task"] == REMINDER_TASK_NAME
    assert entry["schedule"] == REMINDER_SCAN_INTERVAL_SECONDS
    assert entry["options"]["queue"] == DEFAULT_QUEUE
    # The task name is NOT under the ML namespace, so it stays on the light queue.
    assert not REMINDER_TASK_NAME.startswith("app.processing.ml")
