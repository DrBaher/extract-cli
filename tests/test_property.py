"""Property-based invariants using stdlib random.Random(seed) -- no hypothesis.

Generates a spread of synthetic and adversarial documents and asserts the
output contract holds for all of them.
"""
from __future__ import annotations

import random
import string
from typing import List, Tuple

import extract_cli as ex
from tests._schema_validator import validate

SCHEMA = ex.output_schema()
CANON = list(ex.CANONICAL_CLAUSE_ALIASES.keys())
COMPANIES = ["Acme Inc", "Globex LLC", "Initech Corp.", "Umbrella Co",
             "Stark Industries", "Wayne Enterprises", "Cyberdyne Systems"]


def _make_doc(rng: random.Random) -> Tuple[str, List[str]]:
    a, b = rng.sample(COMPANIES, 2)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    year = rng.randint(2018, 2026)
    titles = rng.sample(CANON, rng.randint(1, 6))
    parts = [
        "# Synthetic Agreement",
        f"This Agreement is made by and between {a} and {b} as of "
        f"{year:04d}-{month:02d}-{day:02d}.",
    ]
    for t in titles:
        parts.append(f"## {t}")
        parts.append(f"This section concerns {t.lower()} and its obligations.")
    return "\n\n".join(parts), titles


def _check_invariants(result: dict, text: str) -> None:
    # 1. Always validates against the published schema.
    assert validate(result, SCHEMA) == []
    # 2. _meta sane; deterministic tier never claims llm.
    assert result["_meta"]["llm_used"] is False
    assert result["_meta"]["tiers_used"] == ["deterministic"]
    # 3. Confidence in [0,1] and source in the allowed set, everywhere.
    for f in (result["governing_law"], result["value"],
              result["dates"]["effective"], result["dates"]["expiration"],
              result["term"]["length"], result["term"]["auto_renew"],
              result["term"]["notice_period_days"]):
        assert 0.0 <= f["confidence"] <= 1.0
        assert f["source"] in ("deterministic", "llm", "none")
    # 4. Clause spans valid, ordered, in-bounds.
    prev = -1
    for c in result["clauses"]:
        s, e = c["span"]["start"], c["span"]["end"]
        assert 0 <= s < e <= len(text)
        assert s > prev
        prev = s


def test_synthetic_docs_hold_invariants() -> None:
    rng = random.Random(1234)
    for _ in range(200):
        text, titles = _make_doc(rng)
        result = ex.build_extraction(text, text.encode("utf-8"), "markdown", "doc.md")
        _check_invariants(result, text)
        # Canonical titles we planted should come back mapped.
        got = {c["canonical_title"] for c in result["clauses"]}
        for t in titles:
            assert t in got


def test_extraction_is_deterministic() -> None:
    rng = random.Random(99)
    for _ in range(50):
        text, _ = _make_doc(rng)
        raw = text.encode("utf-8")
        r1 = ex.build_extraction(text, raw, "markdown", "d.md")
        r2 = ex.build_extraction(text, raw, "markdown", "d.md")
        assert r1 == r2


def test_random_garbage_never_crashes() -> None:
    rng = random.Random(7)
    alphabet = string.printable + "“”’§€£\n\n## "
    for _ in range(300):
        n = rng.randint(0, 400)
        text = "".join(rng.choice(alphabet) for _ in range(n))
        result = ex.build_extraction(text, text.encode("utf-8"), "text", "g.txt")
        # Even on garbage the schema contract must hold.
        assert validate(result, SCHEMA) == []
        for c in result["clauses"]:
            assert 0 <= c["span"]["start"] < c["span"]["end"] <= len(text)


def test_large_document_is_bounded() -> None:
    """A big (but legal-sized) document extracts quickly and within memory --
    the resource bounds keep the worst case sane."""
    import time
    import tracemalloc
    doc = "".join(f"## Provision {i}\n\n{'Some body text about obligations. ' * 40}\n\n"
                  for i in range(1500))
    tracemalloc.start()
    t0 = time.perf_counter()
    result = ex.build_extraction(doc, doc.encode("utf-8"), "markdown", "big.md")
    elapsed = time.perf_counter() - t0
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert elapsed < 10.0          # generous; real is well under a second
    assert peak < 300 * 1024 * 1024
    assert result["clauses"]


def test_random_bytes_through_readers(tmp_path) -> None:
    """Readers must degrade gracefully (never raise) on random bytes that only
    *look* like a .pdf/.docx by extension."""
    rng = random.Random(55)
    for ext in (".pdf", ".docx", ".txt", ".md"):
        for _ in range(20):
            blob = bytes(rng.randint(0, 255) for _ in range(rng.randint(0, 300)))
            p = tmp_path / f"x{ext}"
            p.write_bytes(blob)
            raw, text, fmt, _warnings = ex.load_source(p)
            result = ex.build_extraction(text, raw, fmt, str(p))
            assert validate(result, SCHEMA) == []
