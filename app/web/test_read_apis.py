"""Thorough unit tests for the read APIs and dashboard aggregation (task 10.5).

This is the exhaustive companion to the four smoke modules
(``test_read_contracts_smoke``, ``test_tasks_smoke``,
``test_dashboard_calendar_smoke``, ``test_review_queue_smoke``). It drives the
read endpoints through a FastAPI ``TestClient`` against an isolated, file-backed
SQLite database, reusing the established fixture style: ``get_session`` is
overridden with a per-test async SQLite engine, and ``get_settings_dep`` /
``get_refresh_token_store`` are overridden so auth works without real
infrastructure.

Coverage:

* ``GET /contracts`` -- pagination (limit/offset boundaries), status filter,
  newest-first ordering, owner scoping, empty result (Requirements 5.4, 2.1).
* ``GET /contracts/{id}`` -- clauses ordered + tasks with span offsets that
  round-trip; 404 for non-owned and missing; auth required (Requirements 5.4,
  2.1).
* ``GET /tasks`` -- each filter individually and several combined;
  due_before/due_after inclusive boundaries; undated handling; owner scoping;
  contract_id for a non-owned contract returns nothing (Requirements 6.1, 2.1).
* ``GET /dashboard/summary`` -- priority/status counts (incl. UNSET bucket),
  requires_review_count, upcoming_deadlines (excludes past/null, ascending,
  respects upcoming_limit), owner scoping, empty owner (Requirement 6.2).
* ``GET /calendar`` -- inclusive [from, to] windowing, ordering, inverted window
  -> 400, owner scoping (Requirement 6.3).
* ``GET /review-queue`` -- only owner's requires_review tasks (Requirement 8.3).
* Auth -- every endpoint returns 401 without a token (Requirement 1.7, 2.1).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.data.database import Base, get_session
from app.data.enums import ContractStatus, TaskStatus
from app.data.models import Clause, Contract, Task
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


# ---------------------------------------------------------------------------
# Fixtures (mirror the smoke modules' per-test SQLite + dependency overrides)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_url() -> Iterator[str]:
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
    engine = create_async_engine(db_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture()
def client(sessionmaker: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    app = create_app()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings_dep] = lambda: TEST_SETTINGS
    app.dependency_overrides[get_refresh_token_store] = lambda: InMemoryRefreshTokenStore()

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_and_login(client: TestClient, email: str, password: str) -> tuple[int, str]:
    reg = client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    user_id = reg.json()["id"]
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    client.cookies.clear()
    return user_id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _run(sessionmaker, coro_fn):
    async def _do():
        async with sessionmaker() as session:
            return await coro_fn(session)

    return asyncio.run(_do())


def _seed_contract(
    sessionmaker,
    user_id: int,
    *,
    filename: str,
    status: ContractStatus = ContractStatus.PENDING,
    error_message: str | None = None,
    clauses: list[dict] | None = None,
    tasks: list[dict] | None = None,
) -> dict[str, int]:
    """Insert a contract with optional clauses/tasks; return an id map.

    ``clauses`` items accept ``clause_index``, ``heading``, ``body_text``.
    ``tasks`` items accept ``key`` (to map back the id), plus any Task column
    such as ``title``, ``priority``, ``status``, ``requires_review``,
    ``due_date``, ``contract_id`` is filled automatically. Span offsets are
    computed automatically when ``obligated_party``/``action`` are given so they
    round-trip against ``source_text``.
    """

    async def _seed(session: AsyncSession) -> dict[str, int]:
        contract = Contract(
            user_id=user_id,
            filename=filename,
            file_key=f"contracts/{user_id}/{filename}.key",
            file_size_kb=10,
            status=status,
            error_message=error_message,
        )
        session.add(contract)
        await session.flush()

        ids: dict[str, int] = {"contract": contract.id}

        clause_ids: list[int] = []
        for spec in clauses or []:
            clause = Clause(
                contract_id=contract.id,
                clause_index=spec["clause_index"],
                heading=spec.get("heading"),
                body_text=spec.get("body_text", "body"),
            )
            session.add(clause)
            await session.flush()
            clause_ids.append(clause.id)
            if "key" in spec:
                ids[spec["key"]] = clause.id

        for spec in tasks or []:
            source_text = spec.get("source_text", "x")
            party = spec.get("obligated_party")
            action = spec.get("action")
            kwargs = dict(
                contract_id=contract.id,
                title=spec.get("title", "T"),
                priority=spec.get("priority"),
                status=spec.get("status", TaskStatus.PENDING),
                requires_review=spec.get("requires_review", False),
                due_date=spec.get("due_date"),
                source_text=source_text,
                obligated_party=party,
                action=action,
            )
            if "clause_ref" in spec:
                kwargs["clause_id"] = ids[spec["clause_ref"]]
            if party is not None and source_text.find(party) >= 0:
                start = source_text.find(party)
                kwargs["agent_start"] = start
                kwargs["agent_end"] = start + len(party)
            if action is not None and source_text.find(action) >= 0:
                start = source_text.find(action)
                kwargs["action_start"] = start
                kwargs["action_end"] = start + len(action)
            task = Task(**kwargs)
            session.add(task)
            await session.flush()
            if "key" in spec:
                ids[spec["key"]] = task.id

        await session.commit()
        return ids

    return _run(sessionmaker, _seed)


# ===========================================================================
# GET /contracts  (list)
# ===========================================================================


def test_list_contracts_pagination_limit_and_offset(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "page@example.com", "pw-page-1234")

    # Five contracts; ids ascending with creation order, newest-first by id desc.
    created = [
        _seed_contract(sessionmaker, owner_id, filename=f"c{i}.pdf")["contract"]
        for i in range(5)
    ]
    newest_first = list(reversed(created))

    # limit truncates from the newest end.
    resp = client.get("/contracts", params={"limit": 2}, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()] == newest_first[:2]

    # offset skips from the newest end.
    resp = client.get(
        "/contracts", params={"limit": 2, "offset": 2}, headers=_auth(token)
    )
    assert [r["id"] for r in resp.json()] == newest_first[2:4]

    # offset past the end yields an empty page.
    resp = client.get("/contracts", params={"offset": 10}, headers=_auth(token))
    assert resp.json() == []


def test_list_contracts_pagination_boundary_validation(client, sessionmaker) -> None:
    _owner_id, token = _register_and_login(client, "bound@example.com", "pw-bound-123")

    # limit must be >= 1 and <= 100; offset must be >= 0.
    assert client.get("/contracts", params={"limit": 0}, headers=_auth(token)).status_code == 422
    assert client.get("/contracts", params={"limit": 101}, headers=_auth(token)).status_code == 422
    assert client.get("/contracts", params={"offset": -1}, headers=_auth(token)).status_code == 422
    # The boundary values are accepted.
    assert client.get("/contracts", params={"limit": 1}, headers=_auth(token)).status_code == 200
    assert client.get("/contracts", params={"limit": 100}, headers=_auth(token)).status_code == 200


def test_list_contracts_status_filter_each_value(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "cstatus@example.com", "pw-cstat-123")

    by_status = {
        ContractStatus.PENDING: _seed_contract(
            sessionmaker, owner_id, filename="p.pdf", status=ContractStatus.PENDING
        )["contract"],
        ContractStatus.PROCESSING: _seed_contract(
            sessionmaker, owner_id, filename="r.pdf", status=ContractStatus.PROCESSING
        )["contract"],
        ContractStatus.COMPLETE: _seed_contract(
            sessionmaker, owner_id, filename="c.pdf", status=ContractStatus.COMPLETE
        )["contract"],
        ContractStatus.FAILED: _seed_contract(
            sessionmaker, owner_id, filename="f.pdf", status=ContractStatus.FAILED,
            error_message="processing failed",
        )["contract"],
    }

    for status_enum, cid in by_status.items():
        resp = client.get(
            "/contracts", params={"status": status_enum.value}, headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [r["id"] for r in body] == [cid]
        assert body[0]["status"] == status_enum.value


def test_list_contracts_newest_first_ordering(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "order@example.com", "pw-order-123")
    a = _seed_contract(sessionmaker, owner_id, filename="a.pdf")["contract"]
    b = _seed_contract(sessionmaker, owner_id, filename="b.pdf")["contract"]
    c = _seed_contract(sessionmaker, owner_id, filename="c.pdf")["contract"]

    resp = client.get("/contracts", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()] == [c, b, a]


def test_list_contracts_owner_scoping(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "o@example.com", "pw-owner-123")
    other_id, _ = _register_and_login(client, "x@example.com", "pw-other-123")

    mine = _seed_contract(sessionmaker, owner_id, filename="mine.pdf")["contract"]
    _seed_contract(sessionmaker, other_id, filename="theirs.pdf")

    resp = client.get("/contracts", headers=_auth(owner_token))
    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()] == [mine]


def test_list_contracts_empty_result(client, sessionmaker) -> None:
    _owner_id, token = _register_and_login(client, "none@example.com", "pw-none-1234")
    resp = client.get("/contracts", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ===========================================================================
# GET /contracts/{id}  (detail)
# ===========================================================================


def test_contract_detail_clauses_ordered_and_spans_round_trip(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "detail@example.com", "pw-detail-12")

    source = "The Supplier shall deliver the goods by Friday."
    ids = _seed_contract(
        sessionmaker,
        owner_id,
        filename="analysis.pdf",
        status=ContractStatus.COMPLETE,
        # Seed clauses out of order to prove the endpoint sorts by clause_index.
        clauses=[
            {"clause_index": 2, "heading": "Third", "body_text": "c2"},
            {"clause_index": 0, "heading": "First", "body_text": "c0"},
            {"clause_index": 1, "heading": "Second", "body_text": "c1"},
        ],
        tasks=[
            {
                "title": "Deliver",
                "source_text": source,
                "obligated_party": "The Supplier",
                "action": "deliver the goods",
            }
        ],
    )

    resp = client.get(f"/contracts/{ids['contract']}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Clauses are returned in ascending clause_index order.
    assert [c["clause_index"] for c in body["clauses"]] == [0, 1, 2]

    assert len(body["tasks"]) == 1
    task = body["tasks"][0]
    src = task["source_text"]
    assert src[task["agent_start"]:task["agent_end"]] == task["obligated_party"]
    assert src[task["action_start"]:task["action_end"]] == task["action"]


def test_contract_detail_404_for_non_owned(client, sessionmaker) -> None:
    owner_id, _ = _register_and_login(client, "real@example.com", "pw-real-1234")
    _other_id, other_token = _register_and_login(client, "thief@example.com", "pw-thief-12")

    cid = _seed_contract(sessionmaker, owner_id, filename="secret.pdf")["contract"]

    resp = client.get(f"/contracts/{cid}", headers=_auth(other_token))
    assert resp.status_code == 404, resp.text


def test_contract_detail_404_for_missing(client, sessionmaker) -> None:
    _owner_id, token = _register_and_login(client, "missing@example.com", "pw-miss-1234")
    resp = client.get("/contracts/999999", headers=_auth(token))
    assert resp.status_code == 404, resp.text


def test_contract_detail_requires_auth(client, sessionmaker) -> None:
    owner_id, _ = _register_and_login(client, "auth@example.com", "pw-auth-12345")
    cid = _seed_contract(sessionmaker, owner_id, filename="a.pdf")["contract"]
    assert client.get(f"/contracts/{cid}").status_code == 401


# ===========================================================================
# GET /tasks  (filtered list)
# ===========================================================================


def _seed_task_fixture(sessionmaker, user_id: int) -> dict[str, int]:
    """Seed a small, deterministic task set across two contracts."""

    today = date(2030, 6, 15)
    c1 = _seed_contract(
        sessionmaker,
        user_id,
        filename="c1.pdf",
        tasks=[
            {
                "key": "t_high",
                "title": "High dated review",
                "priority": "HIGH",
                "status": TaskStatus.PENDING,
                "requires_review": True,
                "due_date": today,
            },
            {
                "key": "t_low_done",
                "title": "Low done undated",
                "priority": "LOW",
                "status": TaskStatus.DONE,
                "requires_review": False,
                "due_date": None,
            },
        ],
    )
    c2 = _seed_contract(
        sessionmaker,
        user_id,
        filename="c2.pdf",
        tasks=[
            {
                "key": "t_low_far",
                "title": "Low pending far",
                "priority": "LOW",
                "status": TaskStatus.PENDING,
                "requires_review": False,
                "due_date": today + timedelta(days=30),
            },
            {
                "key": "t_snoozed",
                "title": "Snoozed mid",
                "priority": "MEDIUM",
                "status": TaskStatus.SNOOZED,
                "requires_review": False,
                "due_date": today + timedelta(days=10),
            },
        ],
    )
    return {
        "c1": c1["contract"],
        "c2": c2["contract"],
        "today": today,
        **{k: v for k, v in c1.items() if k != "contract"},
        **{k: v for k, v in c2.items() if k != "contract"},
    }


def test_tasks_filter_priority(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "tp@example.com", "pw-tp-12345")
    ids = _seed_task_fixture(sessionmaker, owner_id)

    def get(**params):
        r = client.get("/tasks", params=params, headers=_auth(token))
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()]

    assert get(priority="HIGH") == [ids["t_high"]]
    assert set(get(priority="LOW")) == {ids["t_low_far"], ids["t_low_done"]}
    assert get(priority="MEDIUM") == [ids["t_snoozed"]]
    assert get(priority="NONEXISTENT") == []


def test_tasks_filter_status(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "ts@example.com", "pw-ts-12345")
    ids = _seed_task_fixture(sessionmaker, owner_id)

    def get(**params):
        r = client.get("/tasks", params=params, headers=_auth(token))
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()]

    assert set(get(status="PENDING")) == {ids["t_high"], ids["t_low_far"]}
    assert get(status="DONE") == [ids["t_low_done"]]
    assert get(status="SNOOZED") == [ids["t_snoozed"]]
    assert get(status="DISMISSED") == []


def test_tasks_filter_requires_review(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "trr@example.com", "pw-trr-12345")
    ids = _seed_task_fixture(sessionmaker, owner_id)

    def get(**params):
        r = client.get("/tasks", params=params, headers=_auth(token))
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()]

    assert get(requires_review=True) == [ids["t_high"]]
    assert set(get(requires_review=False)) == {
        ids["t_low_done"], ids["t_low_far"], ids["t_snoozed"]
    }


def test_tasks_filter_contract_id(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "tc@example.com", "pw-tc-12345")
    ids = _seed_task_fixture(sessionmaker, owner_id)

    def get(**params):
        r = client.get("/tasks", params=params, headers=_auth(token))
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()]

    assert set(get(contract_id=ids["c1"])) == {ids["t_high"], ids["t_low_done"]}
    assert set(get(contract_id=ids["c2"])) == {ids["t_low_far"], ids["t_snoozed"]}


def test_tasks_filter_due_before_after_inclusive_boundaries(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "td@example.com", "pw-td-12345")
    ids = _seed_task_fixture(sessionmaker, owner_id)
    today = ids["today"]

    def get(**params):
        r = client.get("/tasks", params=params, headers=_auth(token))
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()]

    # due_before is inclusive: the task due exactly on `today` is included.
    assert get(due_before=today.isoformat()) == [ids["t_high"]]
    # due_after is inclusive: the task due exactly on `today` is included.
    assert set(get(due_after=today.isoformat())) == {
        ids["t_high"], ids["t_snoozed"], ids["t_low_far"]
    }
    # An exact-day window (due_after == due_before == today) returns only that task.
    assert get(due_after=today.isoformat(), due_before=today.isoformat()) == [ids["t_high"]]
    # Undated tasks are excluded whenever a date bound is applied.
    far = today + timedelta(days=30)
    assert ids["t_low_done"] not in get(due_before=far.isoformat())
    assert ids["t_low_done"] not in get(due_after=today.isoformat())


def test_tasks_undated_sorted_last_when_unfiltered(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "tu@example.com", "pw-tu-12345")
    ids = _seed_task_fixture(sessionmaker, owner_id)

    r = client.get("/tasks", headers=_auth(token))
    assert r.status_code == 200, r.text
    returned = [t["id"] for t in r.json()]
    # Deadline-first: dated ascending, then the undated task last.
    assert returned == [ids["t_high"], ids["t_snoozed"], ids["t_low_far"], ids["t_low_done"]]


def test_tasks_combined_filters(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "tcomb@example.com", "pw-tcomb-12")
    ids = _seed_task_fixture(sessionmaker, owner_id)

    def get(**params):
        r = client.get("/tasks", params=params, headers=_auth(token))
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()]

    # PENDING + LOW -> only the far low task (high is HIGH; done is DONE).
    assert get(status="PENDING", priority="LOW") == [ids["t_low_far"]]
    # PENDING + requires_review -> only the high task.
    assert get(status="PENDING", requires_review=True) == [ids["t_high"]]
    # contract c2 + SNOOZED -> only the snoozed task.
    assert get(contract_id=ids["c2"], status="SNOOZED") == [ids["t_snoozed"]]
    # contract c2 + due_after today -> both c2 tasks are dated in the future.
    assert set(get(contract_id=ids["c2"], due_after=ids["today"].isoformat())) == {
        ids["t_low_far"], ids["t_snoozed"]
    }


def test_tasks_owner_scoping(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "town@example.com", "pw-town-12")
    other_id, _ = _register_and_login(client, "tother@example.com", "pw-toth-12")

    ids = _seed_task_fixture(sessionmaker, owner_id)
    other_ids = _seed_task_fixture(sessionmaker, other_id)

    r = client.get("/tasks", headers=_auth(owner_token))
    assert r.status_code == 200, r.text
    returned = {t["id"] for t in r.json()}
    assert returned == {ids["t_high"], ids["t_low_done"], ids["t_low_far"], ids["t_snoozed"]}
    assert other_ids["t_high"] not in returned


def test_tasks_contract_id_for_non_owned_contract_returns_nothing(client, sessionmaker) -> None:
    owner_id, _ = _register_and_login(client, "tnoa@example.com", "pw-tnoa-12")
    _other_id, other_token = _register_and_login(client, "tnob@example.com", "pw-tnob-12")

    ids = _seed_task_fixture(sessionmaker, owner_id)

    # Other user filters by a contract id they do not own -> empty (no leak).
    r = client.get("/tasks", params={"contract_id": ids["c1"]}, headers=_auth(other_token))
    assert r.status_code == 200, r.text
    assert r.json() == []


# ===========================================================================
# GET /dashboard/summary
# ===========================================================================


def test_dashboard_summary_counts_unset_review_and_upcoming(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "dash@example.com", "pw-dash-12345")
    other_id, _ = _register_and_login(client, "dashx@example.com", "pw-dashx-1234")

    today = date.today()
    _seed_contract(
        sessionmaker,
        owner_id,
        filename="owner.pdf",
        status=ContractStatus.COMPLETE,
        tasks=[
            {"priority": "CRITICAL", "status": TaskStatus.PENDING, "requires_review": True,
             "due_date": today + timedelta(days=2)},
            {"priority": "CRITICAL", "status": TaskStatus.DONE, "requires_review": False,
             "due_date": today + timedelta(days=10)},
            {"priority": "LOW", "status": TaskStatus.PENDING, "requires_review": True,
             "due_date": today - timedelta(days=3)},   # past due -> excluded from upcoming
            {"priority": None, "status": TaskStatus.SNOOZED, "requires_review": False,
             "due_date": None},                          # null priority -> UNSET, null due
            {"priority": "LOW", "status": TaskStatus.PENDING, "requires_review": False,
             "due_date": today},                          # due today -> included (>= today)
        ],
    )
    # Another owner's tasks must not affect the summary.
    _seed_contract(
        sessionmaker,
        other_id,
        filename="intruder.pdf",
        tasks=[
            {"priority": "CRITICAL", "status": TaskStatus.PENDING, "requires_review": True,
             "due_date": today + timedelta(days=1)},
        ],
    )

    resp = client.get("/dashboard/summary", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["counts_by_priority"] == {"CRITICAL": 2, "LOW": 2, "UNSET": 1}
    assert body["counts_by_status"] == {"PENDING": 3, "DONE": 1, "SNOOZED": 1}
    assert body["requires_review_count"] == 2

    upcoming = body["upcoming_deadlines"]
    due_dates = [e["due_date"] for e in upcoming]
    # Past-due and null due_date are excluded; remaining are ascending.
    assert (today - timedelta(days=3)).isoformat() not in due_dates
    assert due_dates == sorted(due_dates)
    # today, +2, +10  -> three upcoming.
    assert due_dates == [
        today.isoformat(),
        (today + timedelta(days=2)).isoformat(),
        (today + timedelta(days=10)).isoformat(),
    ]


def test_dashboard_summary_upcoming_limit(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "dlim@example.com", "pw-dlim-12345")
    today = date.today()
    _seed_contract(
        sessionmaker,
        owner_id,
        filename="many.pdf",
        tasks=[
            {"title": f"t{i}", "due_date": today + timedelta(days=i)}
            for i in range(1, 8)
        ],
    )

    # Default upcoming limit is 5.
    body = client.get("/dashboard/summary", headers=_auth(token)).json()
    assert len(body["upcoming_deadlines"]) == 5

    # upcoming_limit caps the list and keeps the soonest first.
    body = client.get(
        "/dashboard/summary", params={"upcoming_limit": 3}, headers=_auth(token)
    ).json()
    due = [e["due_date"] for e in body["upcoming_deadlines"]]
    assert due == [
        (today + timedelta(days=1)).isoformat(),
        (today + timedelta(days=2)).isoformat(),
        (today + timedelta(days=3)).isoformat(),
    ]


def test_dashboard_summary_empty_owner(client, sessionmaker) -> None:
    _owner_id, token = _register_and_login(client, "dempty@example.com", "pw-dempty-12")
    body = client.get("/dashboard/summary", headers=_auth(token)).json()
    assert body["counts_by_priority"] == {}
    assert body["counts_by_status"] == {}
    assert body["requires_review_count"] == 0
    assert body["upcoming_deadlines"] == []


def test_dashboard_summary_upcoming_limit_boundary_validation(client, sessionmaker) -> None:
    _owner_id, token = _register_and_login(client, "dvb@example.com", "pw-dvb-12345")
    assert client.get(
        "/dashboard/summary", params={"upcoming_limit": 0}, headers=_auth(token)
    ).status_code == 422
    assert client.get(
        "/dashboard/summary", params={"upcoming_limit": 51}, headers=_auth(token)
    ).status_code == 422


# ===========================================================================
# GET /calendar
# ===========================================================================


def test_calendar_inclusive_window_and_ordering(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "cal@example.com", "pw-cal-12345")
    base = date(2030, 3, 10)
    _seed_contract(
        sessionmaker,
        owner_id,
        filename="cal.pdf",
        tasks=[
            {"title": "before", "due_date": base - timedelta(days=1)},   # outside
            {"title": "start", "due_date": base},                        # inclusive start
            {"title": "mid", "due_date": base + timedelta(days=5)},
            {"title": "end", "due_date": base + timedelta(days=10)},     # inclusive end
            {"title": "after", "due_date": base + timedelta(days=11)},   # outside
            {"title": "nodue", "due_date": None},                        # excluded
        ],
    )

    resp = client.get(
        "/calendar",
        params={"from": base.isoformat(), "to": (base + timedelta(days=10)).isoformat()},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    titles = [t["title"] for t in resp.json()]
    # Boundary dates included, ordered ascending by due_date.
    assert titles == ["start", "mid", "end"]


def test_calendar_single_day_window(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "cal1@example.com", "pw-cal1-1234")
    day = date(2030, 7, 1)
    _seed_contract(
        sessionmaker,
        owner_id,
        filename="cal1.pdf",
        tasks=[
            {"title": "on-day", "due_date": day},
            {"title": "next-day", "due_date": day + timedelta(days=1)},
        ],
    )
    resp = client.get(
        "/calendar",
        params={"from": day.isoformat(), "to": day.isoformat()},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert [t["title"] for t in resp.json()] == ["on-day"]


def test_calendar_inverted_window_returns_400(client, sessionmaker) -> None:
    _owner_id, token = _register_and_login(client, "calinv@example.com", "pw-calinv-1")
    resp = client.get(
        "/calendar",
        params={"from": "2030-06-20", "to": "2030-06-10"},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text


def test_calendar_owner_scoping(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "calo@example.com", "pw-calo-123")
    other_id, _ = _register_and_login(client, "calx@example.com", "pw-calx-123")

    base = date(2030, 9, 1)
    _seed_contract(
        sessionmaker, owner_id, filename="o.pdf",
        tasks=[{"title": "mine", "due_date": base + timedelta(days=2)}],
    )
    _seed_contract(
        sessionmaker, other_id, filename="x.pdf",
        tasks=[{"title": "theirs", "due_date": base + timedelta(days=2)}],
    )

    resp = client.get(
        "/calendar",
        params={"from": base.isoformat(), "to": (base + timedelta(days=30)).isoformat()},
        headers=_auth(owner_token),
    )
    assert resp.status_code == 200, resp.text
    assert [t["title"] for t in resp.json()] == ["mine"]


# ===========================================================================
# GET /review-queue
# ===========================================================================


def test_review_queue_only_owner_flagged_tasks(client, sessionmaker) -> None:
    owner_id, owner_token = _register_and_login(client, "rq@example.com", "pw-rq-12345")
    other_id, _ = _register_and_login(client, "rqx@example.com", "pw-rqx-12345")

    ids = _seed_contract(
        sessionmaker,
        owner_id,
        filename="rq.pdf",
        tasks=[
            {"key": "flagged_dated", "title": "flagged-dated",
             "requires_review": True, "due_date": date(2030, 1, 1)},
            {"key": "flagged_undated", "title": "flagged-undated",
             "requires_review": True, "due_date": None},
            {"key": "not_flagged", "title": "not-flagged",
             "requires_review": False, "due_date": date(2030, 1, 1)},
        ],
    )
    # Another user's flagged task must never leak.
    _seed_contract(
        sessionmaker, other_id, filename="rqx.pdf",
        tasks=[{"title": "intruder", "requires_review": True, "due_date": date(2030, 1, 1)}],
    )

    resp = client.get("/review-queue", headers=_auth(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Dated flagged first (nulls last); non-flagged and other user excluded.
    assert [r["id"] for r in body] == [ids["flagged_dated"], ids["flagged_undated"]]
    assert all(r["requires_review"] is True for r in body)


def test_review_queue_empty_when_no_flagged(client, sessionmaker) -> None:
    owner_id, token = _register_and_login(client, "rqe@example.com", "pw-rqe-12345")
    _seed_contract(
        sessionmaker, owner_id, filename="rqe.pdf",
        tasks=[{"title": "plain", "requires_review": False}],
    )
    resp = client.get("/review-queue", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ===========================================================================
# Auth required on every read endpoint
# ===========================================================================


def test_read_endpoints_require_auth(client) -> None:
    assert client.get("/contracts").status_code == 401
    assert client.get("/contracts/1").status_code == 401
    assert client.get("/tasks").status_code == 401
    assert client.get("/dashboard/summary").status_code == 401
    assert client.get(
        "/calendar", params={"from": "2030-01-01", "to": "2030-12-31"}
    ).status_code == 401
    assert client.get("/review-queue").status_code == 401
