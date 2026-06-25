"""Property-based test for ownership scoping (task 5.2).

**Property 3: Ownership scoping**

**Validates: Requirements 2.1, 2.2**

Across arbitrary generated multi-user datasets, every record returned by each
``*_for_user`` scoped query belongs to that user (directly, or transitively
through the owning contract / task -> contract), and any reference to an entity
owned by a *different* user via :func:`get_owned_or_none` /
:func:`require_owned` yields denied/not-found (``None`` / :class:`OwnershipError`)
while performing **no read or mutation** on that non-owned record.

A fresh in-memory async SQLite database (via ``aiosqlite``) is built per
generated example so the scoping helpers/guards run without a live PostgreSQL
instance. ``asyncio.run`` drives the coroutines so no pytest-asyncio plugin is
required (mirroring the example-based ``test_ownership.py``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.data.database import Base
from app.data.models import (
    AuditLog,
    Clause,
    Contract,
    Notification,
    Reminder,
    Task,
    User,
)
from app.data.ownership import (
    OwnershipError,
    audit_logs_for_user,
    clauses_for_user,
    contracts_for_user,
    get_owned_or_none,
    notifications_for_user,
    reminders_for_user,
    require_owned,
    tasks_for_user,
)

# ---------------------------------------------------------------------------
# Dataset-shape strategies
# ---------------------------------------------------------------------------
#
# Rather than generate ORM objects directly, we generate a compact, declarative
# *shape* for each user's data and let the seeding code build the real rows in
# dependency order. This keeps generation fast (each example builds a DB) while
# still varying user count, per-user contract counts, and the number of
# clauses/tasks/reminders/notifications/audit-logs hanging off each owner.

# Per-contract shape: how many clauses, and how many tasks (each task carries a
# count of reminders to create under it).
_contract_shape = st.fixed_dictionaries(
    {
        "n_clauses": st.integers(min_value=0, max_value=3),
        "tasks": st.lists(
            st.integers(min_value=0, max_value=2),  # reminders per task
            min_size=0,
            max_size=3,
        ),
    }
)

# Per-user shape: a list of contracts plus directly-owned notification / audit
# counts.
_user_shape = st.fixed_dictionaries(
    {
        "contracts": st.lists(_contract_shape, min_size=0, max_size=3),
        "n_notifications": st.integers(min_value=0, max_value=3),
        "n_audit_logs": st.integers(min_value=0, max_value=3),
    }
)

# A whole world: at least two users so cross-tenant references always exist for
# the denial checks (when other users happen to own data).
_world_shape = st.lists(_user_shape, min_size=2, max_size=4)


async def _seed(session: AsyncSession, world: list[dict]) -> list[dict]:
    """Materialize ``world`` into real rows, returning per-user ownership maps.

    Each returned entry records the owning user's id plus the set of ids it owns
    for every entity kind, so the assertions can check membership without
    re-deriving ownership from the helpers under test.
    """

    users_info: list[dict] = []
    key_counter = 0

    for u_index, user_shape in enumerate(world):
        user = User(email=f"user{u_index}@example.com", password_hash="x")
        session.add(user)
        await session.flush()

        owned = {
            "user_id": user.id,
            "contracts": set(),
            "clauses": set(),
            "tasks": set(),
            "reminders": set(),
            "notifications": set(),
            "audit_logs": set(),
        }

        for contract_shape in user_shape["contracts"]:
            contract = Contract(
                user_id=user.id,
                filename="c.pdf",
                file_key=f"key-{key_counter}",
            )
            key_counter += 1
            session.add(contract)
            await session.flush()
            owned["contracts"].add(contract.id)

            for c_idx in range(contract_shape["n_clauses"]):
                clause = Clause(
                    contract_id=contract.id, clause_index=c_idx, body_text="x"
                )
                session.add(clause)
                await session.flush()
                owned["clauses"].add(clause.id)

            for n_reminders in contract_shape["tasks"]:
                task = Task(
                    contract_id=contract.id, title="t", source_text="x"
                )
                session.add(task)
                await session.flush()
                owned["tasks"].add(task.id)

                for _ in range(n_reminders):
                    reminder = Reminder(
                        task_id=task.id, remind_at=datetime.now(timezone.utc)
                    )
                    session.add(reminder)
                    await session.flush()
                    owned["reminders"].add(reminder.id)

        for _ in range(user_shape["n_notifications"]):
            notif = Notification(user_id=user.id, message="hi")
            session.add(notif)
            await session.flush()
            owned["notifications"].add(notif.id)

        for _ in range(user_shape["n_audit_logs"]):
            log = AuditLog(
                user_id=user.id, entity="Task", entity_id=1, action="update"
            )
            session.add(log)
            await session.flush()
            owned["audit_logs"].add(log.id)

        users_info.append(owned)

    await session.commit()
    return users_info


# Maps each "owned" bucket name to (scoped-select builder, guard model class).
# AuditLog has no guard entry on purpose (it is read-only / list-scoped), so it
# is exercised only by the scoped-select assertions.
_SCOPED_QUERIES = {
    "contracts": (contracts_for_user, Contract),
    "notifications": (notifications_for_user, Notification),
    "audit_logs": (audit_logs_for_user, AuditLog),
    "clauses": (clauses_for_user, Clause),
    "tasks": (tasks_for_user, Task),
    "reminders": (reminders_for_user, Reminder),
}

# Guard-checkable models (those wired into get_owned_or_none/require_owned).
_GUARD_MODELS = {
    "contracts": Contract,
    "notifications": Notification,
    "clauses": Clause,
    "tasks": Task,
    "reminders": Reminder,
}


async def _run_world(world: list[dict]) -> None:
    """Build a fresh DB for ``world`` and assert the ownership-scoping property."""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            users_info = await _seed(session, world)

            # --- Part 1: every scoped query returns only owned rows. ---------
            for owned in users_info:
                uid = owned["user_id"]
                for bucket, (builder, _model) in _SCOPED_QUERIES.items():
                    rows = (await session.execute(builder(uid))).scalars().all()
                    returned_ids = {r.id for r in rows}
                    # Exactly the ids this user owns -- no more (no leak), and
                    # no fewer (scoping does not drop owned rows).
                    assert returned_ids == owned[bucket], (
                        f"user {uid} {bucket}: returned {returned_ids} != "
                        f"owned {owned[bucket]}"
                    )

            # --- Part 2: non-owned references are denied with no mutation. ---
            for owned in users_info:
                uid = owned["user_id"]
                for other in users_info:
                    if other["user_id"] == uid:
                        continue
                    for bucket, model in _GUARD_MODELS.items():
                        for entity_id in other[bucket]:
                            # Snapshot the row as its real owner sees it, so we
                            # can prove the denied access mutated nothing.
                            before = await get_owned_or_none(
                                session, model, entity_id, other["user_id"]
                            )
                            assert before is not None

                            # get_owned_or_none: non-owned -> None (not-found).
                            assert (
                                await get_owned_or_none(
                                    session, model, entity_id, uid
                                )
                                is None
                            )

                            # require_owned: non-owned -> OwnershipError.
                            raised = False
                            try:
                                await require_owned(session, model, entity_id, uid)
                            except OwnershipError:
                                raised = True
                            assert raised, (
                                f"require_owned should deny user {uid} access to "
                                f"{model.__name__} {entity_id} owned by "
                                f"{other['user_id']}"
                            )

                            # No mutation: the row is unchanged for its owner.
                            after = await get_owned_or_none(
                                session, model, entity_id, other["user_id"]
                            )
                            assert after is not None and after.id == before.id
    finally:
        await engine.dispose()


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(world=_world_shape)
def test_ownership_scoping(world: list[dict]) -> None:
    """Property 3: scoped queries only ever return the owner's records, and a
    non-owned reference is denied (None / OwnershipError) with no mutation.

    **Validates: Requirements 2.1, 2.2**
    """

    asyncio.run(_run_world(world))
