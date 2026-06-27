"""Unit tests for Contract upload validation and deletion (task 7.3).

Exercises ``POST /contracts`` and ``DELETE /contracts/{id}`` end-to-end through a
FastAPI ``TestClient`` against an isolated, file-backed SQLite database (so the
async ORM runs without a live PostgreSQL). Following the fixture style of
``test_auth_flows``, the ``get_session`` dependency is overridden with an
in-memory-style file SQLite engine (NullPool, ``Base.metadata.create_all``), and
``get_settings_dep`` / ``get_refresh_token_store`` are overridden too. Uploads
are kept off real infrastructure by overriding ``get_storage_backend`` with a
temp-dir ``LocalFilesystemStorage`` and ``get_enqueue_processing`` with a stub
that records invocations.

A ``PRAGMA foreign_keys=ON`` connection listener is attached to the test engine
so SQLite actually enforces the ``ON DELETE CASCADE`` rules, letting the cascade
deletion path be genuinely exercised.

Coverage (Requirements 3.1, 3.2, 3.3, 3.5):
* size-cap rejection (> 20MB -> 413) creates no Contract and stores nothing.
* MIME rejection (non-pdf content type -> 415) and magic-byte rejection
  (application/pdf without ``%PDF`` -> 400) each create no Contract and store
  nothing.
* success path -> 202 ``{contract_id, job_id}``, Contract row created PENDING,
  file stored under a randomized key, enqueue invoked.
* deletion -> 204 with cascade removal of clauses/tasks/reminders/notifications
  and removal of the stored file; non-owned / missing contract -> 404 with no
  deletion.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.data.database import Base, get_session
from app.data.enums import ContractStatus, ReminderChannel, TaskStatus
from app.data.models import (
    Clause,
    Contract,
    Notification,
    Reminder,
    Task,
)
from app.processing.enqueue import get_enqueue_processing
from app.storage import LocalFilesystemStorage, get_storage_backend
from app.web.dependencies import get_settings_dep
from app.web.main import create_app
from app.web.revocation import (
    InMemoryRefreshTokenStore,
    get_refresh_token_store,
)

# Fixed, known secrets so token issue/verify are consistent across endpoints and
# the current-user dependency. max_upload_bytes is left at the real 20MB cap so
# the size-cap test exercises the actual boundary.
TEST_SETTINGS = Settings(
    jwt_secret="test-access-secret",
    jwt_refresh_secret="test-refresh-secret",
    jwt_algorithm="HS256",
    access_token_ttl_seconds=900,
    refresh_token_ttl_seconds=60 * 60 * 24 * 14,
)

# The job id the stubbed enqueue returns, asserted on the success path.
_STUB_JOB_ID = "stub-job-id-7f3a"

# A minimal byte payload that passes the %PDF magic-byte check.
_VALID_PDF = b"%PDF-1.4\n%minimal valid-ish pdf body\n"


class _RecordingEnqueue:
    """Stub enqueue callable that records calls and returns a fixed job id."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, contract_id: int) -> str:
        self.calls.append(contract_id)
        return _STUB_JOB_ID


@pytest.fixture()
def db_url() -> Iterator[str]:
    """A throwaway file-backed SQLite database, unique per test."""

    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    url = f"sqlite+aiosqlite:///{path}"

    async def _create() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create())
    try:
        yield url
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@pytest.fixture()
def sessionmaker(db_url: str) -> async_sessionmaker[AsyncSession]:
    """Async session factory bound to the per-test database.

    A ``connect`` listener turns on ``PRAGMA foreign_keys`` for every SQLite
    connection so the ``ON DELETE CASCADE`` foreign keys actually fire when a
    Contract row is deleted -- without it SQLite silently ignores cascades and
    the deletion test would not exercise the real behavior.
    """

    engine = create_async_engine(db_url, poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture()
def storage(tmp_path) -> LocalFilesystemStorage:
    """A temp-dir local storage backend so uploads never touch real storage."""

    return LocalFilesystemStorage(tmp_path / "storage")


@pytest.fixture()
def enqueue() -> _RecordingEnqueue:
    """A fresh recording enqueue stub per test."""

    return _RecordingEnqueue()


@pytest.fixture()
def store() -> InMemoryRefreshTokenStore:
    """A fresh, isolated refresh-token revocation store per test."""

    return InMemoryRefreshTokenStore()


@pytest.fixture()
def client(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalFilesystemStorage,
    enqueue: _RecordingEnqueue,
    store: InMemoryRefreshTokenStore,
) -> Iterator[TestClient]:
    """TestClient with DB session, settings, storage, enqueue, and token store overridden."""

    app = create_app()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings_dep] = lambda: TEST_SETTINGS
    app.dependency_overrides[get_storage_backend] = lambda: storage
    app.dependency_overrides[get_enqueue_processing] = lambda: enqueue
    app.dependency_overrides[get_refresh_token_store] = lambda: store

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _query(sessionmaker: async_sessionmaker[AsyncSession], coro_fn):
    """Run a one-off async DB query/operation against the test database."""

    async def _run():
        async with sessionmaker() as session:
            return await coro_fn(session)

    return asyncio.run(_run())


