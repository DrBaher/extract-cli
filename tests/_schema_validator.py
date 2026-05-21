"""A tiny, dependency-free JSON Schema (2020-12 subset) validator.

The suite is stdlib-only and the dev extra is just coverage+mypy -- no
`jsonschema`. This validator covers exactly the keywords used by
extract-cli's output schema: type, required, properties, additionalProperties
(bool), items, enum, const, minimum, maximum, pattern, and local $ref into
$defs. That's enough for the schema-conformance test to be meaningful without
pulling a dependency.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

JSON = Dict[str, Any]

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(Exception):
    pass


def _resolve_ref(ref: str, root: JSON) -> JSON:
    if not ref.startswith("#/"):
        raise SchemaError(f"unsupported $ref (only local refs): {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check_type(value: Any, type_spec: Any, path: str, errors: List[str]) -> None:
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    if not any(_TYPE_CHECKS[t](value) for t in types):
        errors.append(f"{path}: expected type {type_spec}, got {type(value).__name__}")


def _validate(value: Any, schema: JSON, root: JSON, path: str, errors: List[str]) -> None:
    if "$ref" in schema:
        _validate(value, _resolve_ref(schema["$ref"], root), root, path, errors)
        return
    if not schema:  # {} accepts anything
        return

    if "type" in schema:
        _check_type(value, schema["type"], path, errors)

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum {schema['enum']}")

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: {value!r} != const {schema['const']!r}")

    if isinstance(value, str) and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: {value!r} does not match pattern {schema['pattern']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")

    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required property {req!r}")
        props = schema.get("properties", {})
        for key, sub in value.items():
            if key in props:
                _validate(sub, props[key], root, f"{path}.{key}", errors)
            else:
                ap = schema.get("additionalProperties", True)
                if ap is False:
                    errors.append(f"{path}: additional property {key!r} not allowed")
                elif isinstance(ap, dict):
                    _validate(sub, ap, root, f"{path}.{key}", errors)

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _validate(item, schema["items"], root, f"{path}[{i}]", errors)


def validate(instance: Any, schema: JSON) -> List[str]:
    """Return a list of validation error strings (empty == valid)."""
    errors: List[str] = []
    _validate(instance, schema, schema, "$", errors)
    return errors
