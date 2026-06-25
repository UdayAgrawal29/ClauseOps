"""Property-based test for Property 9: Contract-status domain (spec task 2.4).

**Property 9: Contract-status domain**
**Validates: Requirements 4.3, 4.6, 4.7**

A Contract's ``status`` is always one of {PENDING, PROCESSING, COMPLETE, FAILED},
and ``error_message`` is non-null ONLY when ``status == FAILED`` (the biconditional
``error_message IS NOT NULL  <=>  status == FAILED``).

The biconditional is enforced at two layers, mirroring the existing
``test_models_validation.py`` pattern:

* ORM-level ``@validates`` validators raise ``ValueError`` on assignment when a
  non-null ``error_message`` is paired with a non-FAILED status.
* A DB-level ``CheckConstraint`` (``ck_contracts_error_message_requires_failed``)
  raises ``IntegrityError`` on flush when ``status == FAILED`` has no message.

A throwaway in-memory SQLite database exercises the constraints without a live
PostgreSQL instance; the enum domains are stored as their string values so the
same CHECK constraints apply.
"""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.database import Base
from app.data.enums import ContractStatus
from app.data.models import Contract, User

# The full, fixed status domain. Property 9 asserts a Contract's status is always
# drawn from exactly these four members.
STATUS_DOMAIN = {
    ContractStatus.PENDING,
    ContractStatus.PROCESSING,
    ContractStatus.COMPLETE,
    ContractStatus.FAILED,
}


def _fresh_session() -> tuple[Session, "object"]:
    """Create an isolated in-memory SQLite session with all tables created.

    A new engine per example keeps Hypothesis runs independent (function-scoped
    pytest fixtures are shared across @given examples, so we build per call).
    """

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine), engine


# Generates arbitrary status values from the enum and error_message values that
# are either None or a non-empty string, covering all four (status, message)
# quadrants: FAILED/message, FAILED/None, non-FAILED/None, non-FAILED/message.
status_strategy = st.sampled_from(list(ContractStatus))
error_message_strategy = st.one_of(
    st.none(),
    st.text(min_size=1, max_size=64),
)


@settings(max_examples=200, deadline=None)
@given(status=status_strategy, error_message=error_message_strategy)
def test_contract_status_domain_biconditional(status, error_message):
    """status is always in-domain; error_message is non-null iff status == FAILED.

    Valid combinations persist and satisfy the biconditional; invalid combinations
    are rejected (ValueError from the ORM validator or IntegrityError from the DB
    CHECK constraint) and never produce a persisted Contract that violates it.
    """

    # The generated status is, by construction, always a member of the domain.
    assert status in STATUS_DOMAIN

    failed = status == ContractStatus.FAILED
    has_message = error_message is not None
    # The biconditional that a valid Contract must satisfy.
    is_valid_combo = failed == has_message

    session, engine = _fresh_session()
    try:
        user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="h")
        session.add(user)
        session.commit()

        if is_valid_combo:
            # Valid: either (FAILED + message) or (non-FAILED + None).
            contract = Contract(
                user_id=user.id,
                filename="c.pdf",
                file_key=f"key-{uuid.uuid4().hex}",
                status=status,
                error_message=error_message,
            )
            session.add(contract)
            session.commit()
            session.refresh(contract)

            # Persisted invariants: status in domain and the biconditional holds.
            assert contract.status in STATUS_DOMAIN
            assert (contract.error_message is not None) == (
                contract.status == ContractStatus.FAILED
            )
        else:
            # Invalid: (non-FAILED + message) is caught by the @validates validator
            # at assignment time (ValueError); (FAILED + None) is caught by the DB
            # CHECK constraint on flush (IntegrityError).
            with pytest.raises((ValueError, IntegrityError)):
                contract = Contract(
                    user_id=user.id,
                    filename="c.pdf",
                    file_key=f"key-{uuid.uuid4().hex}",
                    status=status,
                    error_message=error_message,
                )
                session.add(contract)
                session.commit()
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@settings(max_examples=100, deadline=None)
@given(status=status_strategy)
def test_contract_status_always_in_domain(status):
    """Every constructed Contract's status is one of the four domain members."""

    contract = Contract(
        user_id=1,
        filename="c.pdf",
        file_key=f"key-{uuid.uuid4().hex}",
        status=status,
        error_message="boom" if status == ContractStatus.FAILED else None,
    )
    assert contract.status in STATUS_DOMAIN
    assert contract.status.value in {"PENDING", "PROCESSING", "COMPLETE", "FAILED"}
