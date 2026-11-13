from __future__ import annotations

import pytest

from app.domain.models import Condition, RuleConfig, StageConfig
from app.domain.rule_engine import (
    MISSING,
    evaluate_condition,
    evaluate_rules,
    resolve_path,
)

INPUT = {
    "score": 700,
    "name": "Ada",
    "country": "US",
    "tags": ["vip", "beta"],
    "nested": {"value": 10},
    "items": [{"id": 1}, {"id": 2}],
    "empty": None,
}

CONTEXT = {
    **INPUT,
    "input": INPUT,
    "dependencies": {"bureau": {"verified": True}},
}


def condition(field: str, op: str, value=None) -> Condition:
    payload = {"field": field, "op": op}
    if value is not None:
        payload["value"] = value
    return Condition.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "op", "value", "expected"),
    [
        ("score", "eq", 700, True),
        ("score", "eq", 701, False),
        ("score", "neq", 701, True),
        ("score", "gt", 699, True),
        ("score", "gte", 700, True),
        ("score", "lt", 701, True),
        ("score", "lte", 700, True),
        ("country", "in", ["US", "CA"], True),
        ("country", "not_in", ["CA"], True),
        ("tags", "contains", "vip", True),
        ("name", "contains", "da", True),
        ("tags", "not_contains", "admin", True),
        ("name", "exists", None, True),
        ("missing", "exists", None, False),
        ("empty", "exists", None, False),
        ("missing", "not_exists", None, True),
        ("name", "matches", "^A.*a$", True),
        ("score", "between", [600, 750], True),
        ("tags", "len_gte", 2, True),
        ("tags", "len_lte", 2, True),
        ("tags", "len_eq", 2, True),
    ],
)
def test_operators(field, op, value, expected):
    result = evaluate_condition(condition(field, op, value), CONTEXT)
    assert result.passed is expected


def test_numeric_operator_on_non_number_reports_error():
    result = evaluate_condition(condition("name", "gt", 3), CONTEXT)
    assert result.passed is False
    assert "numeric" in result.error


def test_invalid_regex_reports_error():
    result = evaluate_condition(condition("name", "matches", "["), CONTEXT)
    assert result.passed is False
    assert "regular expression" in result.error


def test_invalid_between_shape_reports_error():
    result = evaluate_condition(condition("score", "between", [1]), CONTEXT)
    assert result.passed is False
    assert "between" in result.error


def test_len_operator_requires_integer():
    result = evaluate_condition(condition("tags", "len_gte", "2"), CONTEXT)
    assert result.passed is False
    assert "integer" in result.error


def test_composite_all_any_not():
    all_condition = Condition.model_validate(
        {
            "all": [
                {"field": "score", "op": "gte", "value": 700},
                {"field": "country", "op": "eq", "value": "US"},
            ]
        }
    )
    assert evaluate_condition(all_condition, CONTEXT).passed is True

    any_condition = Condition.model_validate(
        {
            "any": [
                {"field": "score", "op": "lt", "value": 700},
                {"field": "country", "op": "eq", "value": "US"},
            ]
        }
    )
    assert evaluate_condition(any_condition, CONTEXT).passed is True

    not_condition = Condition.model_validate(
        {"not": {"field": "score", "op": "lt", "value": 700}}
    )
    assert evaluate_condition(not_condition, CONTEXT).passed is True


def test_condition_value_can_reference_context():
    cond = Condition.model_validate(
        {
            "field": "dependencies.bureau.verified",
            "op": "eq",
            "value": "$input.expected_verified",
        }
    )
    context = {**CONTEXT, "input": {**CONTEXT["input"], "expected_verified": True}}
    assert evaluate_condition(cond, context).passed is True


def test_resolve_path_supports_list_index():
    assert resolve_path(CONTEXT, "items.1.id") == 2
    assert resolve_path(CONTEXT, "items.9.id") is MISSING


def _stage(rules: list[RuleConfig]) -> StageConfig:
    return StageConfig(name="stage", rules=rules)


def _rule(rule_id: str, effect: str, mode: str = "assert", **extra) -> RuleConfig:
    payload = {
        "id": rule_id,
        "mode": mode,
        "condition": {"field": "score", "op": "gte", "value": 700},
        "effect": effect,
        "reason": f"{rule_id} reason",
    }
    payload.update(extra)
    return RuleConfig.model_validate(payload)


def test_assert_failure_decides_and_traces_are_kept():
    evaluation = evaluate_rules(
        _stage(
            [
                _rule("passes", "reject"),
                _rule("fails", "manual_review", **{"condition": {"field": "score", "op": "gte", "value": 800}}),
                _rule("ignored", "retry", **{"condition": {"field": "score", "op": "gte", "value": 900}}),
            ]
        ),
        CONTEXT,
    )
    assert evaluation.passed is False
    assert evaluation.failure_effect.value == "manual_review"
    assert evaluation.decided_by == "fails"
    assert len(evaluation.traces) == 3
    assert evaluation.traces[0].triggered is False
    assert evaluation.traces[1].triggered is True
    assert evaluation.traces[1].decided is True


def test_guard_continue_does_not_decide():
    evaluation = evaluate_rules(
        _stage([_rule("advisory", "continue", mode="guard", reason="advisory")]),
        CONTEXT,
    )
    assert evaluation.passed is True
    assert evaluation.failure_effect is None
    assert evaluation.traces[0].triggered is True


def test_branch_stops_evaluation_and_sets_goto():
    rules = [
        _rule(
            "fast",
            "branch",
            mode="guard",
            goto="auto_approval",
        ),
        _rule("later", "reject"),
    ]
    evaluation = evaluate_rules(_stage(rules), CONTEXT)
    assert evaluation.branch_goto == "auto_approval"
    assert len(evaluation.traces) == 1
