"""Data layer: async SQLAlchemy engine/session and ORM models."""

from app.data.database import Base, get_engine, get_session, get_sessionmaker
from app.data.enums import ContractStatus, ReminderChannel, TaskStatus
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

__all__ = [
    "Base",
    "get_engine",
    "get_sessionmaker",
    "get_session",
    "ContractStatus",
    "TaskStatus",
    "ReminderChannel",
    "User",
    "Contract",
    "Clause",
    "Task",
    "Reminder",
    "Notification",
    "AuditLog",
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
