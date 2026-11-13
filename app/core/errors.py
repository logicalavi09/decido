from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected, structured application failures."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class WorkflowNotFoundError(NotFoundError):
    code = "workflow_not_found"


class RunNotFoundError(NotFoundError):
    code = "run_not_found"


class InputValidationError(AppError):
    status_code = 422
    code = "invalid_input"


class IdempotencyConflictError(AppError):
    status_code = 409
    code = "idempotency_conflict"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class InvalidTransitionError(ConflictError):
    code = "invalid_state_transition"


class ConcurrentModificationError(ConflictError):
    code = "concurrent_modification"


class ConfigError(AppError):
    status_code = 500
    code = "invalid_configuration"


class InjectedCommitFailure(AppError):
    status_code = 503
    code = "partial_save_failure"
