from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.config_lint import lint_summary, lint_workflow
from app.domain.models import (
    Outcome,
    RuleEffect,
    SimulationConfig,
    WorkflowConfig,
)
from app.domain.rule_engine import evaluate_rules
from app.domain.transitions import failure_transition, pass_transition
from app.domain.validation import validate_payload
from app.infra.connectors import DependencyRunner

EXECUTION_STEP_LIMIT = 100
SCHEMA_INVALID = "schema_invalid"


@dataclass
class ShadowResult:
    outcome: str
    reason: str
    decision_stage: str | None
    stages: list[str] = field(default_factory=list)
    triggered_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "decision_stage": self.decision_stage,
            "stages": self.stages,
            "triggered_rules": self.triggered_rules,
        }


def _terminal(
    outcome: Outcome,
    reason: str,
    decision_stage: str | None,
    stages: list[str],
    triggered: list[str],
) -> ShadowResult:
    return ShadowResult(
        outcome=outcome.value,
        reason=reason,
        decision_stage=decision_stage,
        stages=stages,
        triggered_rules=triggered,
    )


def evaluate_workflow(
    workflow: WorkflowConfig,
    payload: dict[str, Any],
    *,
    simulation: SimulationConfig | None = None,
    dependency_runner: DependencyRunner | None = None,
) -> ShadowResult:
    """Evaluate a workflow in memory without writing runs or audit events.

    Retry outcomes are resolved synchronously against the workflow retry
    budget so the analysis reports a terminal decision rather than a schedule.
    """

    runner = dependency_runner or DependencyRunner()
    context: dict[str, Any] = {"input": payload, "dependencies": {}}
    stage_name = workflow.entry_stage
    retry_count = 0
    stages: list[str] = []
    triggered: list[str] = []

    for _ in range(EXECUTION_STEP_LIMIT):
        if stage_name is None:
            break
        stage = workflow.stage(stage_name)
        stages.append(stage.name)

        if stage.dependency is not None:
            dependency_outcome = runner.execute(
                stage.dependency, context, simulation
            )
            if not dependency_outcome.ok:
                effect = RuleEffect(stage.dependency.failure_policy)
                transition = failure_transition(stage, effect)
                reason = (
                    f"dependency '{stage.dependency.connector}' failed: "
                    f"{dependency_outcome.error_message}"
                )
                if transition.goto is not None:
                    stage_name = transition.goto
                    continue
                assert transition.outcome is not None
                if transition.outcome == Outcome.RETRY_SCHEDULED:
                    retry_count += 1
                    if retry_count < workflow.retry.max_workflow_attempts:
                        continue
                    return _terminal(
                        Outcome.FAILED,
                        f"{reason} (retry budget exhausted)",
                        stage.name,
                        stages,
                        triggered,
                    )
                return _terminal(
                    transition.outcome, reason, stage.name, stages, triggered
                )
            context["dependencies"][stage.dependency.connector] = (
                dependency_outcome.data
            )

        evaluation = evaluate_rules(
            stage,
            {
                **payload,
                "input": payload,
                "dependencies": context["dependencies"],
            },
        )
        triggered.extend(
            trace.rule_id for trace in evaluation.traces if trace.triggered
        )

        if evaluation.branch_goto is not None:
            stage_name = evaluation.branch_goto
            continue

        transition = (
            pass_transition(workflow, stage)
            if evaluation.passed
            else failure_transition(stage, evaluation.failure_effect)
        )
        if transition.outcome is not None:
            if transition.outcome == Outcome.RETRY_SCHEDULED:
                retry_count += 1
                if retry_count < workflow.retry.max_workflow_attempts:
                    continue
                return _terminal(
                    Outcome.FAILED,
                    f"{evaluation.reason} (retry budget exhausted)",
                    stage.name,
                    stages,
                    triggered,
                )
            return _terminal(
                transition.outcome,
                evaluation.reason
                or ("all rules passed" if evaluation.passed else ""),
                stage.name,
                stages,
                triggered,
            )

        assert transition.goto is not None
        stage_name = transition.goto

    return _terminal(
        Outcome.FAILED,
        "workflow exceeded maximum stage transitions (possible cycle)",
        stages[-1] if stages else None,
        stages,
        triggered,
    )


