"""Targeted tests that exercise the remaining reachable branches, to keep line
coverage at its practical maximum. (Genuinely-unreachable defensive lines and
[docx]/[pdf]-extra fidelity branches are marked `# pragma: no cover` in the
source.)"""
from __future__ import annotations

import argparse
import io
import json
import sys as _sys
import zipfile
from typing import Any

import pytest

import extract_cli as ex
from tests.conftest import FIXTURES


def _ns(**kw: object) -> argparse.Namespace:
    base = {"silent": False, "why": False}
    base.update(kw)
    return argparse.Namespace(**base)


# --- color + warn -----------------------------------------------------------

def test_color_force_on_and_isatty_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert ex._color_enabled() is True
    assert ex._c("x", "32") == "\033[32mx\033[0m"
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    class _Bad:
        def isatty(self) -> bool:
            raise ValueError("boom")
    assert ex._color_enabled(_Bad()) is False


def test_warn_silent_is_suppressed(capsys: pytest.CaptureFixture[str]) -> None:
    ex._warn(_ns(silent=True), "hush")
    assert capsys.readouterr().err == ""


# --- small helpers ----------------------------------------------------------

def test_titlecase_edges() -> None:
    assert ex._titlecase("   ") == ""
    assert ex._titlecase("IP Rights") == "IP Rights"  # acronym preserved in mixed case


def test_word_to_int_digit_and_unknown() -> None:
    assert ex._word_to_int("30") == 30
    assert ex._word_to_int("zzz") is None


def test_date_parse_none_and_unparseable_raw() -> None:
    assert ex._parse_date_to_iso("not a date") is None
    f = ex._date_field_from_str("13/13/2024", 0.85)  # matches shape, invalid month
    assert f["source"] == "deterministic" and f["confidence"] < 0.85


def test_canonicalize_empty_key() -> None:
    assert ex._canonicalize_clause("   ") == (None, False)
    assert ex._canonicalize_clause("1.") == (None, False)


def test_governing_law_and_title_none() -> None:
    assert ex.extract_governing_law("no law clause here")["source"] == "none"
    assert ex.extract_title("", None, "text") is None


def test_defined_terms_long_and_capped() -> None:
    long_phrase = '"This Is A Very Long Quoted Heading Phrase Indeed"'  # > 6 words
    many = " ".join(f'"Term {i}"' for i in range(60))
    terms = [t["term"] for t in ex.extract_defined_terms(long_phrase + " " + many)]
    assert not any("Very Long" in t for t in terms)
    assert len(terms) <= 50


def test_noise_placeholder_midstring() -> None:
    # Placeholder not at the start -> the mid-string regex branch.
    assert ex._is_noise_clause_title("Fee [ # ]% Cap")
    assert ex._is_noise_clause_title("{placeholder}")


# --- format / readers -------------------------------------------------------

def test_detect_format_by_magic_bytes(tmp_path: Any) -> None:
    p = tmp_path / "x.dat"
    p.write_bytes(b"%PDF-1.4\nrest")
    assert ex._detect_format(p, p.read_bytes()) == "pdf"
    q = tmp_path / "y.dat"
    q.write_bytes(b"PK\x03\x04rest")
    assert ex._detect_format(q, q.read_bytes()) == "docx"


def test_pdf_stream_without_endstream() -> None:
    assert ex._read_pdf_stdlib(b"%PDF\nstream\n(text) Tj") == ""


def test_pdf_decompression_budget_break(monkeypatch: pytest.MonkeyPatch) -> None:
    import zlib
    monkeypatch.setattr(ex, "MAX_DECOMPRESSED_BYTES", 10)
    blob = b"%PDF\nstream\n" + zlib.compress(b"(Hello World) Tj " * 10) + b"\nendstream"
    assert ex._read_pdf_stdlib(blob) == ""  # exceeds the tiny budget -> bail, no text


def test_html_malformed_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: object, data: object) -> None:
        raise ValueError("bad markup")
    monkeypatch.setattr(ex._HTMLTextExtractor, "feed", boom)
    out = ex._read_html("<p>hello <b>world</b></p>")
    assert "hello" in out and "<" not in out  # crude tag-strip fallback


def test_docx_empty_paragraph_stdlib() -> None:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = '<w:p/><w:p><w:r><w:t>Hello</w:t></w:r></w:p>'
    doc = f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
    assert "Hello" in ex._read_docx_stdlib(buf.getvalue())


# --- clause detection edges -------------------------------------------------

def test_clause_heading_on_last_line() -> None:
    clauses = ex.detect_clauses("## First\n\nbody text\n\n## Last")  # no trailing newline
    assert clauses[-1]["title"] == "Last"


def test_two_line_article_skips_non_heading_next_line() -> None:
    text = ("ARTICLE I\n\nThis whole next line is a long running sentence, not a heading at all.\n\n"
            "ARTICLE II\n\nCONFIDENTIALITY\n\nbody\n\nARTICLE III\n\nGOVERNING LAW\n\nbody")
    titles = [c["title"] for c in ex.detect_clauses(text)]
    assert "CONFIDENTIALITY" in titles and "GOVERNING LAW" in titles


