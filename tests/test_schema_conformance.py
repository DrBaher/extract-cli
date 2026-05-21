"""Schema-conformance + golden tests.

Verifies (a) every fixture's extraction validates against the published output
schema, (b) the committed docs/spec schema is byte-identical to what the CLI
emits, and (c) extraction output matches the .expected.json goldens.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import extract_cli as ex
from tests._schema_validator import validate
from tests.conftest import FIXTURES, REPO_ROOT, all_fixture_names

SCHEMA = ex.output_schema()
SPEC_FILE = REPO_ROOT / "docs" / "spec" / "extract-output.schema.json"


@pytest.mark.parametrize("name", all_fixture_names())
def test_fixture_output_validates(name: str) -> None:
    path = FIXTURES / name
    raw, text, fmt, _warnings = ex.load_source(path)
    result = ex.build_extraction(text, raw, fmt, name)
    assert validate(result, SCHEMA) == []


@pytest.mark.parametrize("name", all_fixture_names())
def test_matches_golden(name: str) -> None:
    path = FIXTURES / name
    # Goldens are pinned to the stdlib readers (see tests/_make_goldens.py) so
    # they hold whether or not the [docx]/[pdf] extras are installed.
    raw, text, fmt, _warnings = ex.load_source(path, prefer_optional=False)
    result = ex.build_extraction(text, raw, fmt, name)
    golden = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    assert result == golden, (
        f"{name} drifted from its golden. If intentional, run `make goldens`."
    )


def test_committed_spec_matches_cli_schema() -> None:
    assert SPEC_FILE.exists(), "docs/spec/extract-output.schema.json is missing"
    committed = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    assert committed == SCHEMA, "docs/spec schema drifted; run `make spec-check`"


def test_schema_command_emits_committed_spec() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "extract_cli.py"), "schema"],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(proc.stdout) == json.loads(SPEC_FILE.read_text(encoding="utf-8"))


def test_schema_is_self_describing() -> None:
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "extract-cli" in SCHEMA["title"]
    for key in ("document", "parties", "dates", "term", "governing_law",
                "clauses", "defined_terms", "value", "_meta"):
        assert key in SCHEMA["properties"]
