from __future__ import annotations

from app.domain.models import (
    Outcome,
    RuleEffect,
    StageConfig,
    TransitionConfig,
    WorkflowConfig,
)

DEFAULT_FAILURE_OUTCOMES = {
    RuleEffect.REJECT: Outcome.REJECTED,
    RuleEffect.MANUAL_REVIEW: Outcome.MANUAL_REVIEW,
    RuleEffect.RETRY: Outcome.RETRY_SCHEDULED,
}


def pass_transition(
    workflow: WorkflowConfig, stage: StageConfig
) -> TransitionConfig:
    if stage.on_pass is not None:
        return stage.on_pass
    next_stage = workflow.next_stage(stage.name)
    if next_stage:
        return TransitionConfig(goto=next_stage)
    return TransitionConfig(outcome=Outcome.APPROVED)


def failure_transition(stage: StageConfig, effect: RuleEffect) -> TransitionConfig:
    specific = stage.on.get(effect.value)
    if specific is not None:
        return specific
    if stage.on_fail is not None:
        return stage.on_fail
    outcome = DEFAULT_FAILURE_OUTCOMES.get(effect, Outcome.REJECTED)
    return TransitionConfig(outcome=outcome)
