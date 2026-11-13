from __future__ import annotations

import copy
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from app.domain.models import DependencyConfig, SimulationConfig
from app.domain.rule_engine import MISSING, resolve_path


@dataclass
class AdapterResult:
    ok: bool
    data: Any = None
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: int = 0
    fault: str | None = None


class ConnectorAdapter(Protocol):
    """Seam for real integrations.

    A production adapter (credit bureau, OCR vendor, tax registry) is
    registered by connector name on DependencyRunner. Retry, backoff, audit
    events and decision policy stay in the platform and do not need to change.
    """

    def invoke(
        self,
        *,
        dependency: DependencyConfig,
        context: dict[str, Any],
        attempt: int,
        simulation: SimulationConfig | None,
    ) -> AdapterResult: ...


@dataclass
class DependencyAttempt:
    connector: str
    operation: str
    attempt: int
    ok: bool
    latency_ms: int
    error_code: str | None = None
    error_message: str | None = None
    fault: str | None = None
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "operation": self.operation,
            "attempt": self.attempt,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "fault": self.fault,
            "data": self.data,
        }


@dataclass
class DependencyOutcome:
    ok: bool
    data: Any = None
    attempts: list[DependencyAttempt] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    @property
    def retry_count(self) -> int:
        return max(0, len(self.attempts) - 1)


def _resolve_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        path = value[1:]
        if path.startswith("."):
            path = path[1:]
        resolved = resolve_path(context, path)
        return None if resolved is MISSING else copy.deepcopy(resolved)
    if isinstance(value, dict):
        return {key: _resolve_template(child, context) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve_template(child, context) for child in value]
    return value


class SimulatedConnectorAdapter:
    """Deterministic fake external dependency used by the demo and tests.

    Fault modes (supplied under `simulation.faults[connector]`):
      - error / unavailable
      - timeout
      - fail_until_attempt:<n>   (fail attempts 1..n, then succeed)
      - flaky:<0..1>
    """

    def invoke(
        self,
        *,
        dependency: DependencyConfig,
        context: dict[str, Any],
        attempt: int,
        simulation: SimulationConfig | None,
    ) -> AdapterResult:
        fault = simulation.faults.get(dependency.connector) if simulation else None
        latency = simulation.latency_ms.get(dependency.connector, 0) if simulation else 0
        latency = max(0, latency)

        if latency > dependency.timeout_ms:
            return AdapterResult(
                ok=False,
                error_code="dependency_timeout",
                error_message=(
                    f"{dependency.connector} exceeded timeout of "
                    f"{dependency.timeout_ms}ms"
                ),
                latency_ms=dependency.timeout_ms,
                fault=fault,
            )

        ok, error_code, error_message = self._evaluate_fault(
            dependency.connector, fault, attempt
        )
        if not ok:
            return AdapterResult(
                ok=False,
                error_code=error_code,
                error_message=error_message,
                latency_ms=latency,
                fault=fault,
            )

        override = (
            simulation.responses.get(dependency.connector)
            if simulation and dependency.connector in simulation.responses
            else None
        )
        template = override if override is not None else dependency.result
        data = _resolve_template(
            template,
            {
                "input": context.get("input", {}),
                "dependencies": context.get("dependencies", {}),
            },
        )
        return AdapterResult(ok=True, data=data, latency_ms=latency, fault=fault)

    @staticmethod
    def _evaluate_fault(
        connector: str, fault: str | None, attempt: int
    ) -> tuple[bool, str | None, str | None]:
        if not fault or fault in {"ok", "success", "none"}:
            return True, None, None

        if fault == "timeout":
            return (
                False,
                "dependency_timeout",
                f"{connector} timed out",
            )

        if fault in {"error", "unavailable"}:
            return (
                False,
                "dependency_unavailable",
                f"{connector} is unavailable",
            )

        if fault.startswith("fail_until_attempt:"):
            try:
                threshold = int(fault.split(":", 1)[1])
            except ValueError:
                threshold = 0
            if attempt <= threshold:
                return (
                    False,
                    "transient_failure",
                    f"{connector} transient failure on attempt {attempt}",
                )
            return True, None, None

        if fault.startswith("flaky:"):
            try:
                rate = float(fault.split(":", 1)[1])
            except ValueError:
                rate = 0.0
            if random.random() < rate:
                return False, "transient_failure", f"{connector} flaky failure"
            return True, None, None

        return (
            False,
            "dependency_error",
            f"unknown simulated fault '{fault}' for {connector}",
        )


class DependencyRunner:
    """Owns timeout/retry/backoff and audit-friendly attempt recording.

    Integrations are plugged in via `adapters` keyed by connector name. Any
    connector without an adapter falls back to the simulated implementation.
    """

    def __init__(
        self,
        adapters: dict[str, ConnectorAdapter] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapters = adapters or {}
        self._simulated = SimulatedConnectorAdapter()
        self._sleep = sleeper

    def register(self, connector: str, adapter: ConnectorAdapter) -> None:
        self._adapters[connector] = adapter

    def execute(
        self,
        dependency: DependencyConfig,
        context: dict[str, Any],
        simulation: SimulationConfig | None,
    ) -> DependencyOutcome:
        attempts: list[DependencyAttempt] = []
        retry_policy = dependency.retry
        adapter = self._adapters.get(dependency.connector, self._simulated)

        last_error_code: str | None = None
        last_error_message: str | None = None

        for attempt_number in range(1, retry_policy.max_attempts + 1):
            response = adapter.invoke(
                dependency=dependency,
                context=context,
                attempt=attempt_number,
                simulation=simulation,
            )
            if response.ok:
                attempts.append(
                    DependencyAttempt(
                        connector=dependency.connector,
                        operation=dependency.operation,
                        attempt=attempt_number,
                        ok=True,
                        latency_ms=response.latency_ms,
                        fault=response.fault,
                        data=response.data,
                    )
                )
                return DependencyOutcome(
                    ok=True, data=response.data, attempts=attempts
                )

            last_error_code = response.error_code
            last_error_message = response.error_message
            attempts.append(
                DependencyAttempt(
                    connector=dependency.connector,
                    operation=dependency.operation,
                    attempt=attempt_number,
                    ok=False,
                    latency_ms=response.latency_ms,
                    error_code=response.error_code,
                    error_message=response.error_message,
                    fault=response.fault,
                )
            )
            if attempt_number < retry_policy.max_attempts:
                delay_ms = min(
                    retry_policy.base_delay_ms * (2 ** (attempt_number - 1)),
                    retry_policy.max_delay_ms,
                )
                if delay_ms > 0:
                    self._sleep(delay_ms / 1000.0)

        return DependencyOutcome(
            ok=False,
            attempts=attempts,
            error_code=last_error_code,
            error_message=last_error_message,
        )
