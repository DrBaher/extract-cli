"""End-to-end CLI tests driving extract_cli.main() in-process."""
from __future__ import annotations

import json
from typing import Any

import pytest

import extract_cli as ex
from tests.conftest import FIXTURES


def _has_key(obj: Any, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        ex.main(["--version"])
    assert exc.value.code == 0
    assert "extract-cli 0.1.0" in capsys.readouterr().out


def test_demo_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["demo"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["document"]["format"] == "markdown"
    assert any(c["canonical_title"] == "Confidentiality" for c in payload["clauses"])


def test_schema_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["$schema"].endswith("2020-12/schema")


def test_fields_command_lists_tiers(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["fields"]) == 0
    out = capsys.readouterr().out
    assert "deterministic" in out and "llm" in out


def test_extract_bare_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main([str(FIXTURES / "nda_h2.md")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [p["name"] for p in payload["parties"]] == [
        "Northwind Traders, Inc.", "Contoso Analytics LLC"
    ]
    assert payload["governing_law"]["value"] == "State of New York"


def test_extract_explicit_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["extract", str(FIXTURES / "nda_h2.md")]) == 0
    assert json.loads(capsys.readouterr().out)["document"]["format"] == "markdown"


def test_fields_subset(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main([str(FIXTURES / "nda_h2.md"), "--fields", "parties,clauses"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"parties", "clauses", "_meta"}


def test_no_confidence_strips_markers(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main([str(FIXTURES / "nda_h2.md"), "--no-confidence"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert not _has_key(payload, "confidence")
    assert not _has_key(payload, "source")
    # Field envelopes collapse to bare values.
    assert payload["governing_law"] == "State of New York"
    assert payload["defined_terms"]  # list of strings now
    assert all(isinstance(t, str) for t in payload["defined_terms"])


def test_table_format(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main([str(FIXTURES / "nda_h2.md"), "--format", "table"]) == 0
    out = capsys.readouterr().out
    assert "Clause map" in out and "Parties" in out


def test_low_signal_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    code = ex.main([str(FIXTURES / "scanned.pdf")])
    assert code == 1
    err = capsys.readouterr().err
    assert "scanned" in err or "no high-signal" in err


def test_missing_file_exit_2(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main([str(tmp_path / "nope.md")]) == 2
    assert "error:" in capsys.readouterr().err


def test_completion_handler(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["__complete", "commands"]) == 0
    assert "schema" in capsys.readouterr().out.split()


def test_no_color_disables_ansi(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["--no-color", "fields"]) == 0
    assert "\033[" not in capsys.readouterr().out


def test_why_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main([str(FIXTURES / "nda_h2.md"), "--why"]) == 0
    cap = capsys.readouterr()
    assert "[why]" in cap.err
    assert "[why]" not in cap.out  # stdout stays clean JSON
    json.loads(cap.out)
