from __future__ import annotations

from app.core.errors import InvalidTransitionError
from app.domain.models import RunStatus

ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.RECEIVED: {RunStatus.RUNNING, RunStatus.FAILED},
    RunStatus.RUNNING: {
        RunStatus.RUNNING,
        RunStatus.APPROVED,
        RunStatus.REJECTED,
        RunStatus.MANUAL_REVIEW,
        RunStatus.RETRY_SCHEDULED,
        RunStatus.FAILED,
    },
    RunStatus.RETRY_SCHEDULED: {RunStatus.RUNNING, RunStatus.FAILED},
    RunStatus.MANUAL_REVIEW: {RunStatus.APPROVED, RunStatus.REJECTED},
    RunStatus.APPROVED: set(),
    RunStatus.REJECTED: set(),
    RunStatus.FAILED: set(),
}

TERMINAL = {
    RunStatus.APPROVED,
    RunStatus.REJECTED,
    RunStatus.FAILED,
}


def assert_transition(current: RunStatus, new: RunStatus) -> None:
    if new not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidTransitionError(
            f"invalid transition from '{current.value}' to '{new.value}'",
            details={"from": current.value, "to": new.value},
        )
