from __future__ import annotations

import copy
from pathlib import Path

from app.domain.config_lint import lint_workflow
from app.domain.models import WorkflowConfig
from tests.helpers import application_payload, submit


def test_audit_chain_verifies_and_detects_tampering(client, app):
    run = submit(
        client, "application_approval", application_payload(), key="chain-ok"
    ).json()

    audit = client.get(f"/runs/{run['id']}/audit").json()
    assert audit["integrity"]["valid"] is True
    assert audit["integrity"]["event_count"] == len(audit["events"])
    assert audit["events"][0]["prev_hash"] == "0" * 64
    assert all(event["event_hash"] for event in audit["events"])

    verified = client.get(f"/runs/{run['id']}/audit/verify").json()
    assert verified["valid"] is True
    assert verified["head_hash"] == audit["events"][-1]["event_hash"]

    tampered_seq = audit["events"][2]["seq"]
    with app.state.database.transaction() as connection:
        connection.execute(
            "UPDATE run_events SET payload_json = ? WHERE run_id = ? AND seq = ?",
            ('{"description": "tampered"}', run["id"], tampered_seq),
        )

    broken = client.get(f"/runs/{run['id']}/audit/verify").json()
    assert broken["valid"] is False
    assert broken["broken_at_seq"] == tampered_seq
    assert "does not match" in broken["reason"]
    assert client.get(f"/runs/{run['id']}/audit").json()["integrity"][
        "valid"
    ] is False


def test_audit_anchor_detects_tail_truncation(client, app):
    run = submit(
        client, "application_approval", application_payload(), key="chain-tail"
    ).json()
    verified = client.get(f"/runs/{run['id']}/audit/verify").json()
    assert verified["anchor"]["event_count"] == verified["event_count"]
    assert verified["anchor"]["head_hash"] == verified["head_hash"]

    with app.state.database.transaction() as connection:
        connection.execute(
            "DELETE FROM run_events WHERE run_id = ? AND seq = "
            "(SELECT MAX(seq) FROM run_events WHERE run_id = ?)",
            (run["id"], run["id"]),
        )

    truncated = client.get(f"/runs/{run['id']}/audit/verify").json()
    assert truncated["valid"] is False
    assert truncated["broken_at_seq"] == truncated["event_count"] + 1
    assert "truncated" in truncated["reason"]


def test_explanation_includes_actionable_counterfactuals(client):
    rejected = submit(
        client,
        "application_approval",
        application_payload(
            applicant=application_payload()["applicant"] | {"credit_score": 640}
        ),
        key="cf-reject",
    ).json()
    explanation = client.get(f"/runs/{rejected['id']}/explanation").json()
    counterfactual = explanation["counterfactuals"][0]
    assert counterfactual["field"] == "applicant.credit_score"
    assert counterfactual["operator"] == "gte"
    assert counterfactual["expected"] == 650
    assert counterfactual["gap"] == 10
    assert counterfactual["required"] == ">= 650"
    assert "increase to at least 650" in counterfactual["action"]

    manual = submit(
        client,
        "application_approval",
        application_payload(requested_amount=30000),
        key="cf-guard",
    ).json()
    guard_cf = client.get(f"/runs/{manual['id']}/explanation").json()[
        "counterfactuals"
    ][0]
    assert guard_cf["mode"] == "guard"
    assert guard_cf["field"] == "requested_amount"
    assert guard_cf["gap"] == 5000
    assert "decrease to at most 25000" in guard_cf["action"]
    assert "avoid triggering" in guard_cf["action"]


def test_lint_endpoint_reports_clean_shipped_config(client):
    response = client.get("/workflows/application_approval/lint")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {"error": 0, "warning": 0, "info": 0}
    assert body["findings"] == []

    detail = client.get("/workflows/application_approval").json()
    assert detail["lint"]["summary"]["error"] == 0


def test_lint_flags_duplicate_rules_unknown_fields_and_graph_issues():
    workflow = WorkflowConfig.model_validate(
        {
            "name": "lint_demo",
            "version": "1.0.0",
            "entry_stage": "b",
            "input_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "minimum": 0, "maximum": 100}
                },
            },
            "stages": [
                {"name": "a", "rules": []},
                {
                    "name": "b",
                    "rules": [
                        {
                            "id": "duplicate",
                            "condition": {"field": "score", "op": "gte", "value": 0},
                            "effect": "reject",
                        },
                        {
                            "id": "duplicate",
                            "severity": "high",
                            "condition": {"field": "ghost", "op": "eq", "value": 1},
                            "effect": "continue",
                        },
                    ],
                    "on_pass": {"goto": "b"},
                },
            ],
        }
    )
    codes = {finding["code"] for finding in lint_workflow(workflow)}
    assert {
        "duplicate_rule_id",
        "rule_always_passes",
        "unknown_input_field",
        "high_severity_continue",
        "stage_cycle",
        "unreachable_stage",
    } <= codes


def test_dry_run_rejects_invalid_candidate_without_activating(client, app):
    Path(app.state.registry.directory, "broken.yaml").write_text(
        "name: broken\nversion: '1'\nstages: []\n", encoding="utf-8"
    )
    response = client.post("/workflows/reload", params={"dry_run": True})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "invalid_configuration"
    assert response.json()["error"]["details"][0]["file"] == "broken.yaml"

    assert client.get("/workflows").json()["count"] == 5
    run = submit(
        client, "application_approval", application_payload(), key="after-dry-run"
    ).json()
    assert run["status"] == "approved"


