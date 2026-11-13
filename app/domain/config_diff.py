from __future__ import annotations

import json
from typing import Any

from app.domain.models import WorkflowConfig


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _changed_keys(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    keys = set(old) | set(new)
    return sorted(key for key in keys if old.get(key) != new.get(key))


def _diff_stage(old_stage: dict[str, Any], new_stage: dict[str, Any]) -> dict[str, Any]:
    old_rules = {rule["id"]: rule for rule in old_stage.get("rules", [])}
    new_rules = {rule["id"]: rule for rule in new_stage.get("rules", [])}

    added_rules = sorted(set(new_rules) - set(old_rules))
    removed_rules = sorted(set(old_rules) - set(new_rules))
    changed_rules = []
    for rule_id in sorted(set(old_rules) & set(new_rules)):
        changes = _changed_keys(old_rules[rule_id], new_rules[rule_id])
        if changes:
            changed_rules.append({"rule_id": rule_id, "fields": changes})

    structural = _changed_keys(old_stage, new_stage)
    return {
        "changed": bool(structural),
        "changed_fields": structural,
        "added_rules": added_rules,
        "removed_rules": removed_rules,
        "changed_rules": changed_rules,
    }


def diff_configs(
    old: dict[str, WorkflowConfig], new: dict[str, WorkflowConfig]
) -> dict[str, Any]:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed: list[dict[str, Any]] = []

    for name in sorted(set(old) & set(new)):
        old_workflow = old[name].snapshot()
        new_workflow = new[name].snapshot()
        if _canonical(old_workflow) == _canonical(new_workflow):
            continue

        old_stages = {stage["name"]: stage for stage in old_workflow.get("stages", [])}
        new_stages = {stage["name"]: stage for stage in new_workflow.get("stages", [])}
        stage_diff = {
            stage_name: _diff_stage(old_stages[stage_name], new_stages[stage_name])
            for stage_name in sorted(set(old_stages) & set(new_stages))
        }
        stage_diff = {
            stage_name: detail
            for stage_name, detail in stage_diff.items()
            if detail["changed"]
        }

        sections = [
            key
            for key in ("description", "input_schema", "retry", "metadata")
            if _canonical(old_workflow.get(key)) != _canonical(new_workflow.get(key))
        ]
        if old_workflow.get("entry_stage") != new_workflow.get("entry_stage"):
            sections.append("entry_stage")

        changed.append(
            {
                "name": name,
                "version": {
                    "from": old[name].version,
                    "to": new[name].version,
                },
                "changed_sections": sections,
                "added_stages": sorted(set(new_stages) - set(old_stages)),
                "removed_stages": sorted(set(old_stages) - set(new_stages)),
                "stages": stage_diff,
                "rule_changes": sum(
                    len(detail["added_rules"])
                    + len(detail["removed_rules"])
                    + len(detail["changed_rules"])
                    for detail in stage_diff.values()
                ),
            }
        )

    return {
        "added_workflows": added,
        "removed_workflows": removed,
        "changed_workflows": changed,
        "has_changes": bool(added or removed or changed),
    }
