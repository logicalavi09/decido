from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.models import (
    Condition,
    Operator,
    RuleConfig,
    RuleEffect,
    RuleMode,
    StageConfig,
)


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


MISSING = _Missing()


@dataclass
class FieldCapture:
    path: str
    present: bool
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "present": self.present, "value": self.value}


@dataclass
class ConditionResult:
    passed: bool
    captures: list[FieldCapture] = field(default_factory=list)
    error: str | None = None


@dataclass
class RuleTrace:
    rule_id: str
    description: str
    severity: str
    mode: str
    condition_result: bool
    triggered: bool
    effect: str
    reason: str
    field_values: dict[str, Any]
    condition: dict[str, Any]
    evaluation_error: str | None = None
    decided: bool = False
    counterfactual: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "severity": self.severity,
            "mode": self.mode,
            "condition_result": self.condition_result,
            "triggered": self.triggered,
            "effect": self.effect,
            "reason": self.reason,
            "field_values": self.field_values,
            "condition": self.condition,
            "evaluation_error": self.evaluation_error,
            "decided": self.decided,
            "counterfactual": self.counterfactual,
        }


@dataclass
class StageEvaluation:
    passed: bool
    traces: list[RuleTrace]
    failure_effect: RuleEffect | None = None
    decided_by: str | None = None
    reason: str = ""
    branch_goto: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failure_effect": self.failure_effect.value if self.failure_effect else None,
            "decided_by": self.decided_by,
            "reason": self.reason,
            "branch_goto": self.branch_goto,
            "rule_traces": [trace.to_dict() for trace in self.traces],
        }


def resolve_path(context: Any, path: str) -> Any:
    current = context
    for segment in path.split("."):
        if current is MISSING:
            return MISSING
        if isinstance(current, dict):
            if segment in current:
                current = current[segment]
            else:
                return MISSING
        elif isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return MISSING
        else:
            return MISSING
    return current


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare(op: Operator, actual: Any, expected: Any, present: bool) -> tuple[bool, str | None]:
    if op == Operator.EXISTS:
        return present and actual is not None, None
    if op == Operator.NOT_EXISTS:
        return (not present) or actual is None, None

    if not present:
        return False, f"field is missing (operator '{op.value}')"

    if op in (Operator.LEN_GTE, Operator.LEN_LTE, Operator.LEN_EQ):
        if not isinstance(actual, (str, list, dict)):
            return False, "len_* operators require a string, list or object"
        if not isinstance(expected, int) or isinstance(expected, bool):
            return False, "len_* operators require an integer value"
        if op == Operator.LEN_GTE:
            return len(actual) >= expected, None
        if op == Operator.LEN_LTE:
            return len(actual) <= expected, None
        return len(actual) == expected, None

    if op in (Operator.EQ, Operator.NEQ):
        equal = actual == expected
        return (equal if op == Operator.EQ else not equal), None

    if op in (Operator.GT, Operator.GTE, Operator.LT, Operator.LTE):
        if not _is_number(actual) or not _is_number(expected):
            return False, f"operator '{op.value}' requires numeric values"
        if op == Operator.GT:
            return actual > expected, None
        if op == Operator.GTE:
            return actual >= expected, None
        if op == Operator.LT:
            return actual < expected, None
        return actual <= expected, None

    if op in (Operator.IN, Operator.NOT_IN):
        if not isinstance(expected, (list, tuple, set, str, dict)):
            return False, "operator 'in' requires a list, string or object value"
        contained = actual in expected
        return (contained if op == Operator.IN else not contained), None

    if op in (Operator.CONTAINS, Operator.NOT_CONTAINS):
        if not isinstance(actual, (list, tuple, set, str, dict)):
            return False, "operator 'contains' requires a container field"
        contained = expected in actual
        return (contained if op == Operator.CONTAINS else not contained), None

    if op == Operator.MATCHES:
        if not isinstance(actual, str) or not isinstance(expected, str):
            return False, "operator 'matches' requires string values"
        try:
            return re.search(expected, actual) is not None, None
        except re.error as exc:
            return False, f"invalid regular expression: {exc}"

    if op == Operator.BETWEEN:
        if not isinstance(expected, (list, tuple)) or len(expected) != 2:
            return False, "operator 'between' requires [low, high]"
        if not _is_number(actual) or not all(_is_number(v) for v in expected):
            return False, "operator 'between' requires numeric values"
        return expected[0] <= actual <= expected[1], None

    return False, f"unsupported operator '{op.value}'"


