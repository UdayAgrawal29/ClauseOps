"""Celery application configuration for the ClauseOps processing layer.

This module defines the Celery app, two isolated queues, and the task routing
that keeps the heavy ML pipeline off the light default queue:

* ``default`` — light, API-driven background work.
* ``ml`` — the dedicated heavy queue. The existing ``clauseops`` pipeline
  (Docling + spaCy + transformers + torch) runs here, isolated so ~40s/contract
  jobs never block light tasks.

Design constraints honored here (see design.md "Component 4: Celery ML Worker"):

* Redis (``settings.redis_url``) is used as BOTH the broker and the result
  backend.
* Heavy models are **warm-loaded once per worker process** via a cached loader
  invoked from the ``worker_process_init`` signal — but only on the heavy ``ml``
  worker (gated by the ``CLAUSEOPS_ML_WORKER`` env var). Nothing heavy is
  imported or loaded at module import time, so this module imports cleanly
  without a running Redis and without triggering model downloads.
* The ``ml`` worker makes **no outbound network calls**. There are no
  module-level network calls anywhere in this file.

The actual pipeline task chain is implemented in a later task (8.2); this module
only sets up the app, queues, routing, and the warm-load scaffolding.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Ensure the repository root (which contains BOTH the ``app`` and the existing
# ``clauseops`` packages) is importable inside the Celery worker process. When a
# worker is launched via the ``celery`` console script on Windows, the project
# root is not always on ``sys.path``, which makes the lazy ``import clauseops``
# in :func:`get_pipeline` fail with ``ModuleNotFoundError: No module named
# 'clauseops'`` even though ``app`` imported fine. Inserting it here guarantees
# the heavy pipeline package is importable regardless of how the worker started.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from celery import Celery
from kombu import Queue

from app.config import get_settings

logger = logging.getLogger(__name__)

# Queue names kept as module constants so task modules and worker launch
# commands (``celery -A app.processing.celery_app worker -Q ml``) share one
# source of truth.
DEFAULT_QUEUE = "default"
ML_QUEUE = "ml"

# Namespace for heavy ML pipeline tasks. Any task registered under
# ``app.processing.ml.*`` is routed to the dedicated heavy ``ml`` queue.
ML_TASK_NAMESPACE = "app.processing.ml"

_settings = get_settings()

celery_app = Celery(
    "clauseops",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    # The heavy pipeline tasks live in ``app.processing.ml`` (task 8.2); the
    # light periodic reminder scan lives in ``app.processing.reminders`` (task
    # 13.2). Listing them here lets the worker autodiscover their tasks and lets
    # Celery Beat pick up the reminder schedule registered on import. Importing
    # these names is lazy and does not pull in the clauseops models or connect
    # to Redis/DB.
    include=["app.processing.ml", "app.processing.reminders"],
)

# Two isolated queues. ``default`` is where light tasks land unless a route
# sends them elsewhere; ``ml`` is reserved for the heavy pipeline.
celery_app.conf.task_queues = (
    Queue(DEFAULT_QUEUE),
    Queue(ML_QUEUE),
)

# Light tasks default to the ``default`` queue...
celery_app.conf.task_default_queue = DEFAULT_QUEUE

# ...and heavy ML pipeline tasks (the ``app.processing.ml.*`` namespace) are
# routed to the isolated ``ml`` queue so they never block light API work.
celery_app.conf.task_routes = {
    f"{ML_TASK_NAMESPACE}.*": {"queue": ML_QUEUE},
}

celery_app.conf.update(
    # Use JSON for transport — no pickling of arbitrary Python objects.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Heavy jobs run ~40s each; fetch one at a time so a slow contract does not
    # starve a sibling that could run on another worker process.
    worker_prefetch_multiplier=1,
    # Acknowledge only after the task returns, so a crashed heavy worker does
    # not silently drop an in-flight contract.
    task_acks_late=True,
    timezone="UTC",
    enable_utc=True,
)


# ---------------------------------------------------------------------------
# Warm-load scaffolding
# ---------------------------------------------------------------------------
#
# The clauseops pipeline loads several large models (Docling converter,
# spaCy-trf, two transformers, torch). Loading them per task would dominate
# runtime, so the heavy ``ml`` worker loads them once per process. We expose a
# cached accessor that imports the pipeline callables lazily, plus a best-effort
# warm-load hook wired to ``worker_process_init``.
#
# Importing this module never triggers any of this — the clauseops imports
# happen only inside the functions below, which run in the worker process.


def _is_ml_worker() -> bool:
    """Return True when this process is the heavy ``ml`` worker.

    Gated by the ``CLAUSEOPS_ML_WORKER`` environment variable so that light
    default workers (and test/import contexts) never pull in the heavy models.
    The heavy worker is launched with this flag set, e.g.::

        CLAUSEOPS_ML_WORKER=1 celery -A app.processing.celery_app worker -Q ml
    """

    return os.environ.get("CLAUSEOPS_ML_WORKER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# Process-local cache of the imported pipeline callables. Populated lazily on
# first access within a worker process and reused for every subsequent task.
_pipeline = None


def get_pipeline():
    """Return the clauseops pipeline callables, importing them once per process.

    The existing ``clauseops`` package is imported here and never rewritten. The
    import is deferred to call time (not module import) so that simply importing
    :mod:`app.processing.celery_app` stays cheap and free of model loading.

    Returns a dict mapping stage name to the corresponding clauseops callable.
    """

    global _pipeline
    if _pipeline is not None:
        return _pipeline

    # Lazy, in-process imports of the existing pipeline (no rewrite). The module
    # paths below are the *verified* public entry points of the ``clauseops``
    # package and cover every stage of the design's task chain:
    # segment -> classify -> ner -> obligations -> normalize_dates -> generate_tasks.
    from clauseops.clause_classification.classifier import classify_clauses
    from clauseops.entity_extraction.extractor import (
        extract_entities_from_contract,
    )
    from clauseops.obligation_detection.deontic_classifier import (
        classify_contract_obligations,
    )
    from clauseops.obligation_detection.date_normalizer import (
        normalize_contract_dates,
    )
    from clauseops.obligation_detection.task_generator import (
        generate_tasks_for_contract,
    )
    from clauseops.segmentation import segment_contract

    _pipeline = {
        "segment_contract": segment_contract,
        "classify_clauses": classify_clauses,
        "extract_entities_from_contract": extract_entities_from_contract,
        "classify_contract_obligations": classify_contract_obligations,
        "normalize_contract_dates": normalize_contract_dates,
        "generate_tasks_for_contract": generate_tasks_for_contract,
    }
    return _pipeline


def warm_load_models() -> None:
    """Warm-load the heavy pipeline models once for this worker process.

    This binds the clauseops callables into the process-local cache via
    :func:`get_pipeline`. The clauseops modules use singleton model loaders that
    materialize on first inference; importing them here primes the cache so the
    first real task does not pay the import cost. This makes no outbound network
    calls — models are loaded from the local filesystem.

    Best-effort: if the local model artifacts are not present yet, we log and
    continue rather than crashing the worker bootstrap, leaving the loading to
    happen lazily when the first task runs.
    """

    try:
        get_pipeline()
        logger.info("clauseops pipeline callables warm-loaded for ml worker")
    except Exception:  # noqa: BLE001 - bootstrap must not hard-crash here
        logger.warning(
            "Warm-load of clauseops pipeline deferred (models will load lazily)",
            exc_info=True,
        )


def _register_warm_load() -> None:
    """Connect the warm-load hook to ``worker_process_init`` for the ml worker.

    Connecting the signal is cheap and does not import clauseops. The handler
    only does heavy work when this process is the ml worker, so light default
    workers and import-time consumers are unaffected.
    """

    from celery.signals import worker_process_init

    @worker_process_init.connect
    def _on_worker_process_init(**_kwargs) -> None:  # pragma: no cover - hook
        if _is_ml_worker():
            warm_load_models()


_register_warm_load()
