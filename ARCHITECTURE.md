# Architecture

`extract-cli` is one file, `extract_cli.py`, stdlib-only. This document is the
map.

## Pipeline

```
load_source(path)                       extension/content sniff → reader
  ├─ .md/.txt  → utf-8 decode
  ├─ .html     → stdlib html.parser reader (also auto-detected inside .txt)
  ├─ .docx     → python-docx (if [docx]) else stdlib zipfile/XML reader
  └─ .pdf      → pypdf (if [pdf]) else stdlib zlib + text-operator reader
        │
        ▼  (raw_bytes, text, format, warnings)
build_extraction(text, raw, fmt, src)   the DETERMINISTIC tier (always on)
  │  field extractors run on a whitespace-FLATTENED copy (so values that wrap
  │  across a line are matched whole); clause detection keeps the original text
  ├─ extract_parties          "between X and Y", with role parentheticals
  ├─ extract_dates            effective / expiration, ISO-normalized
  ├─ extract_term             length / auto_renew / notice_period_days
  ├─ extract_governing_law    "governed by the laws of …"
  ├─ extract_clauses          detect_clauses cascade → canonical mapping
  ├─ extract_defined_terms    quoted / parenthetical Capitalized terms
  └─ extract_value            headline monetary amount
        │
        ▼  result : dict (the output contract)
llm_enrich(result, text, args)          the LLM tier — only if --llm
        │
        ▼
render_json | render_table              stdout (JSON is the machine payload)
```

Each extracted scalar is wrapped by `_field(value, confidence, source)` into a
`{value, confidence, source}` envelope; "not found" is the canonical
`{value: null, confidence: 0.0, source: "none"}`. Lists (`parties`, `clauses`,
`defined_terms`) carry per-item `confidence`/`source`. `_meta` records the
extractor version, the tiers that ran, and whether the LLM was used. This is
the "verify, not trust" contract downstream tools consume.

## The clause map

`detect_clauses(text)` is a faithful port of template-vault-cli's three-tier
cascade; the first tier that fires wins so fallbacks never shadow real
structure:

1. **`h2`** — `## Heading` (Markdown-native). Needs ≥ 1 match.
2. **`bold-numbered`** — `**1. Purpose**`, `**Section 4. Term**` (typical of
   DOCX → text). Needs ≥ 2 matches.
3. **`all-caps`** — blank-line-framed `CONFIDENTIALITY` lines (typical of legal
   PDFs), with the single-token-≥-4-letters rule. Needs ≥ 2 matches.

`_strip_clause_number` removes leading numbering, including Roman numerals
1–39 (`_ROMAN_RE` lists longer alternatives first so the engine doesn't
short-circuit on a prefix — bare `V`/`X` match).

`_canonicalize_clause` then maps each detected title onto the suite's shared
vocabulary via `CANONICAL_CLAUSE_ALIASES` (canonical_title → [alias, …]):
exact normalized match first, then a containment fallback. Unmapped clauses are
kept with `mapped: false` and a lower confidence so nothing is silently
dropped. template-vault stores this map *per template*; a foreign document has
none, so extract-cli ships a built-in default — that's the differentiator.

## Readers: degrade up, not down

The playbook gates heavy parsing behind `[docx]`/`[pdf]`. We honor the spirit
(extras improve fidelity) while keeping `.docx`/`.pdf` working with zero extras
via stdlib readers, because the hard rule is "fully functional with zero
extras + degrade gracefully" — and a best-effort reader serves that better than
refusing the format. See the decision note in
[CHANGELOG.md](CHANGELOG.md). A scanned/image-only PDF yields no text → a
stderr warning and exit code `1` (a "finding"), never a crash.

## LLM tier

Opt-in only (`--llm`), never in a hot path. `load_llm_config()` reads the
suite-shared config (`~/.config/contract-ops/llm.json` then `./config/llm.json`).
`_llm_request` posts via stdlib `urllib` to Anthropic or an OpenAI-compatible
endpoint. Any failure (no config, network error, unparseable JSON) is caught:
a warning to stderr, deterministic output untouched. The LLM only *adds* fuzzy
fields (`term.renewal_mechanics`, `obligations`) and fills `governing_law` only
when the deterministic tier found nothing — it never overwrites a deterministic
value.

## The output contract

`output_schema()` is the single source of truth for the JSON Schema. `extract
schema` prints it; `docs/spec/extract-output.schema.json` is the committed copy
(`make spec-check` asserts they're identical). Tests validate every fixture's
output against it with a vendored, dependency-free validator
(`tests/_schema_validator.py`).

## Conventions

UTF-8 stdout/stderr is forced in `main()` (locale-safe on macOS CI). Color
auto-detects a TTY and honors `NO_COLOR`/`FORCE_COLOR`. stdout is reserved for
the machine payload; `--why`, warnings, and errors go to stderr. Exit codes:
`0` success, `1` low-signal document, `2` bad usage / user-actionable error.
