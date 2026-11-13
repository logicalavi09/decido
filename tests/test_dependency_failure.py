from __future__ import annotations

from tests.helpers import (
    application_payload,
    audit_events,
    events_of_type,
    submit,
)


def test_exhausted_dependency_moves_run_to_retry_scheduled(client):
    payload = application_payload()
    run = submit(
        client,
        "application_approval",
        payload,
        key="dep-fail-1",
        simulation={"faults": {"credit_bureau": "error"}},
    ).json()

    assert run["status"] == "retry_scheduled"
    assert run["outcome"] == "retry_scheduled"
    assert run["retry_count"] == 1
    assert run["next_retry_at"] is not None
    assert "credit_bureau" in run["last_error"]

    events = audit_events(client, run["id"])
    attempts = events_of_type(events, "dependency_attempt")
    assert [attempt["payload"]["attempt"] for attempt in attempts] == [1, 2, 3]
    assert all(attempt["payload"]["ok"] is False for attempt in attempts)
    assert events_of_type(events, "retry_scheduled")

    resumed = client.post(
        f"/runs/{run['id']}/resume",
        json={"simulation": {"faults": {}}},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "approved"

    events = audit_events(client, run["id"])
    attempts = events_of_type(events, "dependency_attempt")
    assert len(attempts) == 4
    assert attempts[-1]["payload"]["ok"] is True
    assert events_of_type(events, "run_resumed")


def test_timeout_is_treated_as_a_dependency_failure(client):
    run = submit(
        client,
        "application_approval",
        application_payload(),
        key="dep-timeout",
        simulation={
            "latency_ms": {"credit_bureau": 5000},
        },
    ).json()
    assert run["status"] == "retry_scheduled"
    events = audit_events(client, run["id"])
    attempts = events_of_type(events, "dependency_attempt")
    assert all(
        attempt["payload"]["error_code"] == "dependency_timeout"
        for attempt in attempts
    )


def test_dependency_failure_policy_can_route_to_manual_review(client):
    claim = {
        "policy_id": "POL-654321",
        "claim_amount": 5000,
        "claimant": {"name": "Grace Hopper", "email": "grace@example.com"},
        "documents": [{"type": "invoice", "reference": "DOC-2"}],
        "incident_date": "2026-01-10",
    }
    run = submit(
        client,
        "claim_processing",
        claim,
        key="claim-dep-fail",
        simulation={"faults": {"document_verifier": "unavailable"}},
    ).json()
    assert run["status"] == "manual_review"
    assert run["outcome"] == "manual_review"
    assert "document_verifier" in run["result"]["reason"]

    event_types = [event["event_type"] for event in audit_events(client, run["id"])]
    assert "outcome_decided" in event_types
    assert "retry_scheduled" not in event_types


def test_partial_retry_success_stays_within_the_same_request(client):
    run = submit(
        client,
        "application_approval",
        application_payload(),
        key="dep-transient",
        simulation={"faults": {"credit_bureau": "fail_until_attempt:2"}},
    ).json()
    assert run["status"] == "approved"
    attempts = events_of_type(audit_events(client, run["id"]), "dependency_attempt")
    assert [attempt["payload"]["attempt"] for attempt in attempts] == [1, 2, 3]
    assert [attempt["payload"]["ok"] for attempt in attempts] == [False, False, True]