def _resolve_expected(value: Any, context: dict[str, Any]) -> Any:
    """Allow rule values to reference the request or dependency output.

    Example: value: "$input.vendor.country" is compared against the resolved
    request field rather than the literal string.
    """

    if isinstance(value, str) and value.startswith("$"):
        path = value[1:]
        if path.startswith("."):
            path = path[1:]
        resolved = resolve_path(context, path)
        return None if resolved is MISSING else resolved
    if isinstance(value, list):
        return [_resolve_expected(item, context) for item in value]
    return value


def evaluate_condition(condition: Condition, context: dict[str, Any]) -> ConditionResult:
    captures: list[FieldCapture] = []
    error: str | None = None

    if condition.all is not None:
        results = [evaluate_condition(child, context) for child in condition.all]
        for result in results:
            captures.extend(result.captures)
            error = error or result.error
        return ConditionResult(all(result.passed for result in results), captures, error)

    if condition.any is not None:
        results = [evaluate_condition(child, context) for child in condition.any]
        for result in results:
            captures.extend(result.captures)
            error = error or result.error
        return ConditionResult(any(result.passed for result in results), captures, error)

    if condition.not_ is not None:
        result = evaluate_condition(condition.not_, context)
        return ConditionResult(not result.passed, result.captures, result.error)

    assert condition.field is not None and condition.op is not None
    actual = resolve_path(context, condition.field)
    present = actual is not MISSING
    captures.append(
        FieldCapture(path=condition.field, present=present, value=None if not present else actual)
    )
    expected = _resolve_expected(condition.value, context)
    passed, compare_error = _compare(condition.op, actual, expected, present)
    return ConditionResult(passed, captures, compare_error)


