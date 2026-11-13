from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import ValidationError

from app.core.clock import to_iso, utcnow
from app.core.errors import (
    AppError,
    IdempotencyConflictError,
    InjectedCommitFailure,
    InputValidationError,
    InvalidTransitionError,
)
from app.domain.models import (
    EventType,
    Outcome,
    RuleEffect,
    RunState,
    RunStatus,
    SimulationConfig,
    StageConfig,
    TransitionConfig,
    WorkflowConfig,
    canonical_json,
    request_fingerprint,
)
from app.domain.audit_chain import GENESIS_HASH, verify_chain
from app.domain.rule_engine import StageEvaluation, evaluate_rules
from app.domain.state_machine import assert_transition
from app.domain.transitions import (
    DEFAULT_FAILURE_OUTCOMES,
    failure_transition,
    pass_transition,
)
from app.domain.validation import validate_payload
from app.infra.config_loader import WorkflowRegistry
from app.infra.connectors import DependencyOutcome, DependencyRunner
from app.infra.repository import DuplicateIdempotencyKey, RunRepository


@dataclass
class Submission:
    run: RunState
    replayed: bool


EXECUTION_STEP_LIMIT = 100


class WorkflowService:
    def __init__(
        self,
        registry: WorkflowRegistry,
        repository: RunRepository,
        dependency_runner: DependencyRunner | None = None,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.dependencies = dependency_runner or DependencyRunner()

    # ------------------------------------------------------------------ submit
    def submit(
        self,
        workflow_name: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        simulation: SimulationConfig | None = None,
    ) -> Submission:
        workflow = self.registry.get(workflow_name)

        errors = validate_payload(workflow.input_schema, payload)
        if errors:
            raise InputValidationError(
                "request payload failed schema validation", details=errors
            )

        request_hash = request_fingerprint(workflow_name, payload)
        key = idempotency_key.strip() if idempotency_key else f"auto:{request_hash}"

        existing = self.repository.find_by_idempotency_key(key)
        if existing is not None:
            return self._replay(existing, request_hash)

        now = to_iso()
        run = RunState(
            id=str(uuid.uuid4()),
            workflow_name=workflow.name,
            workflow_version=workflow.version,
            workflow_hash=workflow.snapshot_hash(),
            workflow_snapshot=workflow.snapshot(),
            idempotency_key=key,
            request_hash=request_hash,
            status=RunStatus.RECEIVED,
            input=payload,
            context={
                "input": payload,
                "dependencies": {},
                "stage_results": [],
                "simulation": simulation.model_dump(mode="json") if simulation else None,
            },
            created_at=now,
            updated_at=now,
        )

        request_event = self._event(
            EventType.REQUEST_RECEIVED,
            payload={
                "workflow": workflow.name,
                "workflow_version": workflow.version,
                "workflow_hash": run.workflow_hash,
                "idempotency_key": key,
                "request_hash": request_hash,
                "input": payload,
            },
        )
        try:
            self.repository.create_run(run, request_event)
        except DuplicateIdempotencyKey:
            raced = self.repository.find_by_idempotency_key(key)
            if raced is None:
                raise
            return self._replay(raced, request_hash)

        try:
            self._execute(
                run,
                workflow,
                simulation,
                resume=False,
                start_stage=workflow.entry_stage,
            )
        except InjectedCommitFailure as exc:
            raise InjectedCommitFailure(
                exc.message,
                details={
                    "run_id": run.id,
                    "recoverable": True,
                    "resume_url": f"/runs/{run.id}/resume",
                },
            ) from exc
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - mark recoverable failure then surface
            self._mark_failed(run.id, f"unhandled execution error: {exc}")
            raise

        return Submission(run=self.repository.get_run(run.id), replayed=False)

    def _replay(self, existing: RunState, request_hash: str) -> Submission:
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different payload",
                details={
                    "run_id": existing.id,
                    "idempotency_key": existing.idempotency_key,
                },
            )
        return Submission(run=existing, replayed=True)

    # ----------------------------------------------------------------- execute
    def _execute(
        self,
        run: RunState,
        workflow: WorkflowConfig,
        simulation: SimulationConfig | None,
        *,
        resume: bool,
        start_stage: str | None,
    ) -> None:
        stage_name = start_stage or run.current_stage or workflow.entry_stage
        assert stage_name is not None

        if run.status != RunStatus.RUNNING:
            assert_transition(run.status, RunStatus.RUNNING)
            event_type = EventType.RUN_RESUMED if resume else EventType.RUN_STARTED
            run.version = self.repository.commit_stage(
                run.id,
                run.version,
                {
                    "status": RunStatus.RUNNING,
                    "current_stage": stage_name,
                    "context": run.context,
                    "updated_at": to_iso(),
                },
                [
                    self._event(
                        event_type,
                        stage=stage_name,
                        payload={
                            "previous_status": run.status.value,
                            "start_stage": stage_name,
                            "retry_count": run.retry_count,
                        },
                    )
                ],
            )
            run.status = RunStatus.RUNNING
            run.current_stage = stage_name
        elif resume:
            run.version = self.repository.commit_stage(
                run.id,
                run.version,
                {"context": run.context, "updated_at": to_iso()},
                [
                    self._event(
                        EventType.RUN_RESUMED,
                        stage=stage_name,
                        payload={"retry_count": run.retry_count},
                    )
                ],
            )

        steps = 0
        while stage_name:
            steps += 1
            if steps > EXECUTION_STEP_LIMIT:
                self._finish_terminal(
                    run,
                    workflow,
                    Outcome.FAILED,
                    stage_name,
                    "workflow exceeded maximum stage transitions (possible cycle)",
                    None,
                    [],
                    simulation,
                )
                return

            stage = workflow.stage(stage_name)
            events: list[dict[str, Any]] = [
                self._event(
                    EventType.STAGE_STARTED,
                    stage=stage.name,
                    payload={"description": stage.description},
                )
            ]
            dependency_outcome: DependencyOutcome | None = None
            evaluation: StageEvaluation | None = None

            if stage.dependency is not None:
                dependency_outcome = self.dependencies.execute(
                    stage.dependency, run.context, simulation
                )
                for attempt in dependency_outcome.attempts:
                    events.append(
                        self._event(
                            EventType.DEPENDENCY_ATTEMPT,
                            stage=stage.name,
                            payload=attempt.to_dict(),
                        )
                    )
                if not dependency_outcome.ok:
                    effect = RuleEffect(stage.dependency.failure_policy)
                    reason = (
                        f"dependency '{stage.dependency.connector}' failed after "
                        f"{len(dependency_outcome.attempts)} attempt(s): "
                        f"{dependency_outcome.error_message}"
                    )
                    summary = {
                        "stage": stage.name,
                        "status": "dependency_failed",
                        "reason": reason,
                        "decided_by": stage.dependency.connector,
                        "rule_traces": [],
                        "dependency_attempts": [
                            attempt.to_dict() for attempt in dependency_outcome.attempts
                        ],
                        "timestamp": to_iso(),
                    }
                    transition = failure_transition(stage, effect)
                    self._commit_failure(
                        run,
                        workflow,
                        stage,
                        effect,
                        transition,
                        reason,
                        summary,
                        events,
                        simulation,
                        dependency_outcome,
                    )
                    return
                run.context.setdefault("dependencies", {})[
                    stage.dependency.connector
                ] = dependency_outcome.data

            evaluation = evaluate_rules(stage, self._evaluation_context(run))
            for trace in evaluation.traces:
                events.append(
                    self._event(
                        EventType.RULE_EVALUATED,
                        stage=stage.name,
                        payload=trace.to_dict(),
                    )
                )

            if evaluation.branch_goto:
                next_stage = evaluation.branch_goto
                summary = self._stage_summary(
                    stage, evaluation, "branched", next_stage=next_stage
                )
                run.context.setdefault("stage_results", []).append(summary)
                events.append(
                    self._event(
                        EventType.STAGE_COMPLETED,
                        stage=stage.name,
                        payload={
                            **summary,
                            "next_stage": next_stage,
                        },
                    )
                )
                run.version = self.repository.commit_stage(
                    run.id,
                    run.version,
                    {
                        "current_stage": next_stage,
                        "context": run.context,
                        "updated_at": to_iso(),
                    },
                    events,
                    fail_commit=self._commit_fault(simulation),
                )
                run.current_stage = next_stage
                stage_name = next_stage
                continue

            if evaluation.passed:
                transition = pass_transition(workflow, stage)
                status = "passed"
            else:
                assert evaluation.failure_effect is not None
                transition = failure_transition(stage, evaluation.failure_effect)
                status = "failed"

            if transition.outcome is not None:
                self._finish_terminal(
                    run,
                    workflow,
                    transition.outcome,
                    stage.name,
                    evaluation.reason or ("all rules passed" if evaluation.passed else ""),
                    evaluation,
                    events,
                    simulation,
                    dependency_outcome=dependency_outcome,
                )
                return

            assert transition.goto is not None
            next_stage = transition.goto
            summary = self._stage_summary(
                stage, evaluation, status, next_stage=next_stage
            )
            run.context.setdefault("stage_results", []).append(summary)
            events.append(
                self._event(
                    EventType.STAGE_COMPLETED,
                    stage=stage.name,
                    payload={**summary, "next_stage": next_stage},
                )
            )
            run.version = self.repository.commit_stage(
                run.id,
                run.version,
                {
                    "current_stage": next_stage,
                    "context": run.context,
                    "updated_at": to_iso(),
                },
                events,
                fail_commit=self._commit_fault(simulation),
            )
            run.current_stage = next_stage
            stage_name = next_stage

    # ---------------------------------------------------------------- outcomes
    def _finish_terminal(
        self,
        run: RunState,
        workflow: WorkflowConfig,
        outcome: Outcome,
        stage_name: str,
        reason: str,
        evaluation: StageEvaluation | None,
        prior_events: list[dict[str, Any]],
        simulation: SimulationConfig | None,
        *,
        dependency_outcome: DependencyOutcome | None = None,
    ) -> None:
        if outcome == Outcome.RETRY_SCHEDULED:
            self._schedule_retry(
                run,
                workflow,
                stage_name,
                reason,
                evaluation,
                prior_events,
                simulation,
                dependency_outcome,
            )
            return

        status = RunStatus(outcome.value)
        assert_transition(run.status, status)
        summary = (
            self._stage_summary(workflow.stage(stage_name), evaluation, "decided")
            if evaluation is not None
            else {
                "stage": stage_name,
                "status": "dependency_failed",
                "reason": reason,
                "decided_by": workflow.stage(stage_name).dependency.connector
                if workflow.stage(stage_name).dependency
                else None,
                "rule_traces": [],
                "dependency_attempts": [
                    attempt.to_dict() for attempt in dependency_outcome.attempts
                ]
                if dependency_outcome
                else [],
                "timestamp": to_iso(),
            }
        )
        run.context.setdefault("stage_results", []).append(summary)
        result = {
            "outcome": outcome.value,
            "reason": reason,
            "decision_stage": stage_name,
            "decided_by": evaluation.decided_by if evaluation else None,
            "rule_traces": [trace.to_dict() for trace in evaluation.traces]
            if evaluation
            else [],
            "dependency_attempts": [
                attempt.to_dict() for attempt in dependency_outcome.attempts
            ]
            if dependency_outcome
            else [],
            "stage_results": run.context.get("stage_results", []),
            "workflow_version": workflow.version,
            "decided_at": to_iso(),
        }
        events = [
            *prior_events,
            self._event(
                EventType.STAGE_COMPLETED,
                stage=stage_name,
                outcome=outcome,
                payload=summary,
            ),
            self._event(
                EventType.OUTCOME_DECIDED,
                stage=stage_name,
                outcome=outcome,
                payload={
                    "outcome": outcome.value,
                    "reason": reason,
                    "decided_by": result["decided_by"],
                },
            ),
        ]
        run.version = self.repository.commit_stage(
            run.id,
            run.version,
            {
                "status": status,
                "outcome": outcome,
                "current_stage": stage_name,
                "context": run.context,
                "result": result,
                "last_error": None if outcome != Outcome.FAILED else reason,
                "updated_at": to_iso(),
            },
            events,
            fail_commit=self._commit_fault(simulation),
        )
        run.status = status
        run.outcome = outcome
        run.current_stage = stage_name
        run.result = result
        run.last_error = None if outcome != Outcome.FAILED else reason

    def _commit_failure(
        self,
        run: RunState,
        workflow: WorkflowConfig,
        stage: StageConfig,
        effect: RuleEffect,
        transition: TransitionConfig,
        reason: str,
        summary: dict[str, Any],
        prior_events: list[dict[str, Any]],
        simulation: SimulationConfig | None,
        dependency_outcome: DependencyOutcome | None = None,
    ) -> None:
        if transition.goto is not None:
            next_stage = transition.goto
            run.context.setdefault("stage_results", []).append(summary)
            events = [
                *prior_events,
                self._event(
                    EventType.STAGE_COMPLETED,
                    stage=stage.name,
                    outcome=DEFAULT_FAILURE_OUTCOMES.get(effect),
                    payload={**summary, "next_stage": next_stage},
                ),
            ]
            run.version = self.repository.commit_stage(
                run.id,
                run.version,
                {
                    "current_stage": next_stage,
                    "context": run.context,
                    "updated_at": to_iso(),
                },
                events,
                fail_commit=self._commit_fault(simulation),
            )
            run.current_stage = next_stage
            self._execute(
                run,
                workflow,
                simulation,
                resume=True,
                start_stage=next_stage,
            )
            return

        assert transition.outcome is not None
        self._finish_terminal(
            run,
            workflow,
            transition.outcome,
            stage.name,
            reason,
            None,
            prior_events,
            simulation,
            dependency_outcome=dependency_outcome,
        )

    def _schedule_retry(
        self,
        run: RunState,
        workflow: WorkflowConfig,
        stage_name: str,
        reason: str,
        evaluation: StageEvaluation | None,
        prior_events: list[dict[str, Any]],
        simulation: SimulationConfig | None,
        dependency_outcome: DependencyOutcome | None,
    ) -> None:
        new_retry_count = run.retry_count + 1
        if new_retry_count >= workflow.retry.max_workflow_attempts:
            self._finish_terminal(
                run,
                workflow,
                Outcome.FAILED,
                stage_name,
                f"{reason} (retry budget of {workflow.retry.max_workflow_attempts} attempts exhausted)",
                evaluation,
                prior_events,
                simulation,
                dependency_outcome=dependency_outcome,
            )
            return

        next_retry_at = to_iso(
            utcnow() + timedelta(seconds=workflow.retry.workflow_backoff_seconds)
        )
        summary = (
            self._stage_summary(stage_name and workflow.stage(stage_name), evaluation, "retry_scheduled")
            if evaluation
            else {
                "stage": stage_name,
                "status": "dependency_failed",
                "reason": reason,
                "decided_by": None,
                "rule_traces": [],
                "dependency_attempts": [
                    attempt.to_dict() for attempt in dependency_outcome.attempts
                ]
                if dependency_outcome
                else [],
                "timestamp": to_iso(),
            }
        )
        run.context.setdefault("stage_results", []).append(summary)
        result = {
            "outcome": Outcome.RETRY_SCHEDULED.value,
            "reason": reason,
            "decision_stage": stage_name,
            "decided_by": evaluation.decided_by if evaluation else None,
            "rule_traces": [trace.to_dict() for trace in evaluation.traces]
            if evaluation
            else [],
            "dependency_attempts": [
                attempt.to_dict() for attempt in dependency_outcome.attempts
            ]
            if dependency_outcome
            else [],
            "retry_count": new_retry_count,
            "next_retry_at": next_retry_at,
            "stage_results": run.context.get("stage_results", []),
            "workflow_version": workflow.version,
            "decided_at": to_iso(),
        }
        events = [
            *prior_events,
            self._event(
                EventType.STAGE_COMPLETED,
                stage=stage_name,
                outcome=Outcome.RETRY_SCHEDULED,
                payload=summary,
            ),
            self._event(
                EventType.RETRY_SCHEDULED,
                stage=stage_name,
                outcome=Outcome.RETRY_SCHEDULED,
                payload={
                    "reason": reason,
                    "retry_count": new_retry_count,
                    "next_retry_at": next_retry_at,
                    "max_workflow_attempts": workflow.retry.max_workflow_attempts,
                },
            ),
        ]
        run.version = self.repository.commit_stage(
            run.id,
            run.version,
            {
                "status": RunStatus.RETRY_SCHEDULED,
                "outcome": Outcome.RETRY_SCHEDULED,
                "current_stage": stage_name,
                "context": run.context,
                "result": result,
                "retry_count": new_retry_count,
                "next_retry_at": next_retry_at,
                "last_error": reason,
                "updated_at": to_iso(),
            },
            events,
            fail_commit=self._commit_fault(simulation),
        )
        run.status = RunStatus.RETRY_SCHEDULED
        run.outcome = Outcome.RETRY_SCHEDULED
        run.retry_count = new_retry_count
        run.next_retry_at = next_retry_at
        run.result = result
        run.last_error = reason

    # ------------------------------------------------------------------ resume
    def resume_run(
        self,
        run_id: str,
        *,
        simulation: SimulationConfig | None = None,
        automatic: bool = False,
    ) -> RunState:
        run = self.repository.get_run(run_id)
        if run.status not in {
            RunStatus.RETRY_SCHEDULED,
            RunStatus.RUNNING,
            RunStatus.RECEIVED,
        }:
            raise InvalidTransitionError(
                f"run '{run_id}' cannot be resumed from status '{run.status.value}'",
                details={"status": run.status.value},
            )
        if automatic:
            if run.status != RunStatus.RETRY_SCHEDULED:
                return run

        effective_simulation = simulation
        if effective_simulation is None and run.context.get("simulation"):
            effective_simulation = SimulationConfig.model_validate(
                run.context["simulation"]
            )
        if simulation is not None:
            run.context["simulation"] = simulation.model_dump(mode="json")

        workflow = WorkflowConfig.model_validate(run.workflow_snapshot)
        start_stage = run.current_stage or workflow.entry_stage
        try:
            self._execute(
                run,
                workflow,
                effective_simulation,
                resume=True,
                start_stage=start_stage,
            )
        except InjectedCommitFailure as exc:
            raise InjectedCommitFailure(
                exc.message,
                details={
                    "run_id": run.id,
                    "recoverable": True,
                    "resume_url": f"/runs/{run.id}/resume",
                },
            ) from exc
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(run.id, f"unhandled execution error: {exc}")
            raise
        return self.repository.get_run(run.id)

    # ---------------------------------------------------------- manual review
    def resolve_manual_review(
        self,
        run_id: str,
        *,
        decision: str,
        reviewer: str,
        comment: str = "",
    ) -> RunState:
        run = self.repository.get_run(run_id)
        if run.status != RunStatus.MANUAL_REVIEW:
            raise InvalidTransitionError(
                f"run '{run_id}' is not awaiting manual review",
                details={"status": run.status.value},
            )
        if decision not in {"approve", "reject"}:
            raise InvalidTransitionError(
                f"unsupported manual review decision '{decision}'"
            )

        new_status = (
            RunStatus.APPROVED if decision == "approve" else RunStatus.REJECTED
        )
        assert_transition(run.status, new_status)
        result = dict(run.result or {})
        resolution = {
            "decision": decision,
            "reviewer": reviewer,
            "comment": comment,
            "resolved_at": to_iso(),
        }
        result.update(
            {
                "outcome": new_status.value,
                "reason": f"manual review {decision}d by {reviewer}"
                + (f": {comment}" if comment else ""),
                "manual_review": resolution,
            }
        )
        run.version = self.repository.commit_stage(
            run.id,
            run.version,
            {
                "status": new_status,
                "outcome": Outcome(new_status.value),
                "result": result,
                "updated_at": to_iso(),
            },
            [
                self._event(
                    EventType.MANUAL_REVIEW_RESOLVED,
                    stage=run.current_stage,
                    payload=resolution,
                ),
                self._event(
                    EventType.OUTCOME_DECIDED,
                    stage=run.current_stage,
                    outcome=Outcome(new_status.value),
                    payload={
                        "outcome": new_status.value,
                        "reason": result["reason"],
                        "decided_by": reviewer,
                    },
                ),
            ],
        )
        return self.repository.get_run(run.id)

    # ------------------------------------------------------------------ reads
    def get_run(self, run_id: str) -> RunState:
        return self.repository.get_run(run_id)

    def get_audit(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        events = self.repository.list_events(run_id)
        return {
            "run_id": run.id,
            "workflow": run.workflow_name,
            "workflow_version": run.workflow_version,
            "workflow_hash": run.workflow_hash,
            "status": run.status.value,
            "outcome": run.outcome.value if run.outcome else None,
            "input_snapshot": run.input,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "integrity": self._verify_events(run_id, events),
            "events": events,
        }

    def analyze_impact(
        self,
        workflow_name: str,
        candidate_payload: dict[str, Any],
        *,
        sample_size: int = 50,
        inputs: list[dict[str, Any]] | None = None,
        simulation: SimulationConfig | None = None,
        include_inputs: bool = False,
    ) -> dict[str, Any]:
        from app.services.shadow_evaluator import analyze_impact

        workflow = self.registry.get(workflow_name)
        try:
            candidate = WorkflowConfig.model_validate(candidate_payload)
        except ValidationError as exc:
            raise InputValidationError(
                "candidate workflow failed validation",
                details=[
                    {
                        "path": ".".join(str(part) for part in error["loc"]),
                        "message": error["msg"],
                    }
                    for error in exc.errors()
                ],
            ) from exc
        if candidate.name != workflow_name:
            raise InputValidationError(
                "candidate workflow name must match the workflow being analysed",
                details={
                    "expected": workflow_name,
                    "received": candidate.name,
                },
            )

        if inputs is not None:
            source = "request"
            samples = [
                {"input": item, "run_id": None}
                for item in inputs[:sample_size]
            ]
        else:
            source = "history"
            samples = self.repository.recent_inputs(workflow_name, sample_size)

        result = analyze_impact(
            workflow,
            candidate,
            samples,
            simulation=simulation,
            include_inputs=include_inputs,
        )
        result["sample_source"] = source
        return result

    def verify_audit(self, run_id: str) -> dict[str, Any]:
        self.repository.get_run(run_id)
        events = self.repository.list_events(run_id)
        result = self._verify_events(run_id, events)
        result["run_id"] = run_id
        result["genesis_hash"] = GENESIS_HASH
        return result

    def _verify_events(
        self, run_id: str, events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        result = verify_chain(events)
        anchor = self.repository.get_audit_anchor(run_id)
        result["anchor"] = anchor
        if not result["valid"]:
            return result
        anchored_count = anchor.get("event_count") or 0
        if anchored_count and anchored_count != len(events):
            result.update(
                {
                    "valid": False,
                    "broken_at_seq": len(events) + 1,
                    "reason": (
                        "event count does not match the anchored audit count "
                        f"({len(events)} != {anchored_count}); events may have been "
                        "truncated"
                    ),
                }
            )
            return result
        anchored_head = anchor.get("head_hash")
        if anchored_head and result["head_hash"] != anchored_head:
            result.update(
                {
                    "valid": False,
                    "broken_at_seq": events[-1]["seq"] if events else None,
                    "reason": "chain head does not match the anchored audit head",
                }
            )
        return result

    def explain(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        events = self.repository.list_events(run_id)
        result = run.result or {}
        rule_traces = result.get("rule_traces", [])
        triggered = [trace for trace in rule_traces if trace.get("triggered")]
        counterfactuals = [
            trace["counterfactual"]
            for trace in triggered
            if trace.get("counterfactual")
        ]
        return {
            "run_id": run.id,
            "workflow": run.workflow_name,
            "workflow_version": run.workflow_version,
            "workflow_hash": run.workflow_hash,
            "status": run.status.value,
            "outcome": run.outcome.value if run.outcome else None,
            "decision": {
                "stage": result.get("decision_stage"),
                "rule_id": result.get("decided_by"),
                "reason": result.get("reason"),
                "decided_at": result.get("decided_at"),
            },
            "input": run.input,
            "rules_triggered": triggered,
            "counterfactuals": counterfactuals,
            "rule_traces": rule_traces,
            "dependency_attempts": result.get("dependency_attempts", []),
            "stages": result.get("stage_results", []),
            "audit": {
                "event_count": len(events),
                "event_types": [event["event_type"] for event in events],
                "events": events,
            },
        }

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _evaluation_context(run: RunState) -> dict[str, Any]:
        """Expose request fields at the root while keeping explicit namespaces.

        Rules can therefore reference both `applicant.credit_score` and
        `$input.applicant.credit_score` without a second lookup convention.
        """

        input_payload = run.context.get("input", run.input)
        return {
            **input_payload,
            "input": input_payload,
            "dependencies": run.context.get("dependencies", {}),
        }

    @staticmethod
    def _event(
        event_type: EventType,
        *,
        stage: str | None = None,
        outcome: Outcome | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "event_type": event_type.value,
            "stage": stage,
            "outcome": outcome.value if outcome else None,
            "payload": payload or {},
            "created_at": to_iso(),
        }

    @staticmethod
    def _commit_fault(simulation: SimulationConfig | None) -> bool:
        return bool(simulation and simulation.database_fault == "commit_failure")

    @staticmethod
    def _stage_summary(
        stage: StageConfig,
        evaluation: StageEvaluation | None,
        status: str,
        *,
        next_stage: str | None = None,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "stage": stage.name if stage else None,
            "status": status,
            "passed": evaluation.passed if evaluation else None,
            "decided_by": evaluation.decided_by if evaluation else None,
            "reason": evaluation.reason if evaluation else "",
            "rule_traces": [trace.to_dict() for trace in evaluation.traces]
            if evaluation
            else [],
            "timestamp": to_iso(),
        }
        if next_stage:
            summary["next_stage"] = next_stage
        return summary

    def _mark_failed(self, run_id: str, reason: str) -> None:
        try:
            run = self.repository.get_run(run_id)
            if run.status in {RunStatus.APPROVED, RunStatus.REJECTED, RunStatus.FAILED}:
                return
            self.repository.commit_stage(
                run.id,
                run.version,
                {
                    "status": RunStatus.FAILED,
                    "outcome": Outcome.FAILED,
                    "last_error": reason,
                    "updated_at": to_iso(),
                },
                [
                    self._event(
                        EventType.RUN_FAILED,
                        stage=run.current_stage,
                        outcome=Outcome.FAILED,
                        payload={"reason": reason},
                    )
                ],
            )
        except Exception:  # noqa: BLE001 - best-effort failure recording
            pass

    @staticmethod
    def render_configuration(workflow: WorkflowConfig) -> dict[str, Any]:
        return {
            "workflow": workflow.snapshot(),
            "snapshot_hash": workflow.snapshot_hash(),
        }

    @staticmethod
    def canonical_hash(value: Any) -> str:
        import hashlib

        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
