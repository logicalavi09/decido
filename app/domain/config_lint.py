from __future__ import annotations

from typing import Any

from app.domain.models import (
    Condition,
    RuleConfig,
    RuleEffect,
    StageConfig,
    WorkflowConfig,
)

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _finding(
    severity: str,
    code: str,
    message: str,
    *,
    stage: str | None = None,
    rule_id: str | None = None,
    field: str | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "stage": stage,
        "rule_id": rule_id,
        "field": field,
    }


def _clean_path(path: str) -> str:
    clean = path
    for namespace in ("input.", "$input."):
        if clean.startswith(namespace):
            return clean[len(namespace) :]
    return clean


def _lookup_schema(
    schema: dict[str, Any], path: str
) -> dict[str, Any] | None:
    """Resolve a field path, including array indices and additional properties."""

    current: Any = schema
    for part in _clean_path(path).split("."):
        if not isinstance(current, dict):
            return None
        if part.isdigit():
            current = current.get("items")
            continue
        properties = current.get("properties")
        if isinstance(properties, dict) and part in properties:
            current = properties[part]
            continue
        additional = current.get("additionalProperties")
        if isinstance(additional, dict):
            current = additional
            continue
        if additional is True:
            current = {}
            continue
        return None
    return current if isinstance(current, dict) else None


def _iter_leaves(condition: Condition):
    if condition.all is not None:
        for child in condition.all:
            yield from _iter_leaves(child)
        return
    if condition.any is not None:
        for child in condition.any:
            yield from _iter_leaves(child)
        return
    if condition.not_ is not None:
        yield from _iter_leaves(condition.not_)
        return
    if condition.field is not None and condition.op is not None:
        yield condition


def _iter_template_paths(value: Any):
    if isinstance(value, str) and value.startswith("$"):
        yield value[1:].lstrip(".")
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_template_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_template_paths(child)


