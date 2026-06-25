"""ClauseOps web platform backend package.

This package is the FastAPI + async SQLAlchemy backend for the ClauseOps web
platform. It is deliberately kept separate from the existing ``clauseops`` ML
package, which is imported (never rewritten) by the heavy Celery ``ml`` worker.

Layout:
    app.web         -- FastAPI web layer (routers, dependencies, schemas)
    app.data        -- data layer (SQLAlchemy engine/session, ORM models)
    app.processing  -- Celery processing layer (ML worker, tasks, scheduler)
    app.storage     -- swappable Storage Backend interface + implementations
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