def _register_and_login(client: TestClient, email: str, password: str) -> tuple[int, str]:
    """Register then login a user; return (user_id, access_token)."""

    reg = client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    user_id = reg.json()["id"]

    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    # Clear the refresh cookie so it does not bleed into subsequent requests.
    client.cookies.clear()
    return user_id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _count(sessionmaker, model) -> int:
    return _query(
        sessionmaker,
        lambda s: s.scalar(select(func.count()).select_from(model)),
    )


# ---------------------------------------------------------------------------
# Upload validation: size cap (Requirement 3.2)
# ---------------------------------------------------------------------------


def test_oversized_upload_rejected_413_no_contract_no_storage(
    client: TestClient, sessionmaker, storage: LocalFilesystemStorage, enqueue
) -> None:
    _user_id, token = _register_and_login(client, "big@example.com", "pw-big-123")

    # One byte over the 20MB cap; still valid PDF magic so size is the sole cause.
    oversized = _VALID_PDF + b"\0" * (TEST_SETTINGS.max_upload_bytes + 1 - len(_VALID_PDF))
    assert len(oversized) == TEST_SETTINGS.max_upload_bytes + 1

    resp = client.post(
        "/contracts",
        files={"file": ("big.pdf", oversized, "application/pdf")},
        headers=_auth(token),
    )

    assert resp.status_code == 413, resp.text
    # No Contract was created and nothing was stored.
    assert _count(sessionmaker, Contract) == 0
    assert list(storage.root.rglob("*.pdf")) == []
    assert enqueue.calls == []


# ---------------------------------------------------------------------------
# Upload validation: MIME + magic bytes (Requirement 3.3)
# ---------------------------------------------------------------------------


def test_non_pdf_mime_rejected_415_no_contract_no_storage(
    client: TestClient, sessionmaker, storage: LocalFilesystemStorage, enqueue
) -> None:
    _user_id, token = _register_and_login(client, "mime@example.com", "pw-mime-123")

    # Valid PDF bytes but a non-pdf declared content type -> 415 before storage.
    resp = client.post(
        "/contracts",
        files={"file": ("note.txt", _VALID_PDF, "text/plain")},
        headers=_auth(token),
    )

    assert resp.status_code == 415, resp.text
    assert _count(sessionmaker, Contract) == 0
    assert list(storage.root.rglob("*")) == [] or all(
        p.is_dir() for p in storage.root.rglob("*")
    )
    assert enqueue.calls == []


def test_bad_magic_bytes_rejected_400_no_contract_no_storage(
    client: TestClient, sessionmaker, storage: LocalFilesystemStorage, enqueue
) -> None:
    _user_id, token = _register_and_login(client, "magic@example.com", "pw-magic-123")

    # application/pdf content type but the body does not start with %PDF -> 400.
    not_a_pdf = b"This is plainly not a PDF file at all."
    resp = client.post(
        "/contracts",
        files={"file": ("fake.pdf", not_a_pdf, "application/pdf")},
        headers=_auth(token),
    )

    assert resp.status_code == 400, resp.text
    assert _count(sessionmaker, Contract) == 0
    assert all(p.is_dir() for p in storage.root.rglob("*"))
    assert enqueue.calls == []


def test_empty_file_rejected_400_no_contract(
    client: TestClient, sessionmaker, storage: LocalFilesystemStorage, enqueue
) -> None:
    _user_id, token = _register_and_login(client, "empty@example.com", "pw-empty-123")

    resp = client.post(
        "/contracts",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        headers=_auth(token),
    )

    assert resp.status_code == 400, resp.text
    assert _count(sessionmaker, Contract) == 0
    assert enqueue.calls == []


# ---------------------------------------------------------------------------
# Upload success path (Requirement 3.1)
# ---------------------------------------------------------------------------