def test_reload_dry_run_diff_and_etag_guard(client, app):
    active = client.get("/workflows").json()
    active_etag = active["config_etag"]

    conflict = client.post(
        "/workflows/reload",
        params={"dry_run": True},
        headers={"If-Match": "not-the-current-etag"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"

    no_change = client.post(
        "/workflows/reload",
        params={"dry_run": True},
        headers={"If-Match": active_etag},
    ).json()
    assert no_change["dry_run"] is True
    assert no_change["has_changes"] is False
    assert no_change["candidate_etag"] == active_etag
    assert no_change["diff"]["has_changes"] is False
    assert no_change["lint"]["totals"]["error"] == 0

    workflow_file = (
        Path(app.state.registry.directory) / "application_approval.yaml"
    )
    workflow_file.write_text(
        workflow_file.read_text(encoding="utf-8").replace(
            'version: "1.0.0"', 'version: "1.1.0"', 1
        ),
        encoding="utf-8",
    )

    preview = client.post(
        "/workflows/reload",
        params={"dry_run": True},
        headers={"If-Match": active_etag},
    ).json()
    assert preview["has_changes"] is True
    assert preview["candidate_etag"] != active_etag
    changed = preview["diff"]["changed_workflows"]
    assert [item["name"] for item in changed] == ["application_approval"]
    assert changed[0]["version"] == {"from": "1.0.0", "to": "1.1.0"}
    assert client.get("/workflows/application_approval").json()["workflow"][
        "version"
    ] == "1.0.0"

    reloaded = client.post("/workflows/reload").json()
    assert reloaded["previous_etag"] == active_etag
    assert reloaded["config_etag"] == preview["candidate_etag"]
    assert client.get("/workflows/application_approval").json()["workflow"][
        "version"
    ] == "1.1.0"


def test_lint_flags_impossible_thresholds_and_numeric_index_paths():
    workflow = WorkflowConfig.model_validate(
        {
            "name": "threshold_demo",
            "version": "1.0.0",
            "input_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "documents": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"type": {"type": "string"}},
                        },
                    },
                },
            },
            "stages": [
                {
                    "name": "check",
                    "rules": [
                        {
                            "id": "impossible",
                            "condition": {"field": "score", "op": "gte", "value": 101},
                            "effect": "reject",
                        },
                        {
                            "id": "redundant",
                            "condition": {"field": "score", "op": "lte", "value": 100},
                            "effect": "reject",
                        },
                        {
                            "id": "indexed",
                            "condition": {
                                "field": "documents.0.type",
                                "op": "eq",
                                "value": "$input.documents.0.type",
                            },
                            "effect": "continue",
                        },
                    ],
                }
            ],
        }
    )
    findings = lint_workflow(workflow)
    codes = {finding["code"] for finding in findings}
    assert {"rule_always_fails", "rule_always_passes"} <= codes
    assert "unknown_input_field" not in codes


def test_shadow_impact_compares_candidate_against_supplied_inputs(client):
    amounts = [15_000, 15_000, 15_000, 30_000, 30_000]
    payloads = [
        application_payload(requested_amount=amount) for amount in amounts
    ]
    for index, payload in enumerate(payloads):
        submit(client, "application_approval", payload, key=f"impact-{index}")

    candidate = copy.deepcopy(
        client.get("/workflows/application_approval").json()["workflow"]
    )
    affordability = next(
        stage for stage in candidate["stages"] if stage["name"] == "affordability"
    )
    high_value = next(
        rule
        for rule in affordability["rules"]
        if rule["id"] == "high_value_review"
    )
    high_value["condition"]["value"] = 50_000

    impact = client.post(
        "/workflows/application_approval/impact",
        json={"candidate": candidate, "inputs": payloads, "sample_size": 5},
    ).json()
    assert impact["sample_source"] == "request"
    assert impact["sample_size"] == 5
    assert impact["changed"] == 2
    assert impact["transition_matrix"]["approved"]["approved"] == 3
    assert impact["transition_matrix"]["manual_review"]["approved"] == 2
    assert impact["outcomes"]["active"] == {"approved": 3, "manual_review": 2}
    assert impact["outcomes"]["candidate"] == {"approved": 5}
    assert impact["schema_failures"] == {"active": 0, "candidate": 0}
    assert impact["candidate_lint"]["summary"]["error"] == 0
    assert len(impact["changed_examples"]) == 2
    assert "input" not in impact["changed_examples"][0]

    from_history = client.post(
        "/workflows/application_approval/impact",
        json={"candidate": candidate, "sample_size": 3},
    ).json()
    assert from_history["sample_source"] == "history"
    assert from_history["sample_size"] == 3


def test_impact_rejects_invalid_or_renamed_candidates(client):
    payload = application_payload()
    submit(client, "application_approval", payload, key="impact-invalid")

    candidate = client.get("/workflows/application_approval").json()["workflow"]
    candidate["stages"] = []
    invalid = client.post(
        "/workflows/application_approval/impact", json={"candidate": candidate}
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_input"

    renamed = client.get("/workflows/application_approval").json()["workflow"]
    renamed["name"] = "something_else"
    mismatch = client.post(
        "/workflows/application_approval/impact", json={"candidate": renamed}
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["details"]["received"] == "something_else"
