"""User-scoped query helpers and ownership guards (cross-tenant isolation).

This module centralizes the multi-tenant isolation logic from the design's
"Requirement 2: User Data Scoping and Cross-Tenant Isolation" so that every
list/read query and every single-entity lookup is consistently scoped to one
owning user. Concentrating the scoping rules here means callers (the web layer,
the ML worker, schedulers) never hand-roll a ``WHERE user_id = ...`` clause and
cannot accidentally leak another tenant's rows.

Ownership model
---------------
Ownership is expressed two ways in the schema (see ``app.data.models``):

* **Direct** -- a ``user_id`` column lives on the row itself:
  :class:`~app.data.models.Contract`, :class:`~app.data.models.Notification`,
  and :class:`~app.data.models.AuditLog`.
* **Transitive** -- the row is owned through its parent contract, reached by a
  join: :class:`~app.data.models.Clause` and :class:`~app.data.models.Task`
  join through ``contracts.user_id``; :class:`~app.data.models.Reminder` joins
  through its parent task to the contract.

The ``*_for_user`` helpers return SQLAlchemy ``Select`` statements already
scoped to ``user_id`` (callers may add further filters/ordering/pagination).
The guard functions fetch a single entity *only if it is owned* by performing
the ownership filter inside the query itself, so a non-owned row is never read
or mutated.

Not-owned handling convention
------------------------------
Two flavors are provided so callers can pick the ergonomics they need, and both
deliberately make a missing row and a non-owned row indistinguishable so the
existence of another tenant's data is never revealed:

* :func:`get_owned_or_none` -- returns the entity when owned, otherwise
  ``None`` (no exception). Good for callers that want to branch themselves.
* :func:`require_owned` -- returns the entity when owned, otherwise raises
  :class:`OwnershipError`. The web layer maps this single exception to a
  not-authorized / not-found HTTP response (e.g. 404).

In every case a non-owned reference triggers **no read** of the row's columns
(the ownership predicate is part of the SELECT) and **no mutation**.

This module accepts a plain ``user_id: int`` rather than depending on the auth
layer's current-user dependency, so it is independently testable and reusable
from the worker and schedulers.
"""

from __future__ import annotations

from typing import Optional, TypeVar, Union

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import (
    AuditLog,
    Clause,
    Contract,
    Notification,
    Reminder,
    Task,
)

__all__ = [
    "OwnershipError",
    "contracts_for_user",
    "notifications_for_user",
    "audit_logs_for_user",
    "clauses_for_user",
    "tasks_for_user",
    "reminders_for_user",
    "get_owned_or_none",
    "require_owned",
]


class OwnershipError(Exception):
    """Raised when a referenced entity is not owned by the requesting user.

    To avoid leaking the existence of other tenants' data, this same error is
    used whether the entity does not exist at all or exists but belongs to a
    different user. The web layer maps it to a not-authorized / not-found
    response (typically HTTP 404).
    """

    def __init__(self, entity: str, entity_id: object, user_id: int) -> None:
        self.entity = entity
        self.entity_id = entity_id
        self.user_id = user_id
        super().__init__(
            f"{entity} {entity_id!r} not found for user {user_id}"
        )


# ---------------------------------------------------------------------------
# User-scoped SELECT builders (list / read queries)
# ---------------------------------------------------------------------------
#
# Each helper returns a ``Select`` already constrained to the owning user.
# Callers add their own ordering, pagination, and entity-specific filters on top
# of the returned statement.


def contracts_for_user(user_id: int) -> Select[tuple[Contract]]:
    """Select :class:`Contract` rows directly owned by ``user_id``."""

    return select(Contract).where(Contract.user_id == user_id)


def notifications_for_user(user_id: int) -> Select[tuple[Notification]]:
    """Select :class:`Notification` rows directly owned by ``user_id``."""

    return select(Notification).where(Notification.user_id == user_id)


