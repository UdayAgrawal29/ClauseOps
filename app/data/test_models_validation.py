"""Tests for model-level validation rules (spec task 2.2).

These exercise both layers of enforcement:

* ORM-level ``@validates`` validators that raise ``ValueError`` on assignment.
* DB-level ``CheckConstraint``s that raise ``IntegrityError`` on flush.

A throwaway in-memory SQLite database is used so the constraints are exercised
without requiring a live PostgreSQL instance. The ``JSONB`` columns fall back to
generic ``JSON`` on SQLite (see ``JSONType`` in ``models``), and the enum domains
are stored as their string values, so the same CHECK constraints apply.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.database import Base
from app.data.enums import ContractStatus, ReminderChannel, TaskStatus
from app.data.models import Clause, Contract, Reminder, Task, User


@pytest.fixture()
def session():
    """A fresh in-memory SQLite session with all tables created."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    # Enforce CHECK / FK constraints in SQLite.
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    Base.metadata.drop_all(engine)
    engine.dispose()


def _make_user(session: Session, email: str = "owner@example.com") -> User:
    user = User(email=email, password_hash="argon2$dummy")
    session.add(user)
    session.commit()
    return user


def _make_contract(
    session: Session,
    user: User,
    *,
    status: ContractStatus = ContractStatus.PENDING,
    progress_pct: int = 0,
    error_message: str | None = None,
) -> Contract:
    contract = Contract(
        user_id=user.id,
        filename="c.pdf",
        file_key=f"key-{datetime.now(timezone.utc).timestamp()}",
        status=status,
        progress_pct=progress_pct,
        error_message=error_message,
    )
    session.add(contract)
    session.commit()
    return contract


def _make_task(session: Session, contract: Contract, **kwargs) -> Task:
    task = Task(contract_id=contract.id, title="t", **kwargs)
    session.add(task)
    session.commit()
    return task


# --------------------------------------------------------------------------- #
# users.email
# --------------------------------------------------------------------------- #
def test_valid_email_accepted(session):
    user = _make_user(session)
    assert user.id is not None


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_email_rejected_by_validator(session, bad):
    with pytest.raises(ValueError):
        User(email=bad, password_hash="h")


def test_duplicate_email_rejected_by_db(session):
    _make_user(session, "dup@example.com")
    session.add(User(email="dup@example.com", password_hash="h"))
    with pytest.raises(IntegrityError):
        session.commit()


# --------------------------------------------------------------------------- #
# contracts.progress_pct
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [0, 1, 50, 100])
def test_progress_pct_valid(session, value):
    user = _make_user(session)
    contract = _make_contract(session, user, progress_pct=value)
    assert contract.progress_pct == value


@pytest.mark.parametrize("value", [-1, 101, 1000])
def test_progress_pct_out_of_range_rejected_by_validator(session, value):
    user = _make_user(session)
    with pytest.raises(ValueError):
        Contract(user_id=user.id, filename="c.pdf", file_key="k", progress_pct=value)


def test_progress_pct_non_integer_rejected(session):
    user = _make_user(session)
    with pytest.raises(ValueError):
        Contract(user_id=user.id, filename="c.pdf", file_key="k", progress_pct=1.5)


# --------------------------------------------------------------------------- #
# contracts.error_message <-> status == FAILED
# --------------------------------------------------------------------------- #
def test_failed_with_error_message_ok(session):
    user = _make_user(session)
    contract = _make_contract(
        session, user, status=ContractStatus.FAILED, error_message="boom"
    )
    assert contract.error_message == "boom"


def test_error_message_without_failed_rejected_by_validator(session):
    user = _make_user(session)
    contract = Contract(
        user_id=user.id, filename="c.pdf", file_key="k", status=ContractStatus.COMPLETE
    )
    with pytest.raises(ValueError):
        contract.error_message = "should not be allowed"


def test_clearing_failed_status_with_message_present_rejected(session):
    user = _make_user(session)
    contract = _make_contract(
        session, user, status=ContractStatus.FAILED, error_message="boom"
    )
    with pytest.raises(ValueError):
        contract.status = ContractStatus.COMPLETE


def test_failed_without_error_message_rejected_by_db(session):
    """DB CHECK requires a message when FAILED (validator can't see the gap)."""

    user = _make_user(session)
    contract = Contract(
        user_id=user.id,
        filename="c.pdf",
        file_key="k-failed",
        status=ContractStatus.FAILED,
    )
    session.add(contract)
    with pytest.raises(IntegrityError):
        session.commit()


