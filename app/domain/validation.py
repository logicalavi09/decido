from __future__ import annotations

import re
from typing import Any

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _type_matches(expected: str, value: Any) -> bool:
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    python_type = _TYPE_MAP.get(expected)
    if python_type is None:
        return True
    if expected == "boolean":
        return isinstance(value, bool)
    return isinstance(value, python_type)


def _error(path: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": path, "code": code, "message": message}
    payload.update(extra)
    return payload


def validate_payload(
    schema: dict[str, Any] | None, payload: Any
) -> list[dict[str, Any]]:
    """Validate a JSON value against the supported JSON-Schema subset.

    Supported: type (including unions), required, properties, items,
    additionalProperties (bool), enum, const, minimum, maximum,
    minLength, maxLength, pattern, minItems, maxItems.
    """

    errors: list[dict[str, Any]] = []
    if not schema:
        return errors
    _validate(schema, payload, "$", errors)
    return errors


def _validate(
    schema: dict[str, Any], value: Any, path: str, errors: list[dict[str, Any]]
) -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_type_matches(t, value) for t in types):
            errors.append(
                _error(
                    path,
                    "type_mismatch",
                    f"expected type {expected_type}, got {type(value).__name__}",
                )
            )
            return

    if "const" in schema and value != schema["const"]:
        errors.append(
            _error(path, "const_mismatch", f"expected constant {schema['const']!r}")
        )

    if "enum" in schema and value not in schema["enum"]:
        errors.append(
            _error(path, "enum_mismatch", f"expected one of {schema['enum']!r}")
        )

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(
                _error(
                    path,
                    "min_length",
                    f"must be at least {schema['minLength']} characters",
                )
            )
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(
                _error(
                    path,
                    "max_length",
                    f"must be at most {schema['maxLength']} characters",
                )
            )
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(
                        _error(
                            path,
                            "pattern_mismatch",
                            f"must match pattern {schema['pattern']}",
                        )
                    )
            except re.error:
                errors.append(
                    _error(path, "invalid_schema_pattern", "schema pattern is invalid")
                )
        if value == "" and schema.get("minLength", 0) > 0:
            errors.append(_error(path, "empty_string", "must not be empty"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(
                _error(path, "minimum", f"must be >= {schema['minimum']}")
            )
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(
                _error(path, "maximum", f"must be <= {schema['maximum']}")
            )

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(
                    _error(
                        f"{path}.{key}",
                        "required",
                        "required property is missing",
                    )
                )
        properties = schema.get("properties", {})
        for key, child in properties.items():
            if key in value:
                _validate(child, value[key], f"{path}.{key}", errors)
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for key in value:
                if key not in properties:
                    errors.append(
                        _error(
                            f"{path}.{key}",
                            "additional_property",
                            "additional properties are not allowed",
                        )
                    )

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(
                _error(path, "min_items", f"must contain at least {schema['minItems']} items")
            )
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(
                _error(path, "max_items", f"must contain at most {schema['maxItems']} items")
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate(item_schema, item, f"{path}[{index}]", errors)
