"""Pydantic v2 request/response schemas for the web layer.

Kept dependency-light: email is validated as a non-empty, ``@``-containing
string rather than via the optional ``email-validator`` package, so the schemas
import cleanly in the offline MVP without extra installs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.data.enums import ContractStatus, TaskStatus


class RegisterRequest(BaseModel):
    """Payload for ``POST /auth/register``."""

    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email_nonempty(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or "@" not in normalized:
            raise ValueError("email must be a non-empty, valid email address")
        return normalized

    @field_validator("password")
    @classmethod
    def _password_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("password must be a non-empty string")
        return value


class LoginRequest(BaseModel):
    """Payload for ``POST /auth/login``."""

    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class UserResponse(BaseModel):
    """Public representation of a user (never includes the password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


class ContractUploadResponse(BaseModel):
    """Accepted-upload response for ``POST /contracts`` (HTTP 202).

    Returned once a PDF has passed validation, been stored under a randomized
    object key, registered as a PENDING contract, and enqueued for processing.
    """

    contract_id: int
    job_id: str


class DemoContractSummary(BaseModel):
    """A bundled sample contract offered on the upload page (``GET /demo-contracts``).

    These are read-only catalog entries; processing one (``POST
    /demo-contracts/{slug}``) creates a normal owned contract for the caller.
    """

    slug: str
    title: str
    contract_type: str
    description: str
    size_kb: int


class ContractStatusResponse(BaseModel):
    """Polling-fallback snapshot for ``GET /contracts/{id}/status`` (Requirement 4.5).

    Mirrors the live WebSocket payload's shared fields so a client that cannot
    use a WebSocket can poll the same ``status``/``progress_pct`` it would have
    received as a streamed update. ``stage`` is not persisted on the Contract,
    so it is included only for parity with the live message shape and is left
    ``None`` here.
    """

    model_config = ConfigDict(from_attributes=True)

    contract_id: int
    status: ContractStatus
    progress_pct: int
    stage: Optional[str] = None


class ContractSummary(BaseModel):
    """Lightweight contract row for the list endpoint ``GET /contracts``.

    Deliberately excludes the heavy ``clauses``/``tasks`` collections so the
    list view stays cheap; the full analysis is served by ``GET /contracts/{id}``
    (Requirement 5.4).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    contract_type: Optional[str] = None
    file_size_kb: Optional[int] = None
    effective_date: Optional[date] = None
    status: ContractStatus
    page_count: Optional[int] = None
    progress_pct: int
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class ClauseRead(BaseModel):
    """A segmented, classified clause of a contract (read view)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    clause_index: int
    heading: Optional[str] = None
    clause_type: Optional[str] = None
    body_text: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    confidence: Optional[float] = None


class TaskRead(BaseModel):
    """An obligation/task (read view) carrying the grounding span offsets.

    The ``agent_*``/``action_*`` character offsets index into ``source_text``
    and power the grounded source-span viewer (Requirements 5.2, 5.3, 5.4).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    clause_id: Optional[int] = None
    title: str
    description: Optional[str] = None

    obligated_party: Optional[str] = None
    beneficiary: Optional[str] = None
    obligation_type: Optional[str] = None
    action: Optional[str] = None

    agent_start: Optional[int] = None
    agent_end: Optional[int] = None
    action_start: Optional[int] = None
    action_end: Optional[int] = None

    due_date: Optional[date] = None
    date_type: Optional[str] = None
    priority: Optional[str] = None

    status: TaskStatus
    requires_review: bool

    confidence: float
    agent_score: float
    is_user_corrected: bool
    corrected_fields: Optional[dict[str, Any]] = None

    source_text: str
    created_at: datetime


class TaskUpdate(BaseModel):
    """Partial-update payload for ``PATCH /tasks/{id}`` (Requirement 7).

    Every field is optional so the client may send a status change, one or more
    field corrections, or both. Only the fields explicitly present in the
    request body are applied (resolved via ``model_fields_set`` /
    ``exclude_unset``), so omitting a field leaves it untouched while sending it
    as ``null`` is a deliberate clear.

    ``status`` is typed as :class:`~app.data.enums.TaskStatus`, so a value that
    is not one of PENDING/DONE/SNOOZED/DISMISSED is rejected by validation
    (HTTP 422) before the handler runs -- the Task is therefore never partially
    mutated on an invalid status (Requirement 7.2).

    The remaining fields are the user-correctable subset of a Task; supplying
    any of them records the override in ``corrected_fields`` and flips
    ``is_user_corrected`` (Requirement 7.3).
    """

    model_config = ConfigDict(extra="forbid")

    status: Optional[TaskStatus] = None

    title: Optional[str] = None
    description: Optional[str] = None
    obligated_party: Optional[str] = None
    beneficiary: Optional[str] = None
    action: Optional[str] = None
    due_date: Optional[date] = None
    date_type: Optional[str] = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: Optional[str]) -> Optional[str]:
        """Reject a null/blank title.

        ``title`` is NOT NULL on the Task row, so allowing a client to clear it
        would raise a DB IntegrityError (500). This runs only when ``title`` is
        explicitly present in the request body (validate_default is off), so
        omitting the field still leaves it untouched.
        """

        if value is None or not value.strip():
            raise ValueError("title must be a non-empty string")
        return value
    priority: Optional[str] = None


class ContractDetail(ContractSummary):
    """Full analysis for ``GET /contracts/{id}``: contract + clauses + tasks.

    Extends :class:`ContractSummary` with the contract's clauses and tasks
    (each task including its grounding span offsets), so the grounded viewer can
    render highlights without further round-trips (Requirement 5.4).
    """

    clauses: list[ClauseRead] = []
    tasks: list[TaskRead] = []


class DeadlineEntry(BaseModel):
    """Compact task row for the dashboard's "upcoming deadlines" list.

    Carries only the fields the deadline-first dashboard needs (Requirement
    6.2), deliberately omitting the heavy grounding/source-text payload that
    :class:`TaskRead` exposes. Only tasks with a non-null ``due_date`` reach
    this view, so ``due_date`` is required here.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    title: str
    due_date: date
    priority: Optional[str] = None
    status: TaskStatus
    requires_review: bool


class DashboardSummary(BaseModel):
    """Aggregated dashboard snapshot for ``GET /dashboard/summary`` (Requirement 6.2).

    All figures are scoped to the authenticated owner:

    * ``counts_by_priority`` -- number of tasks per priority bucket. Tasks with
      no priority are grouped under the ``"UNSET"`` key so no task is dropped
      from the totals.
    * ``counts_by_status`` -- number of tasks per :class:`TaskStatus` (keyed by
      the status value).
    * ``requires_review_count`` -- number of tasks flagged ``requires_review``.
    * ``upcoming_deadlines`` -- the soonest tasks with a ``due_date`` on or after
      today, ordered ascending by ``due_date``.
    """

    counts_by_priority: dict[str, int]
    counts_by_status: dict[str, int]
    requires_review_count: int
    upcoming_deadlines: list[DeadlineEntry]


class NotificationRead(BaseModel):
    """An in-app notification delivered to a user (read view).

    Backs ``GET /notifications`` and the response of ``PATCH /notifications/{id}``
    (design "Component 6"). Every notification carries its own ``user_id`` (direct
    ownership), included here so the owning user is explicit in the payload. The
    ``read`` flag reflects whether the user has acknowledged the message.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    task_id: Optional[int] = None
    type: Optional[str] = None
    message: str
    read: bool
    created_at: datetime


class NotificationUpdate(BaseModel):
    """Optional body for ``PATCH /notifications/{id}``.

    The core behavior of the endpoint is to mark a notification read, so ``read``
    defaults to ``True`` when the body is omitted entirely.
    """

    read: bool = True


class TokenResponse(BaseModel):
    """Tokens returned by login/refresh.

    The refresh token is also set in an httpOnly cookie; it is included in the
    body so non-browser clients (and tests) can use it too.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


__all__ = [
    "RegisterRequest",
    "LoginRequest",
    "UserResponse",
    "ContractUploadResponse",
    "DemoContractSummary",
    "ContractStatusResponse",
    "ContractSummary",
    "ClauseRead",
    "TaskRead",
    "TaskUpdate",
    "ContractDetail",
    "DeadlineEntry",
    "DashboardSummary",
    "NotificationRead",
    "NotificationUpdate",
    "TokenResponse",
]
