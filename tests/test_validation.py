from __future__ import annotations

from tests.helpers import application_payload


def test_missing_required_field_is_rejected_with_structured_errors(client):
    payload = application_payload()
    del payload["applicant"]["credit_score"]

    response = client.post(
        "/runs", json={"workflow": "application_approval", "input": payload}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_input"
    details = body["error"]["details"]
    assert any(
        detail["path"] == "$.applicant.credit_score"
        and detail["code"] == "required"
        for detail in details
    )


def test_wrong_type_and_pattern_are_reported_together(client):
    payload = application_payload(
        applicant=application_payload()["applicant"]
        | {"email": "not-an-email", "credit_score": "high"}
    )
    response = client.post(
        "/runs", json={"workflow": "application_approval", "input": payload}
    )
    assert response.status_code == 422
    codes = {detail["code"] for detail in response.json()["error"]["details"]}
    assert {"type_mismatch", "pattern_mismatch"} <= codes


def test_unknown_workflow_returns_404(client):
    response = client.post(
        "/runs", json={"workflow": "does_not_exist", "input": {}}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workflow_not_found"


def test_malformed_envelope_is_rejected_by_fastapi(client):
    response = client.post("/runs", json={"workflow": "application_approval"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_invalid_requests_do_not_persist_runs(client):
    response = client.post(
        "/runs",
        json={"workflow": "application_approval", "input": {"country": "US"}},
    )
    assert response.status_code == 422
    listing = client.get("/runs").json()
    assert listing["count"] == 0


def test_unknown_status_filter_is_rejected(client):
    response = client.get("/runs", params={"status": "banana"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