def _outcome_counts(results: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome in results:
        counts[outcome] = counts.get(outcome, 0) + 1
    return dict(sorted(counts.items()))


def analyze_impact(
    workflow: WorkflowConfig,
    candidate: WorkflowConfig,
    samples: list[dict[str, Any]],
    *,
    simulation: SimulationConfig | None = None,
    include_inputs: bool = False,
    max_examples: int = 10,
) -> dict[str, Any]:
    matrix: dict[str, dict[str, int]] = {}
    baseline_outcomes: list[str] = []
    candidate_outcomes: list[str] = []
    baseline_schema_failures = 0
    candidate_schema_failures = 0
    changed_examples: list[dict[str, Any]] = []
    changed = 0

    for index, sample in enumerate(samples):
        payload = sample.get("input", sample)
        baseline_errors = validate_payload(workflow.input_schema, payload)
        candidate_errors = validate_payload(candidate.input_schema, payload)

        if baseline_errors:
            baseline_schema_failures += 1
            baseline_outcome = SCHEMA_INVALID
            baseline_detail = {
                "outcome": SCHEMA_INVALID,
                "reason": "input failed active schema validation",
                "decision_stage": None,
                "stages": [],
                "triggered_rules": [],
            }
        else:
            baseline_result = evaluate_workflow(
                workflow, payload, simulation=simulation
            )
            baseline_outcome = baseline_result.outcome
            baseline_detail = baseline_result.to_dict()

        if candidate_errors:
            candidate_schema_failures += 1
            candidate_outcome = SCHEMA_INVALID
            candidate_detail = {
                "outcome": SCHEMA_INVALID,
                "reason": "input failed candidate schema validation",
                "decision_stage": None,
                "stages": [],
                "triggered_rules": [],
            }
        else:
            candidate_result = evaluate_workflow(
                candidate, payload, simulation=simulation
            )
            candidate_outcome = candidate_result.outcome
            candidate_detail = candidate_result.to_dict()

        baseline_outcomes.append(baseline_outcome)
        candidate_outcomes.append(candidate_outcome)
        matrix.setdefault(baseline_outcome, {})
        matrix[baseline_outcome][candidate_outcome] = (
            matrix[baseline_outcome].get(candidate_outcome, 0) + 1
        )

        if baseline_outcome != candidate_outcome:
            changed += 1
            if len(changed_examples) < max_examples:
                example: dict[str, Any] = {
                    "run_id": sample.get("run_id"),
                    "index": index,
                    "baseline": baseline_detail,
                    "candidate": candidate_detail,
                    "schema_errors": candidate_errors or baseline_errors,
                }
                if include_inputs:
                    example["input"] = payload
                changed_examples.append(example)

    candidate_findings = lint_workflow(candidate)
    return {
        "workflow": workflow.name,
        "active_version": workflow.version,
        "candidate_version": candidate.version,
        "active_hash": workflow.snapshot_hash(),
        "candidate_hash": candidate.snapshot_hash(),
        "sample_size": len(samples),
        "changed": changed,
        "changed_percent": round((changed / len(samples)) * 100, 1)
        if samples
        else 0.0,
        "transition_matrix": matrix,
        "outcomes": {
            "active": _outcome_counts(baseline_outcomes),
            "candidate": _outcome_counts(candidate_outcomes),
        },
        "schema_failures": {
            "active": baseline_schema_failures,
            "candidate": candidate_schema_failures,
        },
        "changed_examples": changed_examples,
        "candidate_lint": {
            "summary": lint_summary(candidate_findings),
            "findings": candidate_findings,
        },
    }