def _threshold_finding(
    workflow_stage: StageConfig,
    rule: RuleConfig,
    leaf: Condition,
    schema_node: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if schema_node is None or not isinstance(leaf.value, (int, float)):
        return None
    if isinstance(leaf.value, bool):
        return None
    value = leaf.value
    minimum = schema_node.get("minimum")
    maximum = schema_node.get("maximum")
    op = leaf.op.value
    always_passes = False
    always_fails = False

    if isinstance(minimum, (int, float)):
        if op == "gte" and value <= minimum:
            always_passes = True
        if op == "gt" and value < minimum:
            always_passes = True
        if op == "lte" and value < minimum:
            always_fails = True
        if op == "lt" and value <= minimum:
            always_fails = True
    if isinstance(maximum, (int, float)):
        if op == "lte" and value >= maximum:
            always_passes = True
        if op == "lt" and value > maximum:
            always_passes = True
        if op == "gte" and value > maximum:
            always_fails = True
        if op == "gt" and value >= maximum:
            always_fails = True

    if always_passes:
        return _finding(
            "info",
            "rule_always_passes",
            (
                f"Rule '{rule.id}' is always satisfied for the declared schema "
                f"bounds ({leaf.field} {op} {value}); it may be redundant."
            ),
            stage=workflow_stage.name,
            rule_id=rule.id,
            field=leaf.field,
        )
    if always_fails:
        return _finding(
            "warning",
            "rule_always_fails",
            (
                f"Rule '{rule.id}' can never be satisfied for the declared schema "
                f"bounds ({leaf.field} {op} {value}); it will always decide."
            ),
            stage=workflow_stage.name,
            rule_id=rule.id,
            field=leaf.field,
        )
    return None


def lint_workflow(workflow: WorkflowConfig) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    schema_root = workflow.input_schema or {}
    schema_constrained = bool(schema_root.get("properties"))

    # Duplicate rule identifiers make audit traces ambiguous.
    seen_rule_ids: dict[str, str] = {}
    for stage in workflow.stages:
        for rule in stage.rules:
            if rule.id in seen_rule_ids:
                findings.append(
                    _finding(
                        "error",
                        "duplicate_rule_id",
                        (
                            f"Rule id '{rule.id}' is used in stages "
                            f"'{seen_rule_ids[rule.id]}' and '{stage.name}'."
                        ),
                        stage=stage.name,
                        rule_id=rule.id,
                    )
                )
            else:
                seen_rule_ids[rule.id] = stage.name

    # Stage graph: reachability and cycles.
    edges: dict[str, set[str]] = {stage.name: set() for stage in workflow.stages}
    for index, stage in enumerate(workflow.stages):
        if index + 1 < len(workflow.stages):
            edges[stage.name].add(workflow.stages[index + 1].name)
        for transition in [stage.on_pass, stage.on_fail, *stage.on.values()]:
            if transition and transition.goto:
                edges[stage.name].add(transition.goto)
        for rule in stage.rules:
            if rule.goto:
                edges[stage.name].add(rule.goto)

    reachable: set[str] = set()
    stack = [workflow.entry_stage] if workflow.entry_stage else []
    while stack:
        current = stack.pop()
        if current in reachable or current not in edges:
            continue
        reachable.add(current)
        stack.extend(edges[current] - reachable)
    for stage in workflow.stages:
        if stage.name not in reachable:
            findings.append(
                _finding(
                    "warning",
                    "unreachable_stage",
                    f"Stage '{stage.name}' is not reachable from the entry stage.",
                    stage=stage.name,
                )
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        visiting.add(node)
        for target in edges.get(node, set()):
            if target in visiting:
                findings.append(
                    _finding(
                        "warning",
                        "stage_cycle",
                        f"Stage transition cycle detected: {node} -> {target}.",
                        stage=node,
                    )
                )
            elif target not in visited:
                visit(target)
        visiting.discard(node)
        visited.add(node)

    if workflow.entry_stage:
        visit(workflow.entry_stage)

    available_connectors: set[str] = set()
    for stage in workflow.stages:
        if stage.dependency is not None:
            available_connectors.add(stage.dependency.connector)

        for rule in stage.rules:
            if rule.severity in {"high", "critical"} and rule.effect == RuleEffect.CONTINUE:
                findings.append(
                    _finding(
                        "warning",
                        "high_severity_continue",
                        (
                            f"Rule '{rule.id}' is {rule.severity} severity but uses "
                            "effect 'continue'; it can never block a decision."
                        ),
                        stage=stage.name,
                        rule_id=rule.id,
                    )
                )

            for leaf in _iter_leaves(rule.condition):
                assert leaf.field is not None
                path = leaf.field
                if path.startswith("dependencies."):
                    connector = path.split(".", 2)[1]
                    if connector not in available_connectors:
                        findings.append(
                            _finding(
                                "warning",
                                "unknown_dependency_reference",
                                (
                                    f"Rule '{rule.id}' references dependency "
                                    f"'{connector}', which is not configured in this "
                                    "or an earlier stage."
                                ),
                                stage=stage.name,
                                rule_id=rule.id,
                                field=path,
                            )
                        )
                    continue

                if schema_constrained and _lookup_schema(schema_root, path) is None:
                    findings.append(
                        _finding(
                            "warning",
                            "unknown_input_field",
                            (
                                f"Rule '{rule.id}' references '{path}', which is not "
                                "declared in input_schema.properties."
                            ),
                            stage=stage.name,
                            rule_id=rule.id,
                            field=path,
                        )
                    )
                threshold = _threshold_finding(
                    stage, rule, leaf, _lookup_schema(schema_root, path)
                )
                if threshold:
                    findings.append(threshold)

            for template_path in _iter_template_paths(rule.condition.value):
                if template_path.startswith("dependencies."):
                    connector = template_path.split(".", 2)[1]
                    if connector not in available_connectors:
                        findings.append(
                            _finding(
                                "warning",
                                "unknown_dependency_reference",
                                (
                                    f"Rule '{rule.id}' template references dependency "
                                    f"'{connector}', which is not available."
                                ),
                                stage=stage.name,
                                rule_id=rule.id,
                                field=template_path,
                            )
                        )
                elif (
                    schema_constrained
                    and _lookup_schema(schema_root, template_path) is None
                    and not template_path.startswith("dependencies.")
                ):
                    findings.append(
                        _finding(
                            "warning",
                            "unknown_input_field",
                            (
                                f"Rule '{rule.id}' template references "
                                f"'${template_path}', which is not declared in the schema."
                            ),
                            stage=stage.name,
                            rule_id=rule.id,
                            field=template_path,
                        )
                    )

        if stage.dependency is not None:
            for template_path in _iter_template_paths(stage.dependency.result):
                if template_path.startswith("dependencies."):
                    continue
                if schema_constrained and _lookup_schema(
                    schema_root, template_path
                ) is None:
                    findings.append(
                        _finding(
                            "warning",
                            "unknown_input_field",
                            (
                                f"Dependency '{stage.dependency.connector}' template "
                                f"references '${template_path}', which is not declared "
                                "in the schema."
                            ),
                            stage=stage.name,
                            field=template_path,
                        )
                    )

    findings.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(item["severity"], 9),
            item["stage"] or "",
            item["rule_id"] or "",
        )
    )
    return findings


def lint_summary(findings: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        summary[finding["severity"]] = summary.get(finding["severity"], 0) + 1
    return summary


def lint_workflows(workflows: dict[str, WorkflowConfig]) -> dict[str, Any]:
    per_workflow = {
        name: lint_workflow(workflow) for name, workflow in sorted(workflows.items())
    }
    totals = {"error": 0, "warning": 0, "info": 0}
    for findings in per_workflow.values():
        for severity, count in lint_summary(findings).items():
            totals[severity] += count
    return {"totals": totals, "workflows": per_workflow}