def test_valid_upload_202_creates_pending_contract_stores_and_enqueues(
    client: TestClient, sessionmaker, storage: LocalFilesystemStorage, enqueue
) -> None:
    user_id, token = _register_and_login(client, "ok@example.com", "pw-ok-123456")

    resp = client.post(
        "/contracts",
        files={"file": ("agreement.pdf", _VALID_PDF, "application/pdf")},
        headers=_auth(token),
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    contract_id = body["contract_id"]
    assert isinstance(contract_id, int)
    # job_id comes from the (stubbed) enqueue seam, which was invoked once.
    assert body["job_id"] == _STUB_JOB_ID
    assert enqueue.calls == [contract_id]

    # A single PENDING Contract owned by the uploader now exists.
    def _load(session: AsyncSession):
        return session.scalar(select(Contract).where(Contract.id == contract_id))

    contract = _query(sessionmaker, _load)
    assert contract is not None
    assert contract.user_id == user_id
    assert contract.status == ContractStatus.PENDING
    assert contract.filename == "agreement.pdf"

    # The object key is randomized, non-guessable, and namespaced under the user.
    assert re.fullmatch(rf"contracts/{user_id}/[0-9a-f]{{32}}\.pdf", contract.file_key)
    # The bytes were actually stored under that key and round-trip exactly.
    assert storage.get(contract.file_key) == _VALID_PDF
    stored_files = list(storage.root.rglob("*.pdf"))
    assert len(stored_files) == 1


# ---------------------------------------------------------------------------
# Deletion with cascade + file removal (Requirements 3.5)
# ---------------------------------------------------------------------------


def _seed_derived_records(sessionmaker, contract_id: int, user_id: int) -> dict[str, int]:
    """Attach a clause, task, reminder, and notification to a contract.

    Returns the created ids so the test can confirm they are gone after a
    cascade delete.
    """

    async def _seed(session: AsyncSession) -> dict[str, int]:
        clause = Clause(contract_id=contract_id, clause_index=0, body_text="A clause.")
        session.add(clause)
        await session.flush()

        task = Task(
            contract_id=contract_id,
            clause_id=clause.id,
            title="Do the thing",
            status=TaskStatus.PENDING,
            source_text="Do the thing by Friday.",
        )
        session.add(task)
        await session.flush()

        reminder = Reminder(
            task_id=task.id,
            remind_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
            channel=ReminderChannel.IN_APP,
        )
        session.add(reminder)

        notification = Notification(
            user_id=user_id,
            task_id=task.id,
            message="A reminder fired.",
        )
        session.add(notification)
        await session.commit()
        return {
            "clause": clause.id,
            "task": task.id,
            "reminder": reminder.id,
            "notification": notification.id,
        }

    return _query(sessionmaker, _seed)


def test_delete_owned_contract_cascades_and_removes_file(
    client: TestClient, sessionmaker, storage: LocalFilesystemStorage, enqueue
) -> None:
    user_id, token = _register_and_login(client, "del@example.com", "pw-del-123456")

    upload = client.post(
        "/contracts",
        files={"file": ("c.pdf", _VALID_PDF, "application/pdf")},
        headers=_auth(token),
    )
    assert upload.status_code == 202, upload.text
    contract_id = upload.json()["contract_id"]

    # Look up the stored key, then seed derived clause/task/reminder/notification.
    file_key = _query(
        sessionmaker,
        lambda s: s.scalar(
            select(Contract.file_key).where(Contract.id == contract_id)
        ),
    )
    assert file_key is not None
    assert storage.get(file_key) == _VALID_PDF
    _seed_derived_records(sessionmaker, contract_id, user_id)

    # Sanity: everything exists before deletion.
    assert _count(sessionmaker, Contract) == 1
    assert _count(sessionmaker, Clause) == 1
    assert _count(sessionmaker, Task) == 1
    assert _count(sessionmaker, Reminder) == 1
    assert _count(sessionmaker, Notification) == 1

    resp = client.delete(f"/contracts/{contract_id}", headers=_auth(token))
    assert resp.status_code == 204, resp.text

    # The Contract and every derived record were cascade-deleted.
    assert _count(sessionmaker, Contract) == 0
    assert _count(sessionmaker, Clause) == 0
    assert _count(sessionmaker, Task) == 0
    assert _count(sessionmaker, Reminder) == 0
    assert _count(sessionmaker, Notification) == 0

    # The stored file was removed.
    assert list(storage.root.rglob("*.pdf")) == []


def test_delete_non_owned_contract_404_no_deletion(
    client: TestClient, sessionmaker, storage: LocalFilesystemStorage, enqueue
) -> None:
    owner_id, owner_token = _register_and_login(client, "owner@example.com", "pw-owner-123")
    upload = client.post(
        "/contracts",
        files={"file": ("owned.pdf", _VALID_PDF, "application/pdf")},
        headers=_auth(owner_token),
    )
    assert upload.status_code == 202, upload.text
    contract_id = upload.json()["contract_id"]
    file_key = _query(
        sessionmaker,
        lambda s: s.scalar(select(Contract.file_key).where(Contract.id == contract_id)),
    )

    # A different user attempts to delete the contract -> 404, nothing removed.
    _other_id, other_token = _register_and_login(client, "intruder@example.com", "pw-int-123")
    resp = client.delete(f"/contracts/{contract_id}", headers=_auth(other_token))

    assert resp.status_code == 404, resp.text
    assert _count(sessionmaker, Contract) == 1
    assert storage.get(file_key) == _VALID_PDF


def test_delete_missing_contract_404(
    client: TestClient, sessionmaker, storage: LocalFilesystemStorage, enqueue
) -> None:
    _user_id, token = _register_and_login(client, "ghost@example.com", "pw-ghost-123")

    resp = client.delete("/contracts/999999", headers=_auth(token))

    assert resp.status_code == 404, resp.text
    assert _count(sessionmaker, Contract) == 0