def test_is_low_signal_each_branch() -> None:
    def base() -> dict:
        return {"parties": [], "clauses": [],
                "dates": {"effective": ex._none_field(), "expiration": ex._none_field()},
                "governing_law": ex._none_field(), "defined_terms": []}
    r = base(); r["clauses"] = [{}]; assert ex._is_low_signal(r) is False
    r = base(); r["dates"]["effective"] = ex._field("2024-01-01", 0.85); assert ex._is_low_signal(r) is False
    r = base(); r["governing_law"] = ex._field("X", 0.8); assert ex._is_low_signal(r) is False
    r = base(); r["defined_terms"] = [{"term": "X"}]; assert ex._is_low_signal(r) is False
    assert ex._is_low_signal(base()) is True


# --- LLM internals (mocked transport) ---------------------------------------

class _Resp:
    def __init__(self, body: bytes) -> None:
        self._b = body

    def read(self) -> bytes:
        return self._b

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def test_llm_request_openai_no_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex.urllib.request, "urlopen",
                        lambda req, timeout=30.0: _Resp(json.dumps({"choices": []}).encode()))
    assert ex._llm_request({"provider": "openai", "api_key": "k"}, "p") is None


def test_extract_json_object_invalid() -> None:
    assert ex._extract_json_object("prefix {not valid json} suffix") is None


def test_llm_clause_map_skips() -> None:
    cm = ex._llm_clause_map(
        [{"title": ""}, 123, {"title": "Recitals"}, {"title": "Confidentiality"},
         {"title": "Confidentiality"}], "Confidentiality body")
    assert [c["canonical_title"] for c in cm] == ["Confidentiality"]


def test_load_llm_config_malformed(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    bad = tmp_path / "llm.json"
    bad.write_text("{not json")
    monkeypatch.setattr(ex, "LLM_CONFIG_PATHS", (bad,))
    assert ex.load_llm_config() is None


def test_llm_enrich_empty_and_unparseable(monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ex, "load_llm_config", lambda: {"provider": "anthropic", "api_key": "k"})
    text = "x"
    monkeypatch.setattr(ex, "_llm_request", lambda c, p, timeout=30.0: "")
    ex.llm_enrich(ex.build_extraction(text, text.encode(), "text", "x.txt"), text, _ns())
    assert "no content" in capsys.readouterr().err
    monkeypatch.setattr(ex, "_llm_request", lambda c, p, timeout=30.0: "not json at all")
    ex.llm_enrich(ex.build_extraction(text, text.encode(), "text", "x.txt"), text, _ns())
    assert "could not parse" in capsys.readouterr().err


# --- rendering / CLI edges --------------------------------------------------

def test_render_table_unmapped_legend() -> None:
    r = ex.build_extraction("## Zorblax Provisions\n\nbody", b"x", "markdown", "x.md")
    assert "* = not mapped" in ex.render_table(r, no_confidence=False)


def test_render_table_jurisdiction_amounts_signatories() -> None:
    r = ex.build_extraction("body", b"x", "markdown", "x.md")
    r["jurisdiction"] = ex._field("US-DE", ex.CONF_JURISDICTION)
    r["amounts"] = [{"value": "$1", "confidence": 0.6, "source": "deterministic"},
                    {"value": "$2", "confidence": 0.6, "source": "deterministic"}]
    r["signatories"] = [{"name": "Jane Doe", "title": "CEO",
                         "confidence": ex.CONF_SIGNATORY, "source": "deterministic"}]
    table = ex.render_table(r, no_confidence=False)
    assert "US-DE" in table
    assert "+1 more" in table
    assert "Signatories (1)" in table and "Jane Doe - CEO" in table


def test_cli_silent_table_suppresses_human_view(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main([str(FIXTURES / "nda_h2.md"), "--silent", "--format", "table"]) == 0
    assert "Clause map" not in capsys.readouterr().out


def test_main_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


# --- last reachable edges ---------------------------------------------------

def test_parties_skips_empty_capture() -> None:
    # The second "party" is just a parenthetical role -> cleans to an empty
    # name and is skipped; the first is kept.
    parties = ex.extract_parties('between Acme Corp and ("Receiving Party")')
    assert [p["name"] for p in parties] == ["Acme Corp"]


def test_signatories_skips_dupes_short_and_reserved() -> None:
    text = "By: Jane Doe\nName: Jane Doe\nName: a\nName: the\n"
    s = ex.extract_signatories(text)
    assert [x["name"] for x in s] == ["Jane Doe"]


def test_pdf_text_tj_array_branch() -> None:
    # A TJ array of strings inside a text object.
    assert ex._pdf_text_from_content(b"BT [(Hello) (World)] TJ ET") == "HelloWorld"
