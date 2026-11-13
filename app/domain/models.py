from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Operator(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    MATCHES = "matches"
    BETWEEN = "between"
    LEN_GTE = "len_gte"
    LEN_LTE = "len_lte"
    LEN_EQ = "len_eq"


class RuleMode(str, Enum):
    """assert: condition must hold. guard: condition triggers the effect."""

    ASSERT = "assert"
    GUARD = "guard"


class RuleEffect(str, Enum):
    REJECT = "reject"
    MANUAL_REVIEW = "manual_review"
    RETRY = "retry"
    CONTINUE = "continue"
    BRANCH = "branch"


class Outcome(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MANUAL_REVIEW = "manual_review"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"


class RunStatus(str, Enum):
    RECEIVED = "received"
    RUNNING = "running"
    APPROVED = "approved"
    REJECTED = "rejected"
    MANUAL_REVIEW = "manual_review"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"


class EventType(str, Enum):
    REQUEST_RECEIVED = "request_received"
    INPUT_VALIDATED = "input_validated"
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    STAGE_STARTED = "stage_started"
    DEPENDENCY_ATTEMPT = "dependency_attempt"
    RULE_EVALUATED = "rule_evaluated"
    STAGE_COMPLETED = "stage_completed"
    OUTCOME_DECIDED = "outcome_decided"
    RETRY_SCHEDULED = "retry_scheduled"
    MANUAL_REVIEW_RESOLVED = "manual_review_resolved"
    RUN_FAILED = "run_failed"


TERMINAL_STATUSES = {
    RunStatus.APPROVED,
    RunStatus.REJECTED,
    RunStatus.MANUAL_REVIEW,
    RunStatus.FAILED,
}

OUTCOME_TO_STATUS = {
    Outcome.APPROVED: RunStatus.APPROVED,
    Outcome.REJECTED: RunStatus.REJECTED,
    Outcome.MANUAL_REVIEW: RunStatus.MANUAL_REVIEW,
    Outcome.RETRY_SCHEDULED: RunStatus.RETRY_SCHEDULED,
    Outcome.FAILED: RunStatus.FAILED,
}


class Condition(BaseModel):
    """A small, declarative condition tree.

    A leaf uses {field, op, value}. Composite nodes use exactly one of
    {all: [...]}, {any: [...]} or {not: {...}}.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    all: list["Condition"] | None = None
    any: list["Condition"] | None = None
    not_: "Condition | None" = Field(default=None, alias="not")
    field: str | None = None
    op: Operator | None = None
    value: Any = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "Condition":
        composites = [
            self.all is not None,
            self.any is not None,
            self.not_ is not None,
        ]
        is_leaf = self.field is not None or self.op is not None
        if sum(composites) > 1:
            raise ValueError("condition may use only one of 'all', 'any', 'not'")
        if sum(composites) == 1:
            if is_leaf:
                raise ValueError("leaf fields cannot be combined with all/any/not")
            return self
        if self.field is None or self.op is None:
            raise ValueError("leaf condition requires both 'field' and 'op'")
        return self


class TransitionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goto: str | None = Field(default=None, min_length=1)
    outcome: Outcome | None = None

    @model_validator(mode="after")
    def _validate(self) -> "TransitionConfig":
        if self.goto and self.outcome:
            raise ValueError("transition may not define both 'goto' and 'outcome'")
        if not self.goto and not self.outcome:
            raise ValueError("transition requires 'goto' or 'outcome'")
        return self


class RuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$")
    description: str = ""
    severity: Literal["info", "low", "medium", "high", "critical"] = "medium"
    mode: RuleMode = RuleMode.ASSERT
    condition: Condition
    effect: RuleEffect = RuleEffect.REJECT
    reason: str = ""
    goto: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_branch(self) -> "RuleConfig":
        if self.effect == RuleEffect.BRANCH and not self.goto:
            raise ValueError("effect 'branch' requires 'goto'")
        if self.goto and self.effect != RuleEffect.BRANCH:
            raise ValueError("'goto' is only valid with effect 'branch'")
        return self


class InlineRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=10)
    base_delay_ms: int = Field(default=20, ge=0, le=60_000)
    max_delay_ms: int = Field(default=500, ge=0, le=60_000)

    @model_validator(mode="after")
    def _validate_delays(self) -> "InlineRetryPolicy":
        if self.max_delay_ms < self.base_delay_ms:
            raise ValueError("max_delay_ms must be >= base_delay_ms")
        return self


class DependencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: str = Field(min_length=1)
    operation: str = "check"
    timeout_ms: int = Field(default=2_000, ge=1, le=120_000)
    retry: InlineRetryPolicy = Field(default_factory=InlineRetryPolicy)
    failure_policy: Literal["retry", "manual_review", "reject"] = "retry"
    result: dict[str, Any] = Field(default_factory=dict)


class StageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = ""
    dependency: DependencyConfig | None = None
    rules: list[RuleConfig] = Field(default_factory=list)
    on_pass: TransitionConfig | None = None
    on_fail: TransitionConfig | None = None
    on: dict[str, TransitionConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_on_keys(self) -> "StageConfig":
        valid = {"reject", "manual_review", "retry"}
        invalid = set(self.on) - valid
        if invalid:
            raise ValueError(f"unsupported 'on' keys: {sorted(invalid)}")
        return self


class WorkflowRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_backoff_seconds: float = Field(default=2.0, ge=0, le=86_400)
    max_workflow_attempts: int = Field(default=4, ge=1, le=20)


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    entry_stage: str | None = None
    stages: list[StageConfig] = Field(min_length=1)
    retry: WorkflowRetryPolicy = Field(default_factory=WorkflowRetryPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_graph(self) -> "WorkflowConfig":
        names = [stage.name for stage in self.stages]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate stage names: {sorted(duplicates)}")

        known = set(names)
        if self.entry_stage is None:
            self.entry_stage = names[0]
        elif self.entry_stage not in known:
            raise ValueError(f"entry_stage '{self.entry_stage}' does not exist")

        targets: list[str] = []
        for stage in self.stages:
            for transition in [stage.on_pass, stage.on_fail, *stage.on.values()]:
                if transition and transition.goto:
                    targets.append(transition.goto)
            for rule in stage.rules:
                if rule.goto:
                    targets.append(rule.goto)
        unknown = set(targets) - known
        if unknown:
            raise ValueError(f"transitions reference unknown stages: {sorted(unknown)}")
        return self

    def stage(self, name: str) -> StageConfig:
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(name)

    def stage_names(self) -> list[str]:
        return [stage.name for stage in self.stages]

    def next_stage(self, name: str) -> str | None:
        index = self.stage_names().index(name)
        if index + 1 < len(self.stages):
            return self.stages[index + 1].name
        return None

    def snapshot(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def snapshot_json(self) -> str:
        return canonical_json(self.snapshot())

    def snapshot_hash(self) -> str:
        return hashlib.sha256(self.snapshot_json().encode("utf-8")).hexdigest()


class SimulationConfig(BaseModel):
    """Test/demo-only fault injection. Never used for business decisions."""

    model_config = ConfigDict(extra="forbid")

    faults: dict[str, str] = Field(default_factory=dict)
    responses: dict[str, Any] = Field(default_factory=dict)
    latency_ms: dict[str, int] = Field(default_factory=dict)
    database_fault: Literal["commit_failure"] | None = None


@dataclass
class RunState:
    id: str
    workflow_name: str
    workflow_version: str
    workflow_hash: str
    workflow_snapshot: dict[str, Any]
    idempotency_key: str
    request_hash: str
    status: RunStatus
    input: dict[str, Any]
    context: dict[str, Any]
    outcome: Outcome | None = None
    current_stage: str | None = None
    result: dict[str, Any] | None = None
    retry_count: int = 0
    next_retry_at: str | None = None
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    version: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def request_fingerprint(workflow: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json({"workflow": workflow, "input": payload}).encode("utf-8")
    ).hexdigest()
