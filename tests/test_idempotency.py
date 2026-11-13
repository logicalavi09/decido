from __future__ import annotations

from tests.helpers import application_payload, submit


def test_repeated_request_with_same_key_replays_original_decision(client):
    payload = application_payload()
    first = submit(client, "application_approval", payload, key="idem-1")
    second = submit(
        client, "application_approval", payload, key="idem-1", expected_status=200
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert second.json()["id"] == first.json()["id"]

    listing = client.get("/runs").json()
    assert listing["count"] == 1
    first_audit = client.get(f"/runs/{first.json()['id']}/audit").json()
    second_audit = client.get(f"/runs/{first.json()['id']}/audit").json()
    assert first_audit == second_audit
    assert first_audit["events"]


def test_same_key_with_different_payload_is_a_conflict(client):
    submit(client, "application_approval", application_payload(), key="idem-2")
    changed = application_payload(requested_amount=16000)
    response = client.post(
        "/runs",
        json={"workflow": "application_approval", "input": changed},
        headers={"Idempotency-Key": "idem-2"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"
    assert response.json()["error"]["details"]["idempotency_key"] == "idem-2"
    assert client.get("/runs").json()["count"] == 1


def test_automatic_idempotency_when_header_is_absent(client):
    payload = application_payload()
    first = submit(client, "application_approval", payload)
    second = submit(client, "application_approval", payload, expected_status=200)
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["idempotency_key"].startswith("auto:")
    assert client.get("/runs").json()["count"] == 1


def test_distinct_keys_create_distinct_runs(client):
    payload = application_payload()
    first = submit(client, "application_approval", payload, key="idem-a")
    second = submit(client, "application_approval", payload, key="idem-b")
    assert first.json()["id"] != second.json()["id"]
    assert client.get("/runs").json()["count"] == 2
