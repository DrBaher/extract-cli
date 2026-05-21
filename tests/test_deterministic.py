"""Unit tests for the deterministic extraction tier (no LLM, no network)."""
from __future__ import annotations

import extract_cli as ex


def test_parties_between_simple() -> None:
    parties = ex.extract_parties("This is made between Foo Inc and Bar Ltd, dated later.")
    names = [p["name"] for p in parties]
    assert names == ["Foo Inc", "Bar Ltd"]
    assert all(p["source"] == "deterministic" for p in parties)
    assert all(0.0 <= p["confidence"] <= 1.0 for p in parties)


def test_parties_with_roles() -> None:
    text = ('by and between Acme Corp. (the "Disclosing Party") and '
            'Beta LLC (the "Receiving Party"), dated March 1, 2024.')
    parties = ex.extract_parties(text)
    assert parties[0]["name"] == "Acme Corp."
    assert parties[0]["role"] == "Disclosing Party"
    assert parties[1]["name"] == "Beta LLC"
    assert parties[1]["role"] == "Receiving Party"


def test_parties_linebreak_handled_by_build() -> None:
    # build_extraction flattens whitespace, so a party/role that wraps across a
    # line is matched whole.
    text = ('This Agreement is made by and between Acme Corp. (the "Disclosing\n'
            'Party") and Beta LLC (the "Receiving Party").')
    r = ex.build_extraction(text, text.encode("utf-8"), "text", "x.txt")
    assert [p["name"] for p in r["parties"]] == ["Acme Corp.", "Beta LLC"]
    assert r["parties"][0]["role"] == "Disclosing Party"


def test_parties_skip_and_inside_description() -> None:
    # An "and" inside a party's own description must not split the parties.
    text = ("between Blade Ventures Inc., a Nevada corporation having offices at "
            "1 Main St and doing business as Foo (\"Client\"), and KPMG LP")
    parties = ex.extract_parties(text)
    assert [p["name"] for p in parties] == ["Blade Ventures Inc.", "KPMG LP"]


def test_party_name_descriptors_trimmed() -> None:
    assert ex._clean_party_name("Visteon Corporation, a Delaware corporation") == "Visteon Corporation"
    assert ex._clean_party_name("Foo Inc. doing business as Bar") == "Foo Inc."
    assert ex._clean_party_name("Baz LLC having its principal office at X") == "Baz LLC"


def test_parties_none() -> None:
    assert ex.extract_parties("There are no parties named here.") == []


def test_dates_iso_normalization() -> None:
    cases = {
        "effective as of January 15, 2025": "2025-01-15",
        "dated 03/04/2024": "2024-03-04",
        "made and entered into on 2023-06-01": "2023-06-01",
        "entered into as of 1st day of June, 2023": "2023-06-01",
    }
    for text, iso in cases.items():
        out = ex.extract_dates(text)["effective"]
        assert out["value"] == iso, (text, out)
        assert out["source"] == "deterministic"


def test_dates_effective_date_label_and_as_of() -> None:
    # The "(the "Effective Date")" anchor, with the date wrapping a newline.
    text = 'between A and B as of August\n31, 2016 (the "Effective Date").'
    assert ex.extract_dates(text)["effective"]["value"] == "2016-08-31"
    # Bare "as of <date>" cue.
    assert ex.extract_dates("dated as of June 1, 2023")["effective"]["value"] == "2023-06-01"


def test_term_length_rejects_non_number() -> None:
    # "...for consecutive days" must NOT be reported as a term length.
    text = "the Employment Period shall run for consecutive days as scheduled"
    assert ex.extract_term(text)["length"]["source"] == "none"


def test_title_skips_sgml_wrapper() -> None:
    text = "<DOCUMENT>\n<TYPE>EX-10\n<TEXT>\n\nEMPLOYMENT AGREEMENT\n\nbody"
    assert ex.extract_title(text, None, "text") == "EMPLOYMENT AGREEMENT"


def test_dates_missing() -> None:
    out = ex.extract_dates("no dates in here")
    assert out["effective"] == ex._none_field()
    assert out["expiration"]["source"] == "none"


def test_governing_law() -> None:
    out = ex.extract_governing_law(
        "This Agreement shall be governed by the laws of the State of New York."
    )
    assert out["value"] == "State of New York"
    assert out["confidence"] > 0


def test_governing_law_stops_before_trailing_clause() -> None:
    out = ex.extract_governing_law(
        "governed by and construed in accordance with the laws of the State of "
        "Delaware, without regard to its conflict-of-laws principles."
    )
    assert out["value"] == "State of Delaware"


def test_governing_law_linebreak_handled_by_build() -> None:
    # A jurisdiction that wraps a line ("...the Province\nof Ontario") is
    # matched whole because build_extraction flattens whitespace first.
    text = ("This Agreement shall be governed by the laws of the Province\n"
            "of Ontario and the federal laws of Canada.")
    r = ex.build_extraction(text, text.encode("utf-8"), "text", "x.txt")
    assert r["governing_law"]["value"] == "Province of Ontario"


def test_governing_law_missing() -> None:
    assert ex.extract_governing_law("nothing about law")["source"] == "none"


def test_term_length_and_notice() -> None:
    text = ("The initial term of this Agreement is three (3) years. Either party "
            "may terminate upon thirty (30) days' written notice.")
    term = ex.extract_term(text)
    assert term["length"]["value"] == "3 years"
    assert term["notice_period_days"]["value"] == 30


def test_auto_renew_positive() -> None:
    text = ("shall automatically renew for successive one-year terms unless either "
            "party gives sixty (60) days' written notice of non-renewal.")
    assert ex.extract_term(text)["auto_renew"]["value"] is True


def test_auto_renew_negative() -> None:
    text = "This Agreement shall not automatically renew at the end of the term."
    assert ex.extract_term(text)["auto_renew"]["value"] is False


def test_auto_renew_unknown() -> None:
    assert ex.extract_term("The term is one year.")["auto_renew"]["source"] == "none"


def test_value_money() -> None:
    assert ex.extract_value("a fee of $250,000 is due")["value"] == "$250,000"
    assert ex.extract_value("budget is USD 1.5 million")["value"].startswith("USD")
    assert ex.extract_value("no money")["source"] == "none"


def test_defined_terms() -> None:
    text = ('the "Agreement" between the parties; "Confidential Information" '
            'means data; and (the "Receiving Party").')
    terms = [t["term"] for t in ex.extract_defined_terms(text)]
    assert "Agreement" in terms
    assert "Confidential Information" in terms
    assert "Receiving Party" in terms


def test_field_envelope_shape() -> None:
    f = ex._field("x", 0.876)
    assert set(f) == {"value", "confidence", "source"}
    assert f["confidence"] == 0.88  # rounded to 2dp
    assert f["source"] == "deterministic"
    none = ex._none_field()
    assert none == {"value": None, "confidence": 0.0, "source": "none"}
    assert ex._field(None, 0.9) == none


def test_title_from_h1_then_fallback() -> None:
    assert ex.extract_title("# Big Title\n\nbody", None, "markdown") == "Big Title"
    assert ex.extract_title("FIRST LINE\nmore", None, "text") == "FIRST LINE"
