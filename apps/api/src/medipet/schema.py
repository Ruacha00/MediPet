from __future__ import annotations

from collections.abc import Mapping


def validate_object(value: dict[str, object], schema: Mapping[str, object]) -> str | None:
    if schema.get("type") != "object":
        return "schema"
    required = schema.get("required", ())
    if not isinstance(required, (list, tuple)) or any(name not in value for name in required):
        return "required"
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return "schema"
    if schema.get("additionalProperties") is False and any(
        name not in properties for name in value
    ):
        return "additional"
    type_checks = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, int | float) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
    }
    for name, item in value.items():
        property_schema = properties.get(name)
        if not isinstance(property_schema, Mapping):
            continue
        expected = property_schema.get("type")
        if not isinstance(expected, str):
            continue
        check = type_checks.get(expected)
        if check is not None and not check(item):
            return "type"
        allowed_values = property_schema.get("enum")
        if isinstance(allowed_values, (list, tuple)) and item not in allowed_values:
            return "enum"
    return None
