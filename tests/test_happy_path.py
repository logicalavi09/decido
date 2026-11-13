from __future__ import annotations

import pytest

from tests.helpers import (
    VALID_PAYLOADS,
    application_payload,
    audit_events,
    events_of_type,
    submit,
)


def test_application_happy_path(client):
    response = submit(
        client, "application_approval", application_payload(), key="happy-1"
    )
    body = response.json()
    assert response.status_code == 201
    assert body["status"] == "approved"
    assert body["outcome"] == "approved"
    assert body["workflow_version"] == "1.0.0"
    assert body["result"]["reason"] == "all rules passed"

    events = audit_events(client, body["id"])
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    started = events_of_type(events, "stage_started")
    assert {event["stage"] for event in started} >= {
        "intake",
        "credit_check",
        "affordability",
        "auto_approval",
    }
    dependency_attempts = events_of_type(events, "dependency_attempt")
    assert len(dependency_attempts) == 1
    assert dependency_attempts[0]["payload"]["ok"] is True
    assert events_of_type(events, "outcome_decided")[0]["payload"]["outcome"] == "approved"


def test_fast_track_branch_skips_affordability(client):
    payload = application_payload(
        applicant=application_payload()["applicant"] | {"credit_score": 790},
        requested_amount=10000,
    )
    body = submit(client, "application_approval", payload, key="branch-1").json()
    assert body["status"] == "approved"
    stages = body["result"]["stage_results"]
    assert [stage["stage"] for stage in stages] == [
        "intake",
        "credit_check",
        "auto_approval",
    ]
    assert stages[1]["status"] == "branched"
    assert stages[1]["next_stage"] == "auto_approval"
    assert stages[1]["decided_by"] == "premium_fast_track"


@pytest.mark.parametrize("workflow", sorted(VALID_PAYLOADS))
def test_every_configured_workflow_runs_through_the_same_engine(client, workflow):
    body = submit(
        client,
        workflow,
        VALID_PAYLOADS[workflow],
        key=f"generic-{workflow}",
    ).json()
    assert body["workflow"] == workflow
    assert body["status"] == "approved", body["result"]


def test_manual_review_escalation_and_resolution(client):
    payload = application_payload(requested_amount=30000)
    run = submit(client, "application_approval", payload, key="manual-1").json()
    assert run["status"] == "manual_review"
    assert run["result"]["decision_stage"] == "affordability"

    resolved = client.post(
        f"/runs/{run['id']}/resolve",
        json={
            "decision": "approve",
            "reviewer": "credit.officer@example.com",
            "comment": "Verified income documents",
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "approved"
    assert resolved.json()["result"]["manual_review"]["reviewer"] == (
        "credit.officer@example.com"
    )

    events = audit_events(client, run["id"])
    assert events_of_type(events, "manual_review_resolved")
    assert events_of_type(events, "outcome_decided")[-1]["payload"]["outcome"] == "approved"

    again = client.post(
        f"/runs/{run['id']}/resolve",
        json={"decision": "reject", "reviewer": "someone@example.com"},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invalid_state_transition"
