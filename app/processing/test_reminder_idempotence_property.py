"""Property-based test for the reminder scan's single-delivery / idempotence.

**Property 5: Reminder single-delivery / idempotence**
**Validates: Requirements 9.2, 9.3**

Running :func:`app.processing.reminders.scan_and_deliver` any number of times
delivers each reminder *at most once*: once ``sent`` is true a reminder is never
re-selected, and each delivery sets ``sent = true`` with a populated ``sent_at``.

Strategy
--------
For each example we generate an arbitrary set of reminders spread across one or
more users. Each generated reminder is independently:

* **due** (``remind_at <= now``) or **not due** (``remind_at > now``), and
* initially **sent** or **unsent**.

The reminders are seeded into a fresh in-memory async SQLite database (one DB
per example, driven via ``asyncio.run`` and a single shared connection so the
schema/rows are visible across sessions). We then run the scan a *random*
number of consecutive times (1..4) at the same pinned ``now`` and assert the
idempotence invariants below.

The decisive invariant (idempotence) is that the **total** number of
Notifications created equals the number of reminders that were *due AND
initially unsent* — no matter how many times the scan ran.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.data.database import Base
from app.data.enums import ReminderChannel
from app.data.models import Contract, Notification, Reminder, Task, User
from app.processing.reminders import scan_and_deliver

# A fixed reference "now" used both to classify generated reminders as due /
# not-due and as the pinned scan time so the test is fully deterministic.
NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# A single reminder spec: which user owns it, whether it is due at NOW, and
# whether it starts out already sent.
reminder_spec = st.fixed_dictionaries(
    {
        "user": st.integers(min_value=0, max_value=2),
        "due": st.booleans(),
        "sent": st.booleans(),
    }
)

# A scenario: a non-empty-ish list of reminder specs (allow empty to exercise
# the no-op scan) plus how many consecutive scans to run.
scenarios = st.fixed_dictionaries(
    {
        "reminders": st.lists(reminder_spec, min_size=0, max_size=8),
        "scan_count": st.integers(min_value=1, max_value=4),
    }
)


# ---------------------------------------------------------------------------
# DB harness (fresh in-memory DB per example)
# ---------------------------------------------------------------------------


async def _make_factory() -> async_sessionmaker[AsyncSession]:
    """Create a fresh in-memory SQLite DB and return a bound session factory.

    A ``StaticPool`` over a single in-memory connection keeps every session in
    the example talking to the same database (in-memory SQLite is per-connection
    otherwise).
    """

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def _seed(session: AsyncSession, specs: list[dict]) -> list[dict]:
    """Seed users/contracts/tasks/reminders from ``specs``.

    Returns an enriched copy of each spec augmented with the created
    ``reminder_id`` and ``task_id`` so assertions can be made per reminder.
    """

    # Create one user + contract per distinct user index that actually appears.
    user_indices = sorted({s["user"] for s in specs})
    contract_by_user: dict[int, int] = {}
    for idx in user_indices:
        user = User(email=f"user{idx}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        contract = Contract(
            user_id=user.id,
            filename=f"c{idx}.pdf",
            file_key=f"key-{idx}",
        )
        session.add(contract)
        await session.flush()
        contract_by_user[idx] = contract.id

    enriched: list[dict] = []
    for i, spec in enumerate(specs):
        task = Task(
            contract_id=contract_by_user[spec["user"]],
            title=f"task-{i}",
            source_text="",
        )
        session.add(task)
        await session.flush()

        # Due reminders sit at/just before NOW; not-due ones sit in the future.
        remind_at = (
            NOW - timedelta(hours=1) if spec["due"] else NOW + timedelta(days=1)
        )
        reminder = Reminder(
            task_id=task.id,
            remind_at=remind_at,
            channel=ReminderChannel.IN_APP,
            sent=spec["sent"],
            # sent_at populated iff already sent (satisfies the CHECK + invariant).
            sent_at=(NOW - timedelta(days=2)) if spec["sent"] else None,
        )
        session.add(reminder)
        await session.flush()

        enriched.append(
            {**spec, "reminder_id": reminder.id, "task_id": task.id}
        )

    await session.commit()
    return enriched


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(scenario=scenarios)
def test_reminder_scan_is_single_delivery_idempotent(scenario: dict) -> None:
    """Each reminder is delivered at most once across any number of scans.

    **Property 5: Reminder single-delivery / idempotence**
    **Validates: Requirements 9.2, 9.3**
    """

    specs = scenario["reminders"]
    scan_count = scenario["scan_count"]

    async def _run() -> None:
        factory = await _make_factory()

        async with factory() as session:
            enriched = await _seed(session, specs)

        # Reminders that SHOULD be delivered: due AND initially unsent.
        expected_delivered_ids = {
            r["reminder_id"]
            for r in enriched
            if r["due"] and not r["sent"]
        }
        expected_total = len(expected_delivered_ids)

        # Run the scan a random number of consecutive times at the same `now`.
        delivered_counts: list[int] = []
        for _ in range(scan_count):
            async with factory() as session:
                delivered_counts.append(
                    await scan_and_deliver(session, now=NOW)
                )

        # (a) The FIRST scan delivers exactly the due+unsent reminders...
        assert delivered_counts[0] == expected_total
        # ...and every subsequent scan delivers nothing (already sent).
        assert all(c == 0 for c in delivered_counts[1:])

        async with factory() as session:
            # (b) Idempotence: total notifications == due+unsent count, no matter
            # how many times we scanned.
            total_notifications = (
                await session.execute(
                    select(func.count()).select_from(Notification)
                )
            ).scalar_one()
            assert total_notifications == expected_total

            notes = (await session.execute(select(Notification))).scalars().all()
            # (c) Exactly one notification per delivered reminder's task, and
            # each goes to the owning user.
            reminders = {
                r.id: r
                for r in (
                    await session.execute(select(Reminder))
                ).scalars().all()
            }
            task_to_owner = {}
            for r in enriched:
                # Resolve owner via reminder -> task -> contract -> user_id.
                contract_id = (
                    await session.execute(
                        select(Task.contract_id).where(Task.id == r["task_id"])
                    )
                ).scalar_one()
                owner = (
                    await session.execute(
                        select(Contract.user_id).where(Contract.id == contract_id)
                    )
                ).scalar_one()
                task_to_owner[r["task_id"]] = owner

            delivered_task_ids = [
                r["task_id"]
                for r in enriched
                if r["reminder_id"] in expected_delivered_ids
            ]
            # One notification per delivered task; correctly owner-scoped.
            assert sorted(n.task_id for n in notes) == sorted(delivered_task_ids)
            for n in notes:
                assert n.user_id == task_to_owner[n.task_id]

            # (d) Per-reminder final state invariants.
            for r in enriched:
                reminder = reminders[r["reminder_id"]]
                if r["due"] and not r["sent"]:
                    # Delivered: now sent with a populated sent_at.
                    assert reminder.sent is True
                    assert reminder.sent_at is not None
                elif r["sent"]:
                    # Already sent: never re-delivered, keeps a populated sent_at.
                    assert reminder.sent is True
                    assert reminder.sent_at is not None
                else:
                    # Not due and unsent: untouched.
                    assert reminder.sent is False
                    assert reminder.sent_at is None

    asyncio.run(_run())
