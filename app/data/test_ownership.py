"""Light unit checks for user-scoped query helpers and ownership guards (task 5.1).

These are example-based smoke tests; the universal property test for ownership
scoping across arbitrary multi-user datasets is the separate task 5.2.

A throwaway in-memory async SQLite database (via ``aiosqlite``) is used so the
async helpers/guards run without a live PostgreSQL instance. ``asyncio.run`` is
used to drive the coroutines so the suite needs no pytest-asyncio plugin.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.data.database import Base
from app.data.enums import ContractStatus
from app.data.models import Clause, Contract, Notification, Reminder, Task, User
from app.data.ownership import (
    OwnershipError,
    clauses_for_user,
    contracts_for_user,
    get_owned_or_none,
    notifications_for_user,
    reminders_for_user,
    require_owned,
    tasks_for_user,
)


async def _seed(session: AsyncSession):
    """Create two users, each with a full contract -> task -> reminder chain."""

    alice = User(email="alice@example.com", password_hash="x")
    bob = User(email="bob@example.com", password_hash="x")
    session.add_all([alice, bob])
    await session.flush()

    def chain(user: User, key: str):
        contract = Contract(user_id=user.id, filename="c.pdf", file_key=key)
        session.add(contract)
        return contract

    a_contract = chain(alice, "key-a")
    b_contract = chain(bob, "key-b")
    await session.flush()

    a_clause = Clause(contract_id=a_contract.id, clause_index=0, body_text="x")
    b_clause = Clause(contract_id=b_contract.id, clause_index=0, body_text="y")
    session.add_all([a_clause, b_clause])
    await session.flush()

    a_task = Task(contract_id=a_contract.id, title="A task", source_text="x")
    b_task = Task(contract_id=b_contract.id, title="B task", source_text="y")
    session.add_all([a_task, b_task])
    await session.flush()

    a_reminder = Reminder(task_id=a_task.id, remind_at=datetime.now(timezone.utc))
    b_reminder = Reminder(task_id=b_task.id, remind_at=datetime.now(timezone.utc))
    a_notif = Notification(user_id=alice.id, message="hi a")
    b_notif = Notification(user_id=bob.id, message="hi b")
    session.add_all([a_reminder, b_reminder, a_notif, b_notif])
    await session.flush()

    return {
        "alice": alice,
        "bob": bob,
        "a_contract": a_contract,
        "b_contract": b_contract,
        "a_clause": a_clause,
        "b_clause": b_clause,
        "a_task": a_task,
        "b_task": b_task,
        "a_reminder": a_reminder,
        "b_reminder": b_reminder,
        "a_notif": a_notif,
        "b_notif": b_notif,
    }


def _run(coro_factory):
    """Run a coroutine factory against a fresh in-memory async SQLite session."""

    async def runner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                data = await _seed(session)
                return await coro_factory(session, data)
        finally:
            await engine.dispose()

    return asyncio.run(runner())


def test_scoped_selects_return_only_owned_rows():
    async def check(session, data):
        alice_id = data["alice"].id
        # Direct ownership.
        contracts = (await session.execute(contracts_for_user(alice_id))).scalars().all()
        assert {c.id for c in contracts} == {data["a_contract"].id}
        notifs = (await session.execute(notifications_for_user(alice_id))).scalars().all()
        assert {n.id for n in notifs} == {data["a_notif"].id}
        # Transitive ownership.
        clauses = (await session.execute(clauses_for_user(alice_id))).scalars().all()
        assert {c.id for c in clauses} == {data["a_clause"].id}
        tasks = (await session.execute(tasks_for_user(alice_id))).scalars().all()
        assert {t.id for t in tasks} == {data["a_task"].id}
        reminders = (await session.execute(reminders_for_user(alice_id))).scalars().all()
        assert {r.id for r in reminders} == {data["a_reminder"].id}

    _run(check)


def test_get_owned_returns_entity_when_owned():
    async def check(session, data):
        alice_id = data["alice"].id
        task = await get_owned_or_none(session, Task, data["a_task"].id, alice_id)
        assert task is not None and task.id == data["a_task"].id

    _run(check)


def test_get_owned_returns_none_for_non_owned_or_missing():
    async def check(session, data):
        alice_id = data["alice"].id
        # Non-owned (Bob's task) -> None, no leak.
        assert await get_owned_or_none(session, Task, data["b_task"].id, alice_id) is None
        # Missing id -> None.
        assert await get_owned_or_none(session, Contract, 999999, alice_id) is None

    _run(check)


def test_require_owned_raises_for_non_owned():
    async def check(session, data):
        alice_id = data["alice"].id
        with pytest.raises(OwnershipError):
            await require_owned(session, Contract, data["b_contract"].id, alice_id)
        with pytest.raises(OwnershipError):
            await require_owned(session, Notification, data["b_notif"].id, alice_id)
        # Owned reference succeeds.
        got = await require_owned(session, Contract, data["a_contract"].id, alice_id)
        assert got.id == data["a_contract"].id

    _run(check)


def test_require_owned_rejects_unknown_model():
    async def check(session, data):
        with pytest.raises(TypeError):
            await require_owned(session, User, data["alice"].id, data["alice"].id)

    _run(check)
