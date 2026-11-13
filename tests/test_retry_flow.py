from __future__ import annotations

from datetime import timedelta

from app.core.clock import to_iso, utcnow
from tests.helpers import application_payload, audit_events, submit


def test_rule_triggered_retry_can_be_resumed_with_new_dependency_output(client):
    run = submit(
        client,
        "application_approval",
        application_payload(),
        key="rule-retry",
        simulation={"responses": {"credit_bureau": {"verified": False}}},
    ).json()
    assert run["status"] == "retry_scheduled"
    assert run["result"]["decided_by"] == "bureau_verified"

    resumed = client.post(
        f"/runs/{run['id']}/resume",
        json={
            "simulation": {
                "responses": {"credit_bureau": {"verified": True}},
            }
        },
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "approved"


def test_workflow_retry_budget_exhausts_into_failed(client):
    run = submit(
        client,
        "application_approval",
        application_payload(),
        key="budget",
        simulation={"faults": {"credit_bureau": "error"}},
    ).json()
    assert run["status"] == "retry_scheduled"

    for expected_retry_count in (2, 3):
        response = client.post(f"/runs/{run['id']}/resume")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "retry_scheduled"
        assert body["retry_count"] == expected_retry_count

    final = client.post(f"/runs/{run['id']}/resume")
    assert final.status_code == 200
    assert final.json()["status"] == "failed"
    assert final.json()["outcome"] == "failed"
    assert "retry budget" in final.json()["result"]["reason"]
    decided = [
        event
        for event in audit_events(client, run["id"])
        if event["event_type"] == "outcome_decided"
    ]
    assert decided[-1]["payload"]["outcome"] == "failed"


def test_scheduler_resumes_due_retry_scheduled_runs(app, client):
    run = submit(
        client,
        "application_approval",
        application_payload(),
        key="scheduler",
        simulation={"faults": {"credit_bureau": "error"}},
    ).json()
    assert run["status"] == "retry_scheduled"

    scheduler = app.state.scheduler
    scheduler._clock = lambda: to_iso(utcnow() + timedelta(hours=1))
    scheduler.tick()

    refreshed = client.get(f"/runs/{run['id']}").json()
    assert refreshed["retry_count"] == 2
    assert refreshed["status"] == "retry_scheduled"


def test_resume_is_rejected_for_terminal_runs(client):
    run = submit(client, "application_approval", application_payload(), key="term").json()
    assert run["status"] == "approved"
    response = client.post(f"/runs/{run['id']}/resume")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state_transition"