def audit_logs_for_user(user_id: int) -> Select[tuple[AuditLog]]:
    """Select :class:`AuditLog` rows directly owned by ``user_id``."""

    return select(AuditLog).where(AuditLog.user_id == user_id)


def clauses_for_user(user_id: int) -> Select[tuple[Clause]]:
    """Select :class:`Clause` rows owned transitively via the parent contract.

    Clauses carry no ``user_id``; ownership is reached by joining through
    ``contracts.user_id``.
    """

    return (
        select(Clause)
        .join(Contract, Clause.contract_id == Contract.id)
        .where(Contract.user_id == user_id)
    )


def tasks_for_user(user_id: int) -> Select[tuple[Task]]:
    """Select :class:`Task` rows owned transitively via the parent contract.

    Tasks carry no ``user_id``; ownership is reached by joining through
    ``contracts.user_id``.
    """

    return (
        select(Task)
        .join(Contract, Task.contract_id == Contract.id)
        .where(Contract.user_id == user_id)
    )


def reminders_for_user(user_id: int) -> Select[tuple[Reminder]]:
    """Select :class:`Reminder` rows owned transitively via task -> contract.

    Reminders carry no ``user_id``; ownership is reached by joining through the
    parent task to ``contracts.user_id``.
    """

    return (
        select(Reminder)
        .join(Task, Reminder.task_id == Task.id)
        .join(Contract, Task.contract_id == Contract.id)
        .where(Contract.user_id == user_id)
    )


# ---------------------------------------------------------------------------
# Ownership guards (single-entity lookups)
# ---------------------------------------------------------------------------

# Models the guards understand, keyed by the model class. The mapping records
# both a human-readable name (for error messages) and a builder that produces a
# user-scoped SELECT for that model, so the guard logic stays uniform.
_OwnedModel = Union[Contract, Task, Notification, Clause, Reminder, AuditLog]
_M = TypeVar("_M", bound=_OwnedModel)

_SCOPED_SELECT_BUILDERS = {
    Contract: ("Contract", contracts_for_user),
    Task: ("Task", tasks_for_user),
    Notification: ("Notification", notifications_for_user),
    Clause: ("Clause", clauses_for_user),
    Reminder: ("Reminder", reminders_for_user),
    AuditLog: ("AuditLog", audit_logs_for_user),
}


async def get_owned_or_none(
    session: AsyncSession,
    model: type[_M],
    entity_id: int,
    user_id: int,
) -> Optional[_M]:
    """Fetch ``model`` row ``entity_id`` only if owned by ``user_id``.

    Returns the entity when it exists and is owned by ``user_id``; otherwise
    returns ``None`` -- both for a non-existent id and for an id belonging to a
    different user. The ownership predicate is part of the SELECT, so a
    non-owned row is never read.
    """

    try:
        _entity_name, scoped_builder = _SCOPED_SELECT_BUILDERS[model]
    except KeyError as exc:  # pragma: no cover - guards against misuse
        raise TypeError(
            f"{model!r} is not an ownership-scoped model; expected one of "
            f"{[m.__name__ for m in _SCOPED_SELECT_BUILDERS]}"
        ) from exc

    stmt = scoped_builder(user_id).where(model.id == entity_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def require_owned(
    session: AsyncSession,
    model: type[_M],
    entity_id: int,
    user_id: int,
) -> _M:
    """Fetch ``model`` row ``entity_id`` if owned, else raise :class:`OwnershipError`.

    Same lookup as :func:`get_owned_or_none`, but raises rather than returning
    ``None`` so callers can rely on a non-null result. The raised
    :class:`OwnershipError` does not distinguish "missing" from "not owned",
    preventing enumeration of other tenants' ids. No read or mutation is
    performed on a non-owned row.
    """

    entity = await get_owned_or_none(session, model, entity_id, user_id)
    if entity is None:
        entity_name = _SCOPED_SELECT_BUILDERS[model][0]
        raise OwnershipError(entity_name, entity_id, user_id)
    return entity
