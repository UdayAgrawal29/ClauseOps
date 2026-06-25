"""Enumerated domains used by the ORM models.

These encode the status/channel domains from the design's Data Models section so
they are enforced at the type and database level.
"""

from __future__ import annotations

import enum


class ContractStatus(str, enum.Enum):
    """Lifecycle status of a :class:`~app.data.models.Contract`."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class TaskStatus(str, enum.Enum):
    """Workflow status of a :class:`~app.data.models.Task`."""

    PENDING = "PENDING"
    DONE = "DONE"
    SNOOZED = "SNOOZED"
    DISMISSED = "DISMISSED"


class ReminderChannel(str, enum.Enum):
    """Delivery channel for a :class:`~app.data.models.Reminder`.

    For the MVP only ``IN_APP`` is actually delivered; ``EMAIL`` is reserved for
    the deferred email integration.
    """

    EMAIL = "email"
    IN_APP = "in_app"


__all__ = ["ContractStatus", "TaskStatus", "ReminderChannel"]
