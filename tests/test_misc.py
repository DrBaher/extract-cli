"""Coverage for completion scripts, rendering branches, the LLM request
builder (mocked transport), and small helpers."""
from __future__ import annotations

import json

import pytest

import extract_cli as ex
from tests.conftest import FIXTURES


# --- completion --------------------------------------------------------------

def test_completion_bash_and_zsh(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["completion", "bash"]) == 0
    assert "complete -F" in capsys.readouterr().out
    assert ex.main(["completion", "zsh"]) == 0
    assert "compdef" in capsys.readouterr().out


def test_completion_flags_handler(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["__complete", "flags"]) == 0
    assert "--llm" in capsys.readouterr().out
    assert ex.main(["__complete"]) == 0          # empty -> noop
    assert ex.main(["__complete", "bogus"]) == 0  # unknown -> noop


# --- fields / demo variants --------------------------------------------------

def test_fields_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["fields", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    tiers = {row["tier"] for row in payload}
    assert tiers == {"deterministic", "llm"}


def test_demo_table_and_silent(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["demo", "--format", "table"]) == 0
    out = capsys.readouterr().out
    assert "Clause map" in out
    assert ex.main(["demo", "--silent"]) == 0
    json.loads(capsys.readouterr().out)  # silent still emits JSON payload


# --- rendering branches ------------------------------------------------------

def test_render_table_with_llm_fields() -> None:
    text = ex.DEMO_DOCUMENT
    result = ex.build_extraction(text, text.encode("utf-8"), "markdown", "d.md")
    result["term"]["renewal_mechanics"] = ex._field("auto-renews yearly", 0.6, "llm")
    result["obligations"] = [{"text": "keep secrets", "confidence": 0.5, "source": "llm"}]
    table = ex.render_table(result, no_confidence=False)
    assert "renewal" in table
    assert "[llm]" in table


def test_render_table_empty_document() -> None:
    result = ex.build_extraction("nothing here", b"nothing here", "text", "e.txt")
    table = ex.render_table(result, no_confidence=True)
    assert "(none detected)" in table
    assert "(no clause structure detected)" in table


def test_field_subset_includes_document() -> None:
    text = ex.DEMO_DOCUMENT
    result = ex.build_extraction(text, text.encode("utf-8"), "markdown", "d.md")
    sub = ex._apply_field_subset(result, ["document"])
    assert set(sub) == {"document", "_meta"}


# --- LLM request builder (mocked transport, no real network) -----------------

class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._b = body

    def read(self) -> bytes:
        return self._b

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def test_llm_request_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_urlopen(req: object, timeout: float = 30.0) -> _FakeResp:
        seen["url"] = req.full_url  # type: ignore[attr-defined]
        return _FakeResp(json.dumps({"content": [{"type": "text", "text": "ANSWER"}]}).encode())

    monkeypatch.setattr(ex.urllib.request, "urlopen", fake_urlopen)
    out = ex._llm_request({"provider": "anthropic", "api_key": "k", "model": "m"}, "prompt")
    assert out == "ANSWER"
    assert "api.anthropic.com" in seen["url"]


def test_llm_request_openai_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_urlopen(req: object, timeout: float = 30.0) -> _FakeResp:
        seen["url"] = req.full_url  # type: ignore[attr-defined]
        return _FakeResp(json.dumps({"choices": [{"message": {"content": "YO"}}]}).encode())

    monkeypatch.setattr(ex.urllib.request, "urlopen", fake_urlopen)
    out = ex._llm_request(
        {"provider": "openai", "api_key": "k", "base_url": "https://proxy.test/v1/"}, "p")
    assert out == "YO"
    assert seen["url"] == "https://proxy.test/v1/chat/completions"


# --- small helpers -----------------------------------------------------------

def test_docx_fidelity_path() -> None:
    """When [docx] is installed, the python-docx path still yields valid,
    sensible output. Skips when the extra is absent (e.g. in CI)."""
    pytest.importorskip("docx")
    from tests._schema_validator import validate
    raw, text, fmt, _w = ex.load_source(FIXTURES / "employment_docx.docx", prefer_optional=True)
    result = ex.build_extraction(text, raw, fmt, "e.docx")
    assert validate(result, ex.output_schema()) == []
    assert any(p["name"] == "Umbrella Corporation" for p in result["parties"])


def test_pdf_fidelity_path() -> None:
    """When [pdf] is installed, the pypdf path still yields valid, sensible
    output. Skips when the extra is absent."""
    pytest.importorskip("pypdf")
    from tests._schema_validator import validate
    raw, text, fmt, _w = ex.load_source(FIXTURES / "license_pdf.pdf", prefer_optional=True)
    result = ex.build_extraction(text, raw, fmt, "l.pdf")
    assert validate(result, ex.output_schema()) == []
    assert result["governing_law"]["value"] == "State of Washington"


def test_pdf_unescape() -> None:
    assert ex._pdf_unescape(r"a\(b\)c") == "a(b)c"
    assert ex._pdf_unescape(r"line\nbreak") == "line\nbreak"
    assert ex._pdf_unescape(r"\101\102") == "AB"  # octal escapes


def test_extract_json_object_from_noise() -> None:
    assert ex._extract_json_object('prefix {"a": 1} suffix') == {"a": 1}
    assert ex._extract_json_object("no json here") is None


def test_completion_unsupported_shell_argparse() -> None:
    # argparse rejects an unknown choice before our handler runs -> exit 2.
    with pytest.raises(SystemExit) as exc:
        ex.main(["completion", "fish"])
    assert exc.value.code == 2


def test_cmd_completion_rejects_unknown_shell_directly() -> None:
    import argparse
    with pytest.raises(ex.ExtractError):
        ex.cmd_completion(argparse.Namespace(shell="powershell"))
