"""Tests for the ported clause-detection cascade and canonical normalization."""
from __future__ import annotations

import extract_cli as ex


def test_tier1_h2() -> None:
    text = "## Confidentiality\n\nbody\n\n## Term\n\nbody"
    clauses = ex.detect_clauses(text)
    assert [c["tier"] for c in clauses] == ["h2", "h2"]
    assert [c["title"] for c in clauses] == ["Confidentiality", "Term"]


def test_tier2_bold_numbered() -> None:
    text = ("**1. Purpose**\n\nbody about purpose\n\n"
            "**2. Confidentiality**\n\nbody about confidentiality")
    clauses = ex.detect_clauses(text)
    assert [c["tier"] for c in clauses] == ["bold-numbered", "bold-numbered"]
    assert clauses[0]["title"] == "Purpose"


def test_tier3_all_caps() -> None:
    text = "intro line\n\nCONFIDENTIALITY\n\nbody text\n\nGOVERNING LAW\n\nmore"
    clauses = ex.detect_clauses(text)
    assert [c["tier"] for c in clauses] == ["all-caps", "all-caps"]


def test_cascade_priority_h2_wins() -> None:
    # An H2 present means the bold/all-caps fallbacks must not fire.
    text = "## Real Heading\n\n**1. Not A Heading**\n\nALSO NOT A HEADING\n\nbody"
    clauses = ex.detect_clauses(text)
    assert all(c["tier"] == "h2" for c in clauses)
    assert len(clauses) == 1


def test_single_token_all_caps_min_letters() -> None:
    # "TER" (3 letters) should not qualify; "TERM" should.
    assert not ex._qualifies_as_all_caps_heading("TER")
    assert ex._qualifies_as_all_caps_heading("TERM")
    assert ex._qualifies_as_all_caps_heading("GOVERNING LAW")  # multi-token


def test_roman_numeral_stripping() -> None:
    # Roman numerals 1-39 with longer alternatives first (bare V/X must work).
    cases = {
        "Article I. Definitions": "Definitions",
        "Article V. Term": "Term",
        "Article X. Governing Law": "Governing Law",
        "Section IV. Confidentiality": "Confidentiality",
        "Article XXIV. Survival": "Survival",
        "Article XXXIX. Miscellaneous": "Miscellaneous",
        "1. Purpose": "Purpose",
        "1.2.3 Sub Clause": "Sub Clause",
        "(12) Notices": "Notices",
        "§ 4.2 Payment": "Payment",
    }
    for raw, expected in cases.items():
        assert ex._strip_clause_number(raw) == expected, raw


def test_canonicalize_known_aliases() -> None:
    assert ex._canonicalize_clause("Non-Disclosure") == ("Confidentiality", True)
    assert ex._canonicalize_clause("CONFIDENTIALITY OBLIGATIONS") == ("Confidentiality", True)
    assert ex._canonicalize_clause("Choice of Law") == ("Governing Law", True)
    assert ex._canonicalize_clause("Term and Termination") == ("Termination", True)


def test_canonicalize_unmapped_titlecased() -> None:
    canon, mapped = ex._canonicalize_clause("PLATFORM SPECIFIC RIDER")
    assert mapped is False
    assert canon == "Platform Specific Rider"


def test_clause_spans_within_bounds_and_ordered() -> None:
    text = ("## Definitions\n\nbody one\n\n## Confidentiality\n\nbody two\n\n"
            "## Governing Law\n\nbody three")
    clauses = ex.extract_clauses(text)
    last_end = -1
    for c in clauses:
        assert 0 <= c["span"]["start"] < c["span"]["end"] <= len(text)
        assert c["span"]["start"] > last_end - 1
        last_end = c["span"]["start"]
        assert c["source"] == "deterministic"
        assert c["tier"] in ("h2", "bold-numbered", "all-caps", "explicit")
        assert 0.0 <= c["confidence"] <= 1.0


def test_unmapped_clause_lower_confidence_than_mapped() -> None:
    mapped = ex.extract_clauses("## Confidentiality\n\nx")[0]
    unmapped = ex.extract_clauses("## Zorblax Provisions\n\nx")[0]
    assert mapped["confidence"] > unmapped["confidence"]