def _field_values(traces: list[FieldCapture]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for capture in traces:
        values[capture.path] = (
            capture.value if capture.present else {"_present": False}
        )
    return values


def _required_expression(op: Operator, expected: Any) -> str:
    if op == Operator.GTE:
        return f">= {expected}"
    if op == Operator.GT:
        return f"> {expected}"
    if op == Operator.LTE:
        return f"<= {expected}"
    if op == Operator.LT:
        return f"< {expected}"
    if op == Operator.EQ:
        return f"== {expected}"
    if op == Operator.NEQ:
        return f"!= {expected}"
    if op in (Operator.IN, Operator.NOT_IN):
        return f"{op.value} {expected}"
    if op == Operator.BETWEEN:
        return f"between {expected}"
    if op == Operator.MATCHES:
        return f"match /{expected}/"
    if op == Operator.EXISTS:
        return "be present"
    if op == Operator.NOT_EXISTS:
        return "be absent"
    if op == Operator.CONTAINS:
        return f"contain {expected}"
    return f"{op.value} {expected}"


def _numeric_counterfactual(
    op: Operator, actual: Any, expected: Any, is_guard: bool
) -> tuple[float | None, str]:
    if not _is_number(actual) or not _is_number(expected):
        return None, _required_expression(op, expected)

    if op in (Operator.GTE, Operator.GT):
        if not is_guard:
            gap = max(0, expected - actual)
            return gap, f"increase to at least {expected}" if op == Operator.GTE else f"increase above {expected}"
        gap = max(0, actual - expected)
        return gap, f"decrease below {expected}" if op == Operator.GTE else f"decrease to at most {expected}"

    if op in (Operator.LTE, Operator.LT):
        if not is_guard:
            gap = max(0, actual - expected)
            return gap, f"decrease to at most {expected}" if op == Operator.LTE else f"decrease below {expected}"
        gap = max(0, expected - actual)
        return gap, f"increase above {expected}" if op == Operator.LTE else f"increase to at least {expected}"

    if op == Operator.BETWEEN and isinstance(expected, (list, tuple)) and len(expected) == 2:
        low, high = expected
        if actual < low:
            return low - actual, f"increase into the range [{low}, {high}]"
        if actual > high:
            return actual - high, f"decrease into the range [{low}, {high}]"
        # Guard inside the range: no single movement is well-defined.
        return None, f"move outside the range [{low}, {high}]"

    return None, _required_expression(op, expected)


def build_counterfactual(
    rule: RuleConfig,
    condition_result: ConditionResult,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """Explain the smallest simple change that would flip a leaf condition.

    Returns None for composite conditions or non-actionable operators.
    """

    condition = rule.condition
    if condition.all is not None or condition.any is not None or condition.not_ is not None:
        return None
    if condition.field is None or condition.op is None:
        return None

    capture = condition_result.captures[-1] if condition_result.captures else None
    actual = capture.value if capture and capture.present else None
    expected = _resolve_expected(condition.value, context)
    is_guard = rule.mode == RuleMode.GUARD
    gap, action = _numeric_counterfactual(condition.op, actual, expected, is_guard)

    if condition.op == Operator.EXISTS:
        action = "provide this field" if not is_guard else "remove this field"
    elif condition.op == Operator.MATCHES:
        action = f"change the value to match /{expected}/"
    elif condition.op in (Operator.IN, Operator.NOT_IN):
        action = (
            f"use one of {expected}"
            if not is_guard
            else f"use a value outside {expected}"
        )

    target = "satisfy the rule" if not is_guard else "avoid triggering this rule"
    return {
        "mode": rule.mode.value,
        "field": condition.field,
        "operator": condition.op.value,
        "actual": actual,
        "expected": expected,
        "required": _required_expression(condition.op, expected),
        "gap": gap,
        "action": f"{action} to {target}",
    }


def evaluate_rules(stage: StageConfig, context: dict[str, Any]) -> StageEvaluation:
    """Evaluate every rule, record traces, and let the first deciding rule win."""

    traces: list[RuleTrace] = []
    decision_effect: RuleEffect | None = None
    decided_by: str | None = None
    reason = ""
    branch_goto: str | None = None

    for rule in stage.rules:
        result = evaluate_condition(rule.condition, context)
        triggered = (
            not result.passed if rule.mode == RuleMode.ASSERT else result.passed
        )
        deciding_now = (
            triggered
            and rule.effect != RuleEffect.CONTINUE
            and decision_effect is None
        )

        trace = RuleTrace(
            rule_id=rule.id,
            description=rule.description,
            severity=rule.severity,
            mode=rule.mode.value,
            condition_result=result.passed,
            triggered=triggered,
            effect=rule.effect.value,
            reason=rule.reason,
            field_values=_field_values(result.captures),
            condition=rule.condition.model_dump(mode="json", by_alias=True),
            evaluation_error=result.error,
            decided=deciding_now,
            counterfactual=build_counterfactual(rule, result, context)
            if triggered
            else None,
        )
        traces.append(trace)

        if deciding_now:
            decision_effect = rule.effect
            decided_by = rule.id
            reason = rule.reason
            if rule.effect == RuleEffect.BRANCH:
                branch_goto = rule.goto
                break

    return StageEvaluation(
        passed=decision_effect is None,
        traces=traces,
        failure_effect=decision_effect,
        decided_by=decided_by,
        reason=reason,
        branch_goto=branch_goto,
    )
