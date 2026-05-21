"""Tests for the opt-in LLM tier: it must be skippable and never break the
deterministic core. No real network calls are made -- _llm_request is patched.
"""
from __future__ import annotations

import argparse
import json
import urllib.error

import pytest

import extract_cli as ex
from tests.conftest import FIXTURES


def _ns(**kw: object) -> argparse.Namespace:
    base = {"silent": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _fresh_result() -> dict:
    text = ex.DEMO_DOCUMENT
    return ex.build_extraction(text, text.encode("utf-8"), "markdown", "demo.md")


def test_no_config_skips_gracefully(monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ex, "load_llm_config", lambda: None)
    result = _fresh_result()
    ex.llm_enrich(result, ex.DEMO_DOCUMENT, _ns())
    assert result["_meta"]["llm_used"] is False
    assert result["_meta"]["tiers_used"] == ["deterministic"]
    assert "no LLM config" in capsys.readouterr().err


def test_enrich_with_fake_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "load_llm_config",
                        lambda: {"provider": "anthropic", "api_key": "x", "model": "m"})
    fake = json.dumps({
        "renewal_mechanics": "auto-renews for successive one-year terms",
        "obligations": ["protect confidential information", "pay fees on time"],
        "governing_law": "ignored because deterministic already found it",
    })
    monkeypatch.setattr(ex, "_llm_request", lambda cfg, prompt, timeout=30.0: fake)
    result = _fresh_result()
    ex.llm_enrich(result, ex.DEMO_DOCUMENT, _ns())
    assert result["term"]["renewal_mechanics"]["source"] == "llm"
    assert result["term"]["renewal_mechanics"]["value"].startswith("auto-renews")
    assert [o["text"] for o in result["obligations"]][0] == "protect confidential information"
    assert all(o["source"] == "llm" for o in result["obligations"])
    assert result["_meta"]["llm_used"] is True
    assert "llm" in result["_meta"]["tiers_used"]
    # Deterministic governing_law is preserved (not overwritten by the LLM).
    assert result["governing_law"]["source"] == "deterministic"


def test_enrich_fills_only_missing_governing_law(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "load_llm_config",
                        lambda: {"provider": "openai", "api_key": "x"})
    monkeypatch.setattr(ex, "_llm_request",
                        lambda cfg, prompt, timeout=30.0: json.dumps({"governing_law": "France"}))
    text = "This contract is between A Co and B Co with no stated jurisdiction."
    result = ex.build_extraction(text, text.encode("utf-8"), "text", "x.txt")
    assert result["governing_law"]["source"] == "none"
    ex.llm_enrich(result, text, _ns())
    assert result["governing_law"] == {"value": "France", "confidence": 0.6, "source": "llm"}


def test_llm_clause_fallback_when_deterministic_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._schema_validator import validate
    monkeypatch.setattr(ex, "load_llm_config",
                        lambda: {"provider": "anthropic", "api_key": "x"})
    monkeypatch.setattr(ex, "_llm_request", lambda cfg, prompt, timeout=30.0: json.dumps(
        {"clauses": [{"title": "Confidentiality"}, {"title": "Governing Law"},
                     {"title": "Special Widget Terms"}]}))
    # A document with no detectable clause headings -> 0 deterministic clauses.
    text = ("This Agreement is made between Acme Co and Beta Co. The parties agree "
            "to maintain confidentiality. Governed by the laws of Delaware.")
    result = ex.build_extraction(text, text.encode("utf-8"), "text", "x.txt")
    assert result["clauses"] == []
    ex.llm_enrich(result, text, _ns())
    cl = result["clauses"]
    assert [c["canonical_title"] for c in cl] == ["Confidentiality", "Governing Law", "Special Widget Terms"]
    assert all(c["tier"] == "llm" and c["source"] == "llm" for c in cl)
    assert cl[0]["mapped"] is True and cl[2]["mapped"] is False
    assert result["_meta"]["llm_used"] is True and "llm" in result["_meta"]["tiers_used"]
    assert validate(result, ex.output_schema()) == []  # llm clauses are schema-conformant


def test_llm_does_not_replace_deterministic_clauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "load_llm_config",
                        lambda: {"provider": "anthropic", "api_key": "x"})
    monkeypatch.setattr(ex, "_llm_request", lambda cfg, prompt, timeout=30.0: json.dumps(
        {"clauses": [{"title": "Should Not Appear"}]}))
    text = ex.DEMO_DOCUMENT  # has H2 clauses
    result = ex.build_extraction(text, text.encode("utf-8"), "markdown", "d.md")
    assert result["clauses"] and all(c["tier"] == "h2" for c in result["clauses"])
    ex.llm_enrich(result, text, _ns())
    # Deterministic clauses are kept; the LLM clause was never requested/used.
    assert all(c["tier"] == "h2" for c in result["clauses"])
    assert not any(c["detected_title"] == "Should Not Appear" for c in result["clauses"])


def test_request_error_degrades(monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ex, "load_llm_config",
                        lambda: {"provider": "anthropic", "api_key": "x"})

    def boom(cfg: object, prompt: object, timeout: float = 30.0) -> str:
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(ex, "_llm_request", boom)
    result = _fresh_result()
    before = json.dumps(result, sort_keys=True)
    ex.llm_enrich(result, ex.DEMO_DOCUMENT, _ns())
    assert result["_meta"]["llm_used"] is False
    assert json.dumps(result, sort_keys=True) == before  # untouched
    assert "LLM request failed" in capsys.readouterr().err


def test_cli_llm_flag_without_config_is_useful(monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ex, "load_llm_config", lambda: None)
    code = ex.main([str(FIXTURES / "nda_h2.md"), "--llm"])
    assert code == 0
    cap = capsys.readouterr()
    payload = json.loads(cap.out)
    # Fully useful without the LLM: deterministic fields are all present.
    assert payload["parties"] and payload["clauses"]
    assert payload["_meta"]["llm_used"] is False
    assert "skipping --llm enrichment" in cap.err


def test_llm_config_lookup_prefers_suite_path(monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: object) -> None:
    import pathlib
    suite = tmp_path / "suite.json"  # type: ignore[operator]
    local = tmp_path / "local.json"  # type: ignore[operator]
    suite.write_text(json.dumps({"provider": "anthropic", "api_key": "SUITE"}))
    local.write_text(json.dumps({"provider": "openai", "api_key": "LOCAL"}))
    monkeypatch.setattr(ex, "LLM_CONFIG_PATHS",
                        (pathlib.Path(suite), pathlib.Path(local)))
    cfg = ex.load_llm_config()
    assert cfg is not None and cfg["api_key"] == "SUITE"
