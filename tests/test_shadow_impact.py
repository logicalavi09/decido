from __future__ import annotations

from pathlib import Path

from app.domain.audit_chain import verify_chain
from app.domain.models import SimulationConfig, WorkflowConfig
from app.infra.config_loader import WorkflowRegistry
from app.services.shadow_evaluator import analyze_impact, evaluate_workflow
from tests.helpers import application_payload

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "workflows"


def _workflow(name: str = "application_approval") -> WorkflowConfig:
    registry = WorkflowRegistry(str(WORKFLOWS_DIR))
    registry.load()
    return registry.get(name)


def test_shadow_evaluator_follows_branch_and_captures_triggered_rules():
    payload = application_payload(
        applicant=application_payload()["applicant"] | {"credit_score": 800},
        requested_amount=10_000,
    )
    result = evaluate_workflow(_workflow(), payload)
    assert result.outcome == "approved"
    assert result.stages == ["intake", "credit_check", "auto_approval"]
    assert "premium_fast_track" in result.triggered_rules


def test_shadow_evaluator_resolves_retry_budget_to_failed():
    result = evaluate_workflow(
        _workflow(),
        application_payload(),
        simulation=SimulationConfig(faults={"credit_bureau": "error"}),
    )
    assert result.outcome == "failed"
    assert "retry budget exhausted" in result.reason
    assert result.stages[:2] == ["intake", "credit_check"]
    assert result.stages.count("credit_check") == 4


def test_shadow_impact_reports_schema_invalid_candidates():
    workflow = _workflow()
    candidate = WorkflowConfig.model_validate(workflow.snapshot())
    candidate.input_schema["required"] = [
        *candidate.input_schema.get("required", []),
        "approval_code",
    ]

    impact = analyze_impact(
        workflow, candidate, [{"input": application_payload(), "run_id": "r1"}]
    )
    assert impact["changed"] == 1
    assert impact["transition_matrix"]["approved"]["schema_invalid"] == 1
    assert impact["schema_failures"] == {"active": 0, "candidate": 1}
    assert impact["changed_examples"][0]["schema_errors"][0]["path"] == "$.approval_code"


def test_verify_chain_handles_empty_and_legacy_event_streams():
    empty = verify_chain([])
    assert empty["valid"] is True
    assert empty["head_hash"] is None
    assert empty["event_count"] == 0

    legacy = verify_chain(
        [
            {
                "seq": 1,
                "run_id": "run-1",
                "event_type": "request_received",
                "stage": None,
                "outcome": None,
                "payload": {},
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )
    assert legacy["valid"] is False
    assert legacy["broken_at_seq"] == 1
    assert "missing chain hashes" in legacy["reason"]
