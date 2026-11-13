from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"


def application_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "applicant": {
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "credit_score": 720,
            "monthly_income": 4000,
            "employment_status": "employed",
        },
        "requested_amount": 15000,
        "country": "US",
    }
    payload.update(copy.deepcopy(overrides))
    return payload


VALID_PAYLOADS: dict[str, dict[str, Any]] = {
    "application_approval": application_payload(),
    "claim_processing": {
        "policy_id": "POL-123456",
        "claim_amount": 5000,
        "claimant": {"name": "Grace Hopper", "email": "grace@example.com"},
        "documents": [{"type": "invoice", "reference": "DOC-1"}],
        "incident_date": "2026-01-10",
    },
    "employee_onboarding": {
        "candidate": {
            "name": "Alan Turing",
            "email": "alan@example.com",
            "role": "engineer",
            "consent_background_check": True,
        },
        "start_date": "2026-02-01",
        "documents": [{"type": "passport", "reference": "DOC-9"}],
        "equipment_requested": False,
    },
    "vendor_approval": {
        "vendor": {
            "name": "Acme Supplies",
            "tax_id": "ABC12345",
            "country": "US",
            "risk_rating": "low",
        },
        "contract_value": 50000,
    },
    "document_verification": {
        "document": {
            "type": "passport",
            "number": "P1234567",
            "expires_on": "2030-01-01",
            "issuing_country": "US",
        },
        "owner": {"name": "Katherine Johnson", "email": "kj@example.com"},
        "file_ref": "s3://bucket/doc.pdf",
    },
}


@pytest.fixture
def workflows_dir(tmp_path: Path) -> Path:
    destination = tmp_path / "workflows"
    shutil.copytree(WORKFLOWS_DIR, destination)
    return destination


@pytest.fixture
def app(tmp_path: Path, workflows_dir: Path):
    return create_app(
        Settings(
            db_path=str(tmp_path / "test.db"),
            workflows_dir=str(workflows_dir),
            scheduler_enabled=False,
        )
    )


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def submit(
    client: TestClient,
    workflow: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
    simulation: dict[str, Any] | None = None,
    expected_status: int = 201,
):
    headers = {"Idempotency-Key": key} if key else {}
    body: dict[str, Any] = {"workflow": workflow, "input": payload}
    if simulation is not None:
        body["simulation"] = simulation
    response = client.post("/runs", json=body, headers=headers)
    assert response.status_code == expected_status, response.text
    return response


def audit_events(client: TestClient, run_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/runs/{run_id}/audit")
    assert response.status_code == 200
    return response.json()["events"]


def events_of_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event["event_type"] == event_type]


def assert_json_roundtrip(value: Any) -> None:
    json.dumps(value)
