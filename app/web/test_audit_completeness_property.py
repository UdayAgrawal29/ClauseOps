"""Property test for audit completeness (task 12.4).

**Property 6: Audit completeness**
**Validates: Requirements 7.4**

Every APPLIED ``PATCH /tasks/{id}`` mutation produces *exactly one* ``AuditLog``
entry whose ``before``/``after`` capture precisely the changed fields; no
mutation is applied without a corresponding audit entry, and a no-op PATCH
(values equal to the current row) writes no audit entry at all.

The test drives the real endpoint through a FastAPI ``TestClient`` against an
isolated, file-backed SQLite database (the same harness style as
``test_task_status_property.py``): ``get_session``/``get_settings_dep``/
``get_refresh_token_store`` are overridden, a user is registered and logged in,
and one owned task is seeded via the sessionmaker.

Because a Hypothesis ``@given`` test reuses a single function-scoped fixture
across every generated example (``HealthCheck.function_scoped_fixture`` is
suppressed), audit rows accumulate in the shared database across examples. The
properties are therefore expressed in terms of the *delta* in audit-row count
around each operation (count before vs. count after), which is robust to that
accumulation. Expected changes are computed by re-reading the task's state from
the DB immediately before each operation and comparing it to the requested
payload.
"""

from __future__ import annotations

import asyncio
import enum
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.data.database import Base, get_session
from app.data.enums import TaskStatus
from app.data.models import AuditLog, Contract, Task
from app.web.dependencies import get_settings_dep
from app.web.main import create_app
from app.web.revocation import InMemoryRefreshTokenStore, get_refresh_token_store

TEST_SETTINGS = Settings(
    jwt_secret="test-access-secret",
    jwt_refresh_secret="test-refresh-secret",
    jwt_algorithm="HS256",
    access_token_ttl_seconds=900,
    refresh_token_ttl_seconds=60 * 60 * 24 * 14,
)

# The user-correctable (non-status) subset of a Task that PATCH /tasks/{id}
# records as field corrections (mirrors tasks_router._CORRECTABLE_FIELDS).
_CORRECTABLE_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "obligated_party",
    "beneficiary",
    "action",
    "due_date",
    "date_type",
    "priority",
)


# --- JSON-safe coercion (mirrors tasks_router._json_safe) --------------------
def _json_safe(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _parse_requested(field: str, raw: Any) -> Any:
    """Parse a raw JSON payload value into the comparable Python form.

    The endpoint compares the *parsed* request value against the stored Python
    attribute, so we mirror that: ``status`` becomes a :class:`TaskStatus` and
    ``due_date`` an ISO ``date``; everything else passes through unchanged.
    """

    if raw is None:
        return None
    if field == "status":
        return TaskStatus(raw)
    if field == "due_date":
        return date.fromisoformat(raw)
    return raw


# --- Strategies --------------------------------------------------------------
# Per-field value pools deliberately include the *seeded* values so that a fair
# share of generated payloads are no-ops (value == current), while the
# alternatives drive real changes. ``None`` is a deliberate clear for the
# Optional fields.
_FIELD_POOLS: dict[str, list[Any]] = {
    "status": ["PENDING", "DONE", "SNOOZED", "DISMISSED"],
    "title": ["Original title", "Revised title", "Another title", ""],
    "description": [None, "First draft", "Second draft"],
    "obligated_party": ["Acme Corp", "Beta LLC", None],
    "beneficiary": [None, "Beneficiary One", "Beneficiary Two"],
    "action": [None, "Submit report", "Pay invoice"],
    "due_date": [None, "2030-06-01", "2031-01-15"],
    "date_type": [None, "absolute", "relative"],
    "priority": ["LOW", "MEDIUM", "HIGH", None],
}

_ALL_FIELDS: tuple[str, ...] = ("status", *_CORRECTABLE_FIELDS)


@st.composite
def patch_payload(draw: st.DrawFn) -> dict[str, Any]:
    """An arbitrary PATCH body: a subset of fields, each value from its pool.

    Each field is independently included or omitted; included values may equal
    or differ from the seeded task, so examples range from full no-ops to
    multi-field changes.
    """

    payload: dict[str, Any] = {}
    for field in _ALL_FIELDS:
        if draw(st.booleans()):
            payload[field] = draw(st.sampled_from(_FIELD_POOLS[field]))
    return payload


# --- DB / app harness --------------------------------------------------------
def _query(sessionmaker, coro_fn):
    async def _run():
        async with sessionmaker() as session:
            return await coro_fn(session)

    return asyncio.run(_run())


def _seed_task(sessionmaker, user_id: int) -> int:
    async def _do(session: AsyncSession) -> int:
        c = Contract(user_id=user_id, filename="a.pdf", file_key=f"k/{user_id}/a")
        session.add(c)
        await session.flush()
        t = Task(
            contract_id=c.id,
            title="Original title",
            obligated_party="Acme Corp",
            priority="LOW",
            status=TaskStatus.PENDING,
            source_text="x",
        )
        session.add(t)
        await session.commit()
        return t.id

    return _query(sessionmaker, _do)


def _fetch_task_fields(sessionmaker, task_id: int) -> dict[str, Any]:
    """Snapshot the task's current Python attribute values for all PATCHable fields."""

    async def _do(session: AsyncSession) -> dict[str, Any]:
        task = await session.get(Task, task_id)
        return {field: getattr(task, field) for field in _ALL_FIELDS}

    return _query(sessionmaker, _do)


def _fetch_audit_logs(sessionmaker, task_id: int) -> list[AuditLog]:
    """Return all AuditLog rows for ``task_id`` ordered by id (oldest first)."""

    async def _do(session: AsyncSession) -> list[AuditLog]:
        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.entity == "Task")
            .where(AuditLog.entity_id == task_id)
            .order_by(AuditLog.id.asc())
        )
        return list(result.scalars().all())

    return _query(sessionmaker, _do)


