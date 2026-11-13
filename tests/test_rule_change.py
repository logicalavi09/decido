from __future__ import annotations

from pathlib import Path

from tests.helpers import application_payload, submit


def _replace_in_file(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    assert old in content
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def test_rule_change_is_picked_up_without_code_change(client, workflows_dir):
    config_path = workflows_dir / "application_approval.yaml"
    payload = application_payload(
        applicant=application_payload()["applicant"] | {"credit_score": 680}
    )

    before = submit(client, "application_approval", payload, key="before-change").json()
    assert before["status"] == "approved"
    assert before["workflow_version"] == "1.0.0"

    _replace_in_file(config_path, 'version: "1.0.0"', 'version: "1.0.1"')
    _replace_in_file(config_path, "value: 650", "value: 700")
    _replace_in_file(
        config_path,
        "reason: Credit score is below the 650 minimum",
        "reason: Credit score is below the 700 minimum",
    )

    reload_response = client.post("/workflows/reload")
    assert reload_response.status_code == 200
    versions = {
        item["name"]: item["version"]
        for item in reload_response.json()["workflows"]
    }
    assert versions["application_approval"] == "1.0.1"

    after = submit(client, "application_approval", payload, key="after-change").json()
    assert after["status"] == "rejected"
    assert after["workflow_version"] == "1.0.1"
    assert "700" in after["result"]["reason"]
    assert after["result"]["decided_by"] == "minimum_credit_score"

    old = client.get(f"/runs/{before['id']}").json()
    assert old["status"] == "approved"
    assert old["workflow_version"] == "1.0.0"
    old_explanation = client.get(f"/runs/{before['id']}/explanation").json()
    assert old_explanation["workflow_version"] == "1.0.0"


def test_invalid_reload_keeps_previous_registry_atomic(client, workflows_dir):
    (workflows_dir / "broken.yaml").write_text(
        "name: broken\nversion: '1'\nstages: []\n", encoding="utf-8"
    )
    response = client.post("/workflows/reload")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "invalid_configuration"
    assert any(
        detail["file"] == "broken.yaml"
        for detail in response.json()["error"]["details"]
    )

    health = client.get("/health")
    assert health.status_code == 200
    run = submit(
        client, "application_approval", application_payload(), key="still-working"
    ).json()
    assert run["status"] == "approved"


def test_workflow_configuration_is_exposed_read_only(client):
    listing = client.get("/workflows").json()
    assert listing["count"] == 5
    names = {item["name"] for item in listing["workflows"]}
    assert "application_approval" in names
    assert "vendor_approval" in names

    detail = client.get("/workflows/application_approval").json()
    assert detail["workflow"]["version"] == "1.0.0"
    assert detail["workflow"]["entry_stage"] == "intake"
    assert len(detail["snapshot_hash"]) == 64
    stage_names = [stage["name"] for stage in detail["workflow"]["stages"]]
    assert stage_names == ["intake", "credit_check", "affordability", "auto_approval"]
