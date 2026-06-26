"""FastAPI application factory for the ClauseOps web platform.

Wires the web-layer routers into a FastAPI app. The Auth Service router
(task 4.2), the Contract Upload & Ingestion router (task 7.1), the Progress
Channel router (task 9.2 -- the WebSocket stream plus the status-polling
fallback), and the Task list router (task 10.2) are mounted; remaining read and
task routers are added by later tasks. CORS is locked to the SPA origin via
configuration.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.data.ownership import OwnershipError
from app.web.auth_router import router as auth_router
from app.web.contracts_router import router as contracts_router
from app.web.dashboard_router import router as dashboard_router
from app.web.notifications_router import router as notifications_router
from app.web.progress_router import router as progress_router
from app.web.review_router import router as review_router
from app.web.tasks_router import router as tasks_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    settings = get_settings()

    app = FastAPI(
        title="ClauseOps Web Platform",
        version="0.1.0",
        description="Offline-first contract obligation tracker.",
    )

    # CORS: restrict to the configured SPA origin(s). Credentials are allowed so
    # the httpOnly refresh cookie flows on /auth/refresh and /auth/logout.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(OwnershipError)
    async def _ownership_error_handler(
        _request: Request, _exc: OwnershipError
    ) -> JSONResponse:
        """Map a non-owned/missing entity reference to a uniform 404.

        ``OwnershipError`` deliberately does not distinguish "missing" from
        "not owned", so every reference to an entity the caller does not own
        returns the same not-found response and never reveals another tenant's
        data (Requirement 2.2).
        """

        return JSONResponse(status_code=404, content={"detail": "Not found"})

    @app.exception_handler(IntegrityError)
    async def _integrity_error_handler(
        _request: Request, _exc: IntegrityError
    ) -> JSONResponse:
        """Map a DB constraint violation to a clean 400 instead of a 500.

        Guards against e.g. clearing a NOT NULL field via PATCH or a duplicate
        unique value racing a check-then-insert; the offending transaction is
        rolled back by the session context manager.
        """

        return JSONResponse(
            status_code=400,
            content={"detail": "The request violates a data constraint."},
        )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        """Liveness probe."""

        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(contracts_router)
    app.include_router(progress_router)
    app.include_router(tasks_router)
    app.include_router(review_router)
    app.include_router(dashboard_router)
    app.include_router(notifications_router)
    return app


# Module-level app instance for ``uvicorn app.web.main:app``.
app = create_app()


__all__ = ["create_app", "app"]
