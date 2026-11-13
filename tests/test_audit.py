from __future__ import annotations

import pytest

from app.core.errors import InvalidTransitionError
from app.domain.models import RunStatus
from app.domain.state_machine import assert_transition
from tests.helpers import application_payload, audit_events, submit


def test_rejected_decision_has_full_audit_and_explanation(client):
    payload = application_payload(
        applicant=application_payload()["applicant"] | {"credit_score": 600}
    )
    run = submit(client, "application_approval", payload, key="audit-reject").json()
    assert run["status"] == "rejected"

    audit = client.get(f"/runs/{run['id']}/audit").json()
    assert audit["input_snapshot"] == payload
    assert audit["workflow_version"] == "1.0.0"
    assert audit["workflow_hash"] == run["workflow_hash"]
    event_types = [event["event_type"] for event in audit["events"]]
    assert event_types[:3] == ["request_received", "run_started", "stage_started"]
    assert event_types[-2:] == ["stage_completed", "outcome_decided"]
    assert all(
        earlier["seq"] < later["seq"]
        for earlier, later in zip(audit["events"], audit["events"][1:])
    )

    explanation = client.get(f"/runs/{run['id']}/explanation").json()
    assert explanation["input"] == payload
    assert explanation["decision"]["rule_id"] == "minimum_credit_score"
    assert explanation["decision"]["stage"] == "credit_check"
    assert explanation["decision"]["reason"] == "Credit score is below the 650 minimum"
    assert explanation["rules_triggered"][0]["rule_id"] == "minimum_credit_score"
    assert explanation["rules_triggered"][0]["field_values"]["applicant.credit_score"] == 600
    assert explanation["dependency_attempts"][0]["ok"] is True
    assert explanation["audit"]["event_count"] == len(audit["events"])


def test_audit_records_dependency_attempts_and_input_reference(client):
    run = submit(
        client,
        "application_approval",
        application_payload(),
        key="audit-dep",
        simulation={"faults": {"credit_bureau": "fail_until_attempt:1"}},
    ).json()
    events = audit_events(client, run["id"])
    attempts = [
        event for event in events if event["event_type"] == "dependency_attempt"
    ]
    assert [attempt["payload"]["attempt"] for attempt in attempts] == [1, 2]
    assert attempts[0]["payload"]["error_code"] == "transient_failure"
    assert attempts[1]["payload"]["data"]["bureau_score"] == 720
    request_event = events[0]
    assert request_event["payload"]["input"] == application_payload()


def test_state_machine_rejects_invalid_transitions():
    assert_transition(RunStatus.RECEIVED, RunStatus.RUNNING)
    with pytest.raises(InvalidTransitionError):
        assert_transition(RunStatus.APPROVED, RunStatus.RUNNING)
    with pytest.raises(InvalidTransitionError):
        assert_transition(RunStatus.RECEIVED, RunStatus.APPROVED)


def test_explanation_is_available_for_manual_review(client):
    run = submit(
        client,
        "application_approval",
        application_payload(requested_amount=30000),
        key="explain-manual",
    ).json()
    explanation = client.get(f"/runs/{run['id']}/explanation").json()
    assert explanation["outcome"] == "manual_review"
    assert explanation["decision"]["rule_id"] == "high_value_review"
    assert "run_resumed" not in explanation["audit"]["event_types"]