# --------------------------------------------------------------------------- #
# clauses.confidence / tasks.confidence / tasks.agent_score in [0, 1]
# --------------------------------------------------------------------------- #
def test_clause_confidence_valid(session):
    user = _make_user(session)
    contract = _make_contract(session, user)
    clause = Clause(contract_id=contract.id, clause_index=0, confidence=0.5)
    session.add(clause)
    session.commit()
    assert clause.confidence == 0.5


def test_clause_confidence_none_allowed(session):
    user = _make_user(session)
    contract = _make_contract(session, user)
    clause = Clause(contract_id=contract.id, clause_index=0, confidence=None)
    session.add(clause)
    session.commit()
    assert clause.confidence is None


@pytest.mark.parametrize("value", [-0.01, 1.01, 2.0, -5.0])
def test_clause_confidence_out_of_range_rejected(session, value):
    with pytest.raises(ValueError):
        Clause(contract_id=1, clause_index=0, confidence=value)


@pytest.mark.parametrize("field", ["confidence", "agent_score"])
@pytest.mark.parametrize("value", [-0.1, 1.1, 5.0])
def test_task_unit_interval_rejected(session, field, value):
    with pytest.raises(ValueError):
        Task(contract_id=1, title="t", **{field: value})


def test_task_unit_interval_valid(session):
    user = _make_user(session)
    contract = _make_contract(session, user)
    task = _make_task(session, contract, confidence=0.9, agent_score=0.1)
    assert task.confidence == 0.9
    assert task.agent_score == 0.1


# --------------------------------------------------------------------------- #
# reminders.channel
# --------------------------------------------------------------------------- #
def test_reminder_channel_valid(session):
    user = _make_user(session)
    contract = _make_contract(session, user)
    task = _make_task(session, contract)
    reminder = Reminder(
        task_id=task.id,
        remind_at=datetime.now(timezone.utc),
        channel=ReminderChannel.EMAIL,
    )
    session.add(reminder)
    session.commit()
    assert reminder.channel == ReminderChannel.EMAIL


def test_reminder_channel_invalid_rejected_by_validator(session):
    with pytest.raises(ValueError):
        Reminder(task_id=1, remind_at=datetime.now(timezone.utc), channel="sms")


# --------------------------------------------------------------------------- #
# reminders.sent_at only when sent is true
# --------------------------------------------------------------------------- #
def test_sent_at_with_sent_true_ok(session):
    user = _make_user(session)
    contract = _make_contract(session, user)
    task = _make_task(session, contract)
    reminder = Reminder(
        task_id=task.id,
        remind_at=datetime.now(timezone.utc),
        sent=True,
        sent_at=datetime.now(timezone.utc),
    )
    session.add(reminder)
    session.commit()
    assert reminder.sent_at is not None


def test_sent_at_without_sent_rejected_by_validator(session):
    with pytest.raises(ValueError):
        Reminder(
            task_id=1,
            remind_at=datetime.now(timezone.utc),
            sent=False,
            sent_at=datetime.now(timezone.utc),
        )


def test_clearing_sent_with_sent_at_present_rejected(session):
    user = _make_user(session)
    contract = _make_contract(session, user)
    task = _make_task(session, contract)
    reminder = Reminder(
        task_id=task.id,
        remind_at=datetime.now(timezone.utc),
        sent=True,
        sent_at=datetime.now(timezone.utc),
    )
    session.add(reminder)
    session.commit()
    with pytest.raises(ValueError):
        reminder.sent = False


# --------------------------------------------------------------------------- #
# Property-based: progress_pct and unit-interval ranges
# --------------------------------------------------------------------------- #
@settings(max_examples=100, deadline=None)
@given(value=st.integers(min_value=0, max_value=100))
def test_progress_pct_accepts_all_in_range(value):
    c = Contract(user_id=1, filename="c.pdf", file_key=f"k{value}", progress_pct=value)
    assert c.progress_pct == value


@settings(max_examples=100, deadline=None)
@given(value=st.integers().filter(lambda v: v < 0 or v > 100))
def test_progress_pct_rejects_all_out_of_range(value):
    with pytest.raises(ValueError):
        Contract(user_id=1, filename="c.pdf", file_key="k", progress_pct=value)


@settings(max_examples=100, deadline=None)
@given(value=st.floats(min_value=0.0, max_value=1.0))
def test_task_confidence_accepts_unit_interval(value):
    t = Task(contract_id=1, title="t", confidence=value)
    assert t.confidence == value