def _expected_change(
    payload: dict[str, Any], current: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compute the JSON-safe ``before``/``after`` of fields that actually change.

    Mirrors the endpoint: a field is "changed" only when its parsed requested
    value differs from the stored value; unchanged / omitted fields contribute
    nothing.
    """

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field, raw in payload.items():
        new_value = _parse_requested(field, raw)
        old_value = current[field]
        if new_value != old_value:
            before[field] = _json_safe(old_value)
            after[field] = _json_safe(new_value)
    return before, after


class _Harness:
    """A built app + client + seeded owned task reused across examples."""

    def __init__(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self._path = path
        url = f"sqlite+aiosqlite:///{path}"

        async def _create() -> None:
            engine = create_async_engine(url, poolclass=NullPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await engine.dispose()

        asyncio.run(_create())

        engine = create_async_engine(url, poolclass=NullPool)
        self.sessionmaker = async_sessionmaker(
            engine, expire_on_commit=False, autoflush=False
        )

        app = create_app()

        async def _override_get_session() -> AsyncIterator[AsyncSession]:
            async with self.sessionmaker() as session:
                yield session

        app.dependency_overrides[get_session] = _override_get_session
        app.dependency_overrides[get_settings_dep] = lambda: TEST_SETTINGS
        app.dependency_overrides[get_refresh_token_store] = (
            lambda: InMemoryRefreshTokenStore()
        )

        self.client = TestClient(app)
        self.client.__enter__()

        reg = self.client.post(
            "/auth/register",
            json={"email": "owner@example.com", "password": "pw-owner-123"},
        )
        assert reg.status_code == 201, reg.text
        self.user_id = reg.json()["id"]
        login = self.client.post(
            "/auth/login",
            json={"email": "owner@example.com", "password": "pw-owner-123"},
        )
        assert login.status_code == 200, login.text
        self.token = login.json()["access_token"]
        self.client.cookies.clear()

        self.task_id = _seed_task(self.sessionmaker, self.user_id)

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        try:
            os.remove(self._path)
        except OSError:
            pass


@pytest.fixture()
def harness() -> Iterator[_Harness]:
    h = _Harness()
    try:
        yield h
    finally:
        h.close()


# --- Properties --------------------------------------------------------------
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(payload=patch_payload())
def test_each_applied_mutation_writes_exactly_one_matching_audit_row(
    harness: _Harness, payload: dict[str, Any]
) -> None:
    """Validates: Requirements 7.4.

    For a single PATCH: if at least one field actually changes, exactly one new
    AuditLog row is written whose before/after keys equal the changed field set
    and whose values match the pre/post (JSON-safe) values, with correct
    action/entity/entity_id/user_id. If nothing changes (no-op), no new audit
    row is written.
    """

    before_state = _fetch_task_fields(harness.sessionmaker, harness.task_id)
    logs_before = _fetch_audit_logs(harness.sessionmaker, harness.task_id)

    expected_before, expected_after = _expected_change(payload, before_state)
    changed = bool(expected_before)

    resp = harness.client.patch(
        f"/tasks/{harness.task_id}", json=payload, headers=harness.auth
    )
    assert resp.status_code == 200, resp.text

    logs_after = _fetch_audit_logs(harness.sessionmaker, harness.task_id)
    delta = len(logs_after) - len(logs_before)

    if not changed:
        # A no-op mutation must not write any audit row.
        assert delta == 0
        return

    # An effective mutation writes exactly one new audit row.
    assert delta == 1
    entry = logs_after[-1]
    assert entry.user_id == harness.user_id
    assert entry.entity == "Task"
    assert entry.entity_id == harness.task_id
    assert entry.action == "task.update"
    # before/after capture EXACTLY the changed fields, with matching values.
    assert entry.before == expected_before
    assert entry.after == expected_after
    assert set(entry.before.keys()) == set(expected_before.keys())
    assert set(entry.after.keys()) == set(expected_before.keys())

    # The post-mutation row reflects the requested changes.
    after_state = _fetch_task_fields(harness.sessionmaker, harness.task_id)
    for field in expected_after:
        assert _json_safe(after_state[field]) == expected_after[field]


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(payloads=st.lists(patch_payload(), min_size=1, max_size=5))
def test_audit_row_count_equals_number_of_effective_mutations(
    harness: _Harness, payloads: list[dict[str, Any]]
) -> None:
    """Validates: Requirements 7.4.

    Across a short sequence of PATCHes, the number of new AuditLog rows equals
    the number of operations that actually changed something -- never more
    (every applied mutation is audited) and never fewer (no-ops add nothing).
    """

    logs_before = _fetch_audit_logs(harness.sessionmaker, harness.task_id)

    effective = 0
    for payload in payloads:
        # Re-read state before each op so expected changes are computed against
        # the live row (earlier ops in the sequence may have mutated it).
        state = _fetch_task_fields(harness.sessionmaker, harness.task_id)
        exp_before, _ = _expected_change(payload, state)
        if exp_before:
            effective += 1

        resp = harness.client.patch(
            f"/tasks/{harness.task_id}", json=payload, headers=harness.auth
        )
        assert resp.status_code == 200, resp.text

    logs_after = _fetch_audit_logs(harness.sessionmaker, harness.task_id)
    assert len(logs_after) - len(logs_before) == effective
