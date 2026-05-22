# CLAUDE.md

Working notes for developing **extract-cli** (the codebase). For *driving* the
CLI as an agent, see [AGENTS.md](AGENTS.md); for the design, see
[ARCHITECTURE.md](ARCHITECTURE.md).

## What this is

The open-loop front door of the contract-ops CLI suite: ingest any contract
(`.md`/`.txt`/`.html`/`.docx`/`.pdf`) and emit structured JSON (parties, dates,
term, governing law, a canonical-vocabulary clause map, defined terms, value).
All logic lives in one file, **`extract_cli.py`**.

## Hard rules (don't break these)

- **Stdlib-only core.** `extract_cli.py` imports nothing outside the standard
  library. Heavy parsing lives behind the `[docx]`/`[pdf]` extras and must
  degrade gracefully when absent. `dependencies = []` stays empty.
- **LLM is opt-in only** (`--llm`), never in a default code path. The
  deterministic tier must stand alone.
- **stdout = machine JSON, stderr = humans** (`--why`, warnings, errors). Never
  mix them. Output is ASCII-safe (`ensure_ascii=True`) and locale-safe.
- **The output JSON is a cross-CLI contract.** Adding a field is a minor bump;
  renaming/removing/narrowing is a major bump (see docs/INTEROP.md).
- **Commit identity is `DrBaher <Drbaher@gmail.com>`** — set it before
  committing; `scripts/release.py` does this for you.

## Layout

| Path | What |
|---|---|
| `extract_cli.py` | the entire CLI (readers, clause cascade, extractors, LLM tier, schema, argparse) |
| `docs/spec/extract-output.schema.json` | the published output schema (generated from `output_schema()`) |
| `tests/` | per-tier unit tests, seeded property tests, schema-conformance, fixture corpus + goldens |
| `tests/_fixtures_build.py` | stdlib generators for the binary (`.docx`/`.pdf`) fixtures (deterministic) |
| `AGENTS.md` / `llms.txt` | agent-facing usage contract + machine summary |

## Clause detection (the differentiator)

`detect_clauses()` is a first-match-wins cascade of tiers: `h2` (`## Heading`,
also emitted by the DOCX reader for Word heading-styles **and** `w:numPr`
paragraphs) → `bold-numbered` → `numbered` (`1. Title`, `Section 3. …`, and
two-line `ARTICLE N` + title) → `all-caps`. Detected titles are normalized onto
the suite vocabulary by `_canonicalize_clause()` via `CANONICAL_CLAUSE_ALIASES`.
Unmapped clauses are kept with `mapped: false` — consumers wanting only suite
clauses filter `mapped == true`. With `--llm`, an LLM fallback fills the clause
map when the cascade finds nothing.

## Dev loop

```bash
make install      # editable install + dev extra
make typecheck    # mypy --strict (must be clean)
make test         # full suite
make coverage     # suite + coverage report
make spec-check   # docs/spec schema must equal `extract schema`
make smoke        # build wheel + run it in a clean venv
```

## When you change…

- **the output schema** (`output_schema()`): run `make spec-update` then
  `make goldens`, and review the diff. Bump per semver above.
- **extraction logic**: run `make goldens` to refresh `tests/fixtures/*.expected.json`
  and review the diff (the goldens are pinned to the stdlib readers via
  `prefer_optional=False`, so they're stable regardless of installed extras).
- **a binary fixture**: `make fixtures` (deterministic — fixed zip timestamp).
- **commands/flags**: the `extract --catalog json` discovery output is asserted
  against the real argparse parser by a test, so keep them in sync.

## Releasing

`make release VERSION=X.Y.Z` (bumps `__version__`/`EXTRACTOR_VERSION`/pyproject,
regenerates spec + goldens, runs mypy + tests, commits + tags as DrBaher). Then
push `main` and the `vX.Y.Z` tag; `publish.yml` publishes to PyPI on the tag via
Trusted Publishing.
