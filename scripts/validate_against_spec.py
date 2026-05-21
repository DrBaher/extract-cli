#!/usr/bin/env python3
"""Validate an extract-cli JSON document (on stdin) against the published
output schema in docs/spec/extract-output.schema.json.

Self-contained (no third-party deps) so it works in CI, in the wheel
smoke-test, and in the README's `extract <fixture> | python ...` example.

Usage:
    extract some_contract.md | python scripts/validate_against_spec.py
    python scripts/validate_against_spec.py < output.json
Exit code 0 = valid, 1 = invalid (errors printed to stderr).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

SPEC = Path(__file__).resolve().parent.parent / "docs" / "spec" / "extract-output.schema.json"

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _resolve(ref: str, root: Dict[str, Any]) -> Dict[str, Any]:
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _validate(value: Any, schema: Dict[str, Any], root: Dict[str, Any],
              path: str, errors: List[str]) -> None:
    if "$ref" in schema:
        _validate(value, _resolve(schema["$ref"], root), root, path, errors)
        return
    if not schema:
        return
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPE_CHECKS[t](value) for t in types):
            errors.append(f"{path}: expected {schema['type']}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str) and "pattern" in schema and re.search(schema["pattern"], value) is None:
        errors.append(f"{path}: {value!r} fails pattern {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > {schema['maximum']}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required {req!r}")
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


def main() -> int:
    try:
        instance = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"not valid JSON on stdin: {e}", file=sys.stderr)
        return 1
    schema = json.loads(SPEC.read_text(encoding="utf-8"))
    errors: List[str] = []
    _validate(instance, schema, schema, "$", errors)
    if errors:
        for err in errors:
            print(f"SCHEMA ERROR {err}", file=sys.stderr)
        return 1
    print("valid against extract-output.schema.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
