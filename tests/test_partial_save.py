from __future__ import annotations

import pytest

from app.core.errors import ConcurrentModificationError
from app.infra.repository import RunRepository
from tests.helpers import application_payload, audit_events


def test_commit_failure_leaves_no_partial_stage_and_is_recoverable(client):
    response = client.post(
        "/runs",
        json={
            "workflow": "application_approval",
            "input": application_payload(),
            "simulation": {"database_fault": "commit_failure"},
        },
        headers={"Idempotency-Key": "db-fault"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "partial_save_failure"
    assert response.json()["error"]["details"]["recoverable"] is True
    assert response.json()["error"]["details"]["run_id"]

    runs = client.get("/runs").json()["items"]
    run = next(item for item in runs if item["idempotency_key"] == "db-fault")
    assert run["status"] == "running"
    assert run["current_stage"] == "intake"

    events = audit_events(client, run["id"])
    assert [event["event_type"] for event in events] == [
        "request_received",
        "run_started",
    ]

    resumed = client.post(
        f"/runs/{run['id']}/resume", json={"simulation": {}}
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "approved"
    event_types = [event["event_type"] for event in audit_events(client, run["id"])]
    assert "outcome_decided" in event_types
    assert event_types.count("request_received") == 1


def test_stale_version_write_is_rejected(app):
    repository: RunRepository = app.state.repository
    submission = app.state.service.submit(
        "application_approval", application_payload(), idempotency_key="stale"
    )
    stored = repository.get_run(submission.run.id)

    with pytest.raises(ConcurrentModificationError):
        repository.commit_stage(
            stored.id,
            stored.version - 1,
            {"updated_at": stored.updated_at},
            [],
        )
