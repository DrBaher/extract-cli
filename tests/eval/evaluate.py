"""Accuracy benchmark for extract-cli.

Runs the deterministic extractor over a small corpus of real, filled contracts
(see corpus/ + ATTRIBUTION.md) and scores it against hand-labeled ground truth
(gold.json) -- precision/recall/F1 per field. Line coverage says the code runs;
this says how *correct* the extraction is.

    python tests/eval/evaluate.py          # print the report
    make eval                              # same, via the Makefile

`run_eval()` returns the per-field metrics dict so the test suite can gate on it.
Stdlib-only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import extract_cli as ex  # noqa: E402


def _norm(s: str) -> str:
    """Loose normalization for entity/jurisdiction comparison."""
    s = re.sub(r"\s+", " ", s).strip().lower().rstrip(".,")
    s = re.sub(r"[“”\"']", "", s)
    return s


def _party_match(gold: str, got: str) -> bool:
    g, h = _norm(gold), _norm(got)
    return g == h or g in h or h in g


def _set_pr(gold: List[str], got: List[str], matcher: Any = None) -> Tuple[int, int, int]:
    """Return (true_positives, false_positives, false_negatives)."""
    matcher = matcher or (lambda a, b: _norm(a) == _norm(b))
    tp = sum(1 for g in gold if any(matcher(g, h) for h in got))
    fn = len(gold) - tp
    fp = sum(1 for h in got if not any(matcher(g, h) for g in gold))
    return tp, fp, fn


def _gov_match(gold: str, got: Any) -> bool:
    if not isinstance(got, str):
        return False
    return _norm(gold) in _norm(got) or _norm(got) in _norm(gold)


def run_eval() -> Dict[str, Dict[str, float]]:
    gold = json.loads((EVAL_DIR / "gold.json").read_text(encoding="utf-8"))
    # tp/fp/fn accumulators per field
    acc: Dict[str, List[int]] = {k: [0, 0, 0] for k in
                                 ("parties", "clauses")}
    scalar: Dict[str, List[int]] = {k: [0, 0] for k in  # [correct, total]
                                    ("effective_date", "governing_law", "jurisdiction")}
    for name, g in gold.items():
        if name.startswith("_"):
            continue
        raw, text, fmt, _w = ex.load_source(EVAL_DIR / "corpus" / name)
        r = ex.build_extraction(text, raw, fmt, name)

        # parties: set precision/recall
        got_parties = [p["name"] for p in r["parties"]]
        tp, fp, fn = _set_pr(g["parties"], got_parties, _party_match)
        acc["parties"] = [a + b for a, b in zip(acc["parties"], (tp, fp, fn))]

        # clauses: recall over the verified must-contain set (gold isn't exhaustive,
        # so we don't penalize extra real clauses as false positives).
        got_clauses = [c["canonical_title"] for c in r["clauses"] if c["mapped"]]
        tp, _fp, fn = _set_pr(g.get("clauses_present", []), got_clauses)
        acc["clauses"] = [acc["clauses"][0] + tp, acc["clauses"][1], acc["clauses"][2] + fn]

        # scalar fields: correct iff matches gold
        scalar["effective_date"][1] += 1
        if r["dates"]["effective"]["value"] == g["effective_date"]:
            scalar["effective_date"][0] += 1
        scalar["governing_law"][1] += 1
        if _gov_match(g["governing_law"], r["governing_law"]["value"]):
            scalar["governing_law"][0] += 1
        scalar["jurisdiction"][1] += 1
        if r["jurisdiction"]["value"] == g["jurisdiction"]:
            scalar["jurisdiction"][0] += 1

    out: Dict[str, Dict[str, float]] = {}
    for field, (tp, fp, fn) in acc.items():
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        out[field] = {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)}
    for field, (correct, total) in scalar.items():
        acc_v = correct / total if total else 0.0
        out[field] = {"accuracy": round(acc_v, 3), "n": float(total)}
    return out


def _fmt(metrics: Dict[str, float]) -> str:
    if "f1" in metrics:
        return f"P={metrics['precision']:.2f}  R={metrics['recall']:.2f}  F1={metrics['f1']:.2f}"
    return f"accuracy={metrics['accuracy']:.2f}  (n={int(metrics['n'])})"


def main() -> int:
    report = run_eval()
    print("extract-cli accuracy benchmark (deterministic tier, no LLM)")
    print("=" * 62)
    for field, metrics in report.items():
        print(f"  {field:16} {_fmt(metrics)}")
    print("=" * 62)
    print("Corpus: tests/eval/corpus/ (6 real filled contracts, .txt + .html).")
    print("Ground truth hand-verified against sources; see tests/eval/gold.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
