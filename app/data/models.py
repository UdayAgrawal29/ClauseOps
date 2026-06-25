"""SQLAlchemy 2.0 (async) ORM models for the ClauseOps web platform.

These models implement the relational schema from the design's "Data Models"
section. Every row is scoped by ownership: a ``user_id`` lives directly on the
top-level owned entities (``contracts``, ``notifications``, ``audit_log``) while
``clauses``, ``tasks``, and ``reminders`` are owned transitively through their
parent contract via FK relationships with ``ON DELETE CASCADE`` so that deleting
a contract cascades to all of its derived records.

The ``Task`` model maps 1:1 to the pipeline's ``TaskRecord`` and carries the
character span offsets that power the grounded source-span viewer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.data.database import Base
from app.data.enums import ContractStatus, ReminderChannel, TaskStatus

# JSONB on PostgreSQL, falling back to generic JSON on other dialects (e.g. for
# lightweight test databases) so the models remain importable everywhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    """An authenticated account that owns contracts, tasks, and notifications."""

    __tablename__ = "users"
    __table_args__ = (
        # email must be non-empty at the DB level (uniqueness is on the column).
        CheckConstraint("length(email) > 0", name="ck_users_email_nonempty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    contracts: Mapped[list["Contract"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    @validates("email")
    def _validate_email(self, key: str, value: Optional[str]) -> str:
        """Reject null/blank emails and normalize to lowercase.

        Lowercasing here (every write path goes through the ORM) makes the
        unique index effective against case-variant duplicates
        (``A@x.com`` vs ``a@x.com``).
        """

        if value is None or not str(value).strip():
            raise ValueError("email must be a non-empty string")
        return str(value).strip().lower()


class Contract(Base):
    """An uploaded contract PDF and its processing status."""

    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint(
            "progress_pct >= 0 AND progress_pct <= 100",
            name="ck_contracts_progress_pct_range",
        ),
        # error_message is non-null only when status == FAILED, and null otherwise.
        CheckConstraint(
            "(status = 'FAILED' AND error_message IS NOT NULL) "
            "OR (status <> 'FAILED' AND error_message IS NULL)",
            name="ck_contracts_error_message_requires_failed",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Randomized, non-guessable object key stored outside the web root.
    file_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    file_size_kb: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    contract_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    status: Mapped[ContractStatus] = mapped_column(
        SAEnum(ContractStatus, name="contract_status"),
        default=ContractStatus.PENDING,
        nullable=False,
        index=True,
    )
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Populated only when status == FAILED (enforced in task 2.2).
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="contracts")
    clauses: Mapped[list["Clause"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan", passive_deletes=True
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan", passive_deletes=True
    )

    @validates("progress_pct")
    def _validate_progress_pct(self, key: str, value: int) -> int:
        """progress_pct must be an integer in [0, 100]."""

        if value is None:
            raise ValueError("progress_pct must not be null")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("progress_pct must be an integer")
        if not 0 <= value <= 100:
            raise ValueError("progress_pct must be between 0 and 100")
        return value

    @validates("status")
    def _validate_status(self, key: str, value: ContractStatus) -> ContractStatus:
        """Keep status/error_message consistent: clearing FAILED requires no message."""

        if value != ContractStatus.FAILED and self.error_message is not None:
            raise ValueError(
                "error_message must be null unless status is FAILED"
            )
        return value

    @validates("error_message")
    def _validate_error_message(self, key: str, value: Optional[str]) -> Optional[str]:
        """error_message may only be populated when status == FAILED."""

        if value is not None and self.status not in (None, ContractStatus.FAILED):
            raise ValueError(
                "error_message may only be set when status is FAILED"
            )
        return value


class Clause(Base):
    """A segmented, classified unit of a contract."""

    __tablename__ = "clauses"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_clauses_confidence_unit_range",
        ),
        # A contract's clause ordinals are unique, so reprocessing (which deletes
        # and re-inserts a contract's derived rows) can never leave duplicate
        # clause_index values and the viewer ordering is deterministic.
        UniqueConstraint(
            "contract_id", "clause_index", name="uq_clauses_contract_clause_index"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Zero-based ordinal of the clause within its contract.
    clause_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    clause_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Reserved for the Phase-2 PDF bbox overlay.
    page_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    contract: Mapped["Contract"] = relationship(back_populates="clauses")
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="clause", passive_deletes=True
    )

    @validates("confidence")
    def _validate_confidence(self, key: str, value: Optional[float]) -> Optional[float]:
        """confidence, when present, is a float in [0, 1]."""

        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("confidence must be a number")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value


class Task(Base):
    """An obligation derived from a clause; maps 1:1 to the pipeline ``TaskRecord``.

    The ``agent_*`` / ``action_*`` character offsets index into ``source_text``
    and are the foundation of the grounded source-span viewer.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_tasks_confidence_unit_range",
        ),
        CheckConstraint(
            "agent_score >= 0.0 AND agent_score <= 1.0",
            name="ck_tasks_agent_score_unit_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clause_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("clauses.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    obligated_party: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    beneficiary: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    obligation_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Character offsets into ``source_text`` for highlighting (grounding spans).
    agent_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    agent_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    date_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    priority: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)

    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, name="task_status"),
        default=TaskStatus.PENDING,
        nullable=False,
        index=True,
    )
    requires_review: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )

    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    agent_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    is_user_corrected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # JSON object recording user field overrides.
    corrected_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONType, nullable=True
    )

    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    contract: Mapped["Contract"] = relationship(back_populates="tasks")
    clause: Mapped[Optional["Clause"]] = relationship(back_populates="tasks")
    reminders: Mapped[list["Reminder"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )

    @validates("confidence", "agent_score")
    def _validate_unit_interval(self, key: str, value: float) -> float:
        """confidence and agent_score are floats in [0, 1]."""

        if value is None:
            raise ValueError(f"{key} must not be null")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{key} must be between 0 and 1")
        return value


class Reminder(Base):
    """A scheduled prompt tied to a Task, delivered at most once."""

    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('email', 'in_app')",
            name="ck_reminders_channel_domain",
        ),
        # sent_at may only be populated once the reminder has been sent.
        CheckConstraint(
            "sent_at IS NULL OR sent = true",
            name="ck_reminders_sent_at_requires_sent",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )

    remind_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    channel: Mapped[ReminderChannel] = mapped_column(
        SAEnum(
            ReminderChannel,
            name="reminder_channel",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=ReminderChannel.IN_APP,
        nullable=False,
    )
    sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # Populated only when ``sent`` is true.
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    task: Mapped["Task"] = relationship(back_populates="reminders")

    @validates("channel")
    def _validate_channel(self, key: str, value: Any) -> ReminderChannel:
        """channel must be one of the ReminderChannel members (email, in_app)."""

        if isinstance(value, ReminderChannel):
            return value
        try:
            return ReminderChannel(value)
        except ValueError as exc:
            raise ValueError("channel must be one of: email, in_app") from exc

    @validates("sent")
    def _validate_sent(self, key: str, value: bool) -> bool:
        """Clearing ``sent`` is not allowed while ``sent_at`` is populated."""

        if value is False and self.sent_at is not None:
            raise ValueError("sent_at must be null when sent is false")
        return value

    @validates("sent_at")
    def _validate_sent_at(
        self, key: str, value: Optional[datetime]
    ) -> Optional[datetime]:
        """sent_at may only be populated when ``sent`` is true."""

        if value is not None and self.sent is False:
            raise ValueError("sent_at may only be set when sent is true")
        return value


class Notification(Base):
    """An in-app message delivered to a user."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )

    type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="notifications")
    task: Mapped[Optional["Task"]] = relationship(back_populates="notifications")


class AuditLog(Base):
    """Append-only record of before/after field values for task mutations."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)

    before: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    after: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "User",
    "Contract",
    "Clause",
    "Task",
    "Reminder",
    "Notification",
    "AuditLog",
]
