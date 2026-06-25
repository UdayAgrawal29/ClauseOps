"""Processing-enqueue abstraction.

The web layer must be able to enqueue a contract for ML processing and get back
a job identifier *without* hard-depending on the Celery internals that task 8.x
builds out concurrently. This module provides a thin, swappable seam:

* :data:`EnqueueProcessing` -- the callable contract ``(contract_id) -> job_id``.
* :func:`enqueue_contract_processing` -- dispatches the chain to the Celery
  ``ml`` queue and returns the Celery task id (falling back to a generated id if
  the broker is unreachable so the upload still returns 202).
* :func:`get_enqueue_processing` -- a FastAPI dependency returning the callable,
  so it can be overridden in tests or rebound via configuration.

Keeping this behind a function/dependency means the upload endpoint (task 7.1)
is decoupled from the Celery wiring (task 8.1) and only relies on the
``(contract_id) -> job_id`` contract.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable

logger = logging.getLogger(__name__)

# The enqueue contract the web layer depends on: given a contract id, schedule
# the processing chain and return an opaque job identifier.
EnqueueProcessing = Callable[[int], str]


def enqueue_contract_processing(contract_id: int) -> str:
    """Dispatch the ML processing chain for ``contract_id`` to the Celery ``ml`` queue.

    Sends ``app.processing.ml.process_contract`` to Celery and returns the
    resulting task id as the job id. The import is deferred to call time so the
    web layer never imports the Celery app (or, transitively, the heavy ML
    modules) at startup.

    If Celery/Redis is unreachable, we still return a generated job id so the
    upload endpoint can respond ``202`` — the contract simply stays ``PENDING``
    until a worker processes it (or until ``scripts/run_pending.py`` is run).
    """

    from app.processing.ml import process_contract

    try:
        async_result = process_contract.delay(contract_id)
        return str(async_result.id)
    except Exception:  # noqa: BLE001 - broker may be down in dev; don't fail the upload
        logger.warning(
            "Could not dispatch processing for contract %s to Celery; it will "
            "remain PENDING until a worker runs.",
            contract_id,
            exc_info=True,
        )
        return uuid.uuid4().hex


def get_enqueue_processing() -> EnqueueProcessing:
    """Return the configured enqueue callable (overridable as a dependency)."""

    return enqueue_contract_processing


__all__ = [
    "EnqueueProcessing",
    "enqueue_contract_processing",
    "get_enqueue_processing",
]
