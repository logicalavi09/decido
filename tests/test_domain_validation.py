from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.clock import parse_iso, to_iso, utcnow
from app.domain.models import Condition, RuleConfig, StageConfig, WorkflowConfig
from app.domain.validation import validate_payload


def test_schema_constraints_catch_multiple_issues():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "tags", "score"],
        "properties": {
            "name": {"type": "string", "minLength": 3, "maxLength": 5},
            "tags": {"type": "array", "minItems": 2, "items": {"type": "string"}},
            "score": {"type": "integer", "minimum": 1, "maximum": 10},
            "mode": {"enum": ["a", "b"]},
        },
    }
    payload = {"name": "ab", "tags": ["x"], "score": 11, "mode": "c", "extra": True}
    codes = {error["code"] for error in validate_payload(schema, payload)}
    assert {
        "min_length",
        "min_items",
        "maximum",
        "enum_mismatch",
        "additional_property",
    } <= codes


def test_schema_type_union_and_const():
    schema = {"type": ["string", "null"], "const": None}
    assert validate_payload(schema, None) == []

    schema = {"type": "object", "properties": {"n": {"type": "number"}}}
    assert validate_payload(schema, {"n": True})[0]["code"] == "type_mismatch"


def test_condition_shape_validation():
    with pytest.raises(ValidationError):
        Condition.model_validate({"field": "x", "op": "eq", "all": []})
    with pytest.raises(ValidationError):
        Condition.model_validate({"field": "x"})
    with pytest.raises(ValidationError):
        Condition.model_validate({"all": [], "any": []})


def test_rule_and_transition_validation():
    base = {
        "id": "r1",
        "condition": {"field": "x", "op": "eq", "value": 1},
        "effect": "branch",
    }
    with pytest.raises(ValidationError):
        RuleConfig.model_validate(base)
    with pytest.raises(ValidationError):
        RuleConfig.model_validate({**base, "effect": "reject", "goto": "next"})
    with pytest.raises(ValidationError):
        StageConfig.model_validate(
            {"name": "s", "on": {"approved": {"outcome": "approved"}}}
        )


def test_workflow_graph_validation_rejects_unknown_targets():
    stage = {
        "name": "one",
        "rules": [],
        "on_pass": {"goto": "missing"},
    }
    with pytest.raises(ValidationError):
        WorkflowConfig.model_validate(
            {"name": "w", "version": "1", "stages": [stage]}
        )

    with pytest.raises(ValidationError):
        WorkflowConfig.model_validate(
            {
                "name": "w",
                "version": "1",
                "entry_stage": "nope",
                "stages": [{"name": "one", "rules": []}],
            }
        )


def test_clock_round_trip():
    value = to_iso(utcnow())
    assert parse_iso(value).tzinfo is not None
    naive = "2026-01-01T00:00:00"
    assert parse_iso(naive).tzinfo is not None
