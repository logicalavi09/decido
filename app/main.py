from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import Settings
from app.core.errors import AppError
from app.infra.config_loader import WorkflowRegistry
from app.infra.connectors import DependencyRunner
from app.infra.db import Database
from app.infra.repository import RunRepository
from app.services.retry_scheduler import RetryScheduler
from app.services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)


def _error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    database = Database(settings.db_path)
    database.initialize()

    registry = WorkflowRegistry(settings.workflows_dir)
    registry.load()

    repository = RunRepository(database)
    service = WorkflowService(registry, repository, DependencyRunner())
    scheduler = RetryScheduler(
        service,
        repository,
        interval_seconds=settings.scheduler_interval_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.scheduler_enabled:
            scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(
        title="Resilient Workflow Decision Platform",
        version="1.1.0",
        description=(
            "Configurable workflow decision platform with declarative rules, "
            "hash-chained audit, counterfactual explanations, idempotent intake, "
            "retryable dependencies, configuration lint/diff and shadow impact analysis."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.registry = registry
    app.state.repository = repository
    app.state.service = service
    app.state.scheduler = scheduler

    app.include_router(router)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "invalid_request",
                "request body failed validation",
                jsonable_validation_errors(exc.errors()),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled request error")
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "unexpected server error"),
        )

    return app


def jsonable_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for error in errors:
        cleaned.append(
            {
                "path": ".".join(str(part) for part in error.get("loc", [])),
                "message": error.get("msg", ""),
                "type": error.get("type", ""),
            }
        )
    return cleaned


app = create_app()
