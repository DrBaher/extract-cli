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


def test_docx_heading_style_helpers() -> None:
    assert ex._is_heading_style("Heading1")
    assert ex._is_heading_style("Heading 2".replace(" ", ""))
    assert ex._is_heading_style("Title")
    assert ex._is_heading_style("h3")
    assert not ex._is_heading_style("Plain")
    assert not ex._is_heading_style(None)
    # Run-in heading: title is the lead before the sentence body.
    assert ex._docx_heading_title("Payment.  Customer will pay the fees.") == "Payment"
    assert ex._docx_heading_title("Governing Law") == "Governing Law"
    # A full sentence carrying a heading style is rejected (not a clause title).
    assert ex._docx_heading_title(
        "Either party may terminate this Agreement upon material breach that "
        "remains uncured for thirty days.") is None


def test_docx_heading_styles_drive_clause_map() -> None:
    """The Word-styled fixture's clauses come from Heading1 styles (their
    numbers are auto-generated), detected via the H2 tier; the sentence that
    merely carries a heading style is not a clause."""
    raw, text, fmt, _w = ex.load_source(FIXTURES / "heading_docx.docx", prefer_optional=False)
    result = ex.build_extraction(text, raw, fmt, "heading_docx.docx")
    assert result["clauses"], "heading-styled docx should yield clauses"
    canon = {c["canonical_title"] for c in result["clauses"]}
    assert {"Confidentiality", "Payment", "Governing Law"} <= canon
    assert all(c["tier"] == "h2" for c in result["clauses"])
    # The full-sentence "Either party may terminate ..." must not appear.
    assert not any("terminate this Agreement" in c["detected_title"] for c in result["clauses"])
    assert [p["name"] for p in result["parties"]] == ["Initech Software, Inc.", "Globex Corporation"]


def test_load_source_rejects_directory(tmp_path: Any) -> None:
    with pytest.raises(ex.ExtractError):
        ex.load_source(tmp_path)


def test_pdf_unescape_control_and_unknown_escapes() -> None:
    assert ex._pdf_unescape(r"a\tb\rc") == "a\tb\rc"
    assert ex._pdf_unescape(r"\bx\fy") == "xy"     # \b and \f drop to nothing
    assert ex._pdf_unescape(r"\q") == "q"          # unknown escape -> literal char


def test_jurisdiction_contained_name() -> None:
    assert ex.extract_jurisdiction(ex._field("Delaware, USA", 0.85))["value"] == "US-DE"


def test_signatories_capped_at_twelve() -> None:
    text = "\n".join(f"By: Person Number {i}" for i in range(20))
    assert len(ex.extract_signatories(text)) == 12


def test_amounts_capped() -> None:
    text = " ".join(f"${i},000" for i in range(40))
    assert len(ex.extract_amounts(text)) <= 30


def test_main_broken_pipe_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # `extract … | head` closes the pipe early; main() must exit 0, not crash.
    # main()'s handler calls sys.stdout.close(), so give it a throwaway stdout
    # (monkeypatch restores the real one) instead of closing pytest's capture.
    import io
    import sys as _sys

    def boom(*_a: object, **_k: object) -> int:
        raise BrokenPipeError()
    monkeypatch.setattr(ex, "cmd_fields", boom)
    monkeypatch.setattr(_sys, "stdout", io.StringIO())
    assert ex.main(["fields"]) == 0


def test_oversized_file_refused(tmp_path: Any) -> None:
    p = tmp_path / "huge.txt"
    with open(p, "wb") as f:
        f.truncate(ex.MAX_INPUT_BYTES + 1)
    with pytest.raises(ex.ExtractError):
        ex.load_source(p)


def test_docx_zip_bomb_guard(tmp_path: Any) -> None:
    # A .docx whose document.xml decompresses past the cap is refused gracefully
    # (no OOM): the reader checks the uncompressed size in the zip header first.
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", b" " * (ex.MAX_DECOMPRESSED_BYTES + 1))
    p = tmp_path / "bomb.docx"
    p.write_bytes(buf.getvalue())
    assert p.stat().st_size < 1_000_000  # tiny on disk
    raw, text, fmt, warnings = ex.load_source(p)
    assert fmt == "docx" and text == ""
    assert any("decompress" in w for w in warnings)


def test_numbered_docx_clauses() -> None:
    """A DOCX whose clauses are w:numPr list paragraphs (no heading style, no
    visible number) still yields a clause map; a deep numbered body sentence is
    excluded."""
    raw, text, fmt, _w = ex.load_source(FIXTURES / "numbered_docx.docx", prefer_optional=False)
    result = ex.build_extraction(text, raw, fmt, "numbered_docx.docx")
    canon = {c["canonical_title"] for c in result["clauses"]}
    assert {"Definitions", "Confidentiality", "Governing Law"} <= canon
    assert not any("remains fully liable" in c["detected_title"] for c in result["clauses"])
    assert [p["name"] for p in result["parties"]][0] == "Globex Cloud, Inc."


def test_html_extraction() -> None:
    raw, text, fmt, _w = ex.load_source(FIXTURES / "services_html.html")
    assert fmt == "html"
    # script/style content is dropped; entities are unescaped.
    assert "this should never appear" not in text
    result = ex.build_extraction(text, raw, fmt, "services_html.html")
    assert result["document"]["format"] == "html"
    assert [p["name"] for p in result["parties"]] == ["Initrode Systems, Inc.", "Hooli LLC"]
    assert result["governing_law"]["value"] == "State of California"
    assert result["dates"]["effective"]["value"] == "2023-03-15"
    canon = {c["canonical_title"] for c in result["clauses"]}
    assert {"Payment", "Termination", "Confidentiality", "Governing Law"} <= canon


def test_html_detected_by_content_sniff(tmp_path: Any) -> None:
    # HTML masquerading as .txt (e.g. a SEC EDGAR full submission) is sniffed.
    p = tmp_path / "exhibit.txt"
    p.write_text("<html><body><p>between A Co and B Co</p></body></html>")
    _raw, _text, fmt, _w = ex.load_source(p)
    assert fmt == "html"


def test_html_malformed_does_not_crash() -> None:
    assert ex._read_html("<p>unclosed <b>bold <div>text") is not None


def test_pdf_text_only_inside_bt_et() -> None:
    # Strings outside BT/ET (font/signature/metadata stream bytes that happen to
    # contain parentheses) must be ignored; only text objects yield text.
    content = b"(garbage outside) /Font << >> BT (real text) Tj ET (more garbage)"
    assert ex._pdf_text_from_content(content) == "real text"


def test_pdf_mostly_printable_backstop() -> None:
    assert ex._mostly_printable("Hello, world")
    assert not ex._mostly_printable("\x00\x01\x02\x03\x04\x05\x06\x07")
    assert not ex._mostly_printable("")


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
