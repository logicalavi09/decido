from __future__ import annotations

import logging
import threading
from typing import Callable

from app.core.clock import to_iso
from app.infra.repository import RunRepository
from app.services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)


class RetryScheduler:
    """In-process poller that resumes due retry-scheduled runs.

    This is deliberately small and replaceable: in production the same
    `resume_run(automatic=True)` contract can be driven by SQS/Kafka + a worker
    fleet without changing domain or service code.
    """

    def __init__(
        self,
        service: WorkflowService,
        repository: RunRepository,
        *,
        interval_seconds: float = 1.0,
        clock: Callable[[], str] = to_iso,
    ) -> None:
        self.service = service
        self.repository = repository
        self.interval_seconds = max(0.05, interval_seconds)
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="retry-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - scheduler must not die
                logger.exception("retry scheduler tick failed")
            self._stop.wait(self.interval_seconds)

    def tick(self) -> int:
        resumed = 0
        for run in self.repository.due_retries(self._clock(), limit=25):
            try:
                self.service.resume_run(run.id, automatic=True)
                resumed += 1
            except Exception:  # noqa: BLE001 - one bad run must not block others
                logger.exception("failed to resume run %s", run.id)
        return resumed
