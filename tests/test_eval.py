"""Gate the accuracy benchmark (tests/eval/) so extraction quality can't silently
regress. Floors are conservative -- the point is a tripwire, not a tight target;
`make eval` prints the full report."""
from __future__ import annotations

from tests.eval.evaluate import run_eval

# Conservative per-field floors. Current (deterministic, no LLM): parties F1≈.96,
# effective_date/governing_law/jurisdiction = 1.0, clause recall ≈ .45 (HTML
# heading detection is the known weak spot). Floors sit well below current so a
# real regression trips them without day-to-day flakiness.
_FLOORS = {
    ("parties", "recall"): 0.80,
    ("parties", "precision"): 0.80,
    ("clauses", "recall"): 0.30,
    ("effective_date", "accuracy"): 0.80,
    ("governing_law", "accuracy"): 0.80,
    ("jurisdiction", "accuracy"): 0.80,
}


def test_accuracy_benchmark_meets_floors() -> None:
    report = run_eval()
    for (field, metric), floor in _FLOORS.items():
        got = report[field][metric]
        assert got >= floor, f"{field}.{metric} = {got} < floor {floor} (accuracy regression?)"
