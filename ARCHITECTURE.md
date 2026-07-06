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

`detect_clauses(text)` extends template-vault-cli's clause cascade; the first
tier that fires wins so fallbacks never shadow real structure:

1. **`h2`** — `## Heading` (Markdown-native; also what the DOCX reader emits for
   Word heading styles / `w:numPr` paragraphs). Needs ≥ 1 match.
2. **`bold-numbered`** — `**1. Purpose**`, `**Section 4. Term**` (typical of
   DOCX → text). Needs ≥ 2 matches.
3. **`numbered`** — plain `1. Term`, `Section 3. Payment`, and two-line
   `ARTICLE N` + title (the dominant format in foreign paper), gated by a
   title-case heuristic. Needs ≥ 2 matches.
4. **`all-caps`** — blank-line-framed `CONFIDENTIALITY` lines (typical of legal
   PDFs), with the single-token-≥-4-letters rule. Needs ≥ 2 matches.

(Plus an opt-in **`llm`** clause-map fallback under `--llm` when none of the
above fire — see the LLM tier below.) After detection, running headers/footers
and front/back-matter are filtered (`_is_noise_clause_title` + repeat dedup).

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
[CHANGELOG.md](CHANGELOG.md).

The stdlib PDF reader parses the real object graph (`_pdf_structured_text`):
the xref chain (classic tables, PDF 1.5+ cross-reference streams with PNG
predictors, hybrid `/XRefStm`), objects packed in compressed `/ObjStm` object
streams, the page tree, and per-font `/ToUnicode` CMaps — so text stored as
CID glyph codes in hex strings (Word, HexaPDF/SignWell, DocuSign, qpdf output)
decodes correctly. A legacy inflate-every-stream scanner remains as the
fallback for undecodable structures. When no text comes out, the reader says
*why* on stderr — genuinely scanned/image-only (no text operators + page
images), undecodable font encoding, encrypted, or unparseable structure — and
the run exits `1` (a "finding"), never a crash.

## LLM tier

Opt-in only (`--llm`), never in a hot path. `load_llm_config()` reads the
suite-shared config (`~/.config/contract-ops/llm.json` then `./config/llm.json`).
`_llm_request` posts via stdlib `urllib` to Anthropic or an OpenAI-compatible
endpoint. Any failure (no config, network error, unparseable JSON) is caught:
a warning to stderr, deterministic output untouched. The LLM only *adds* fuzzy
fields (`term.renewal_mechanics`, `obligations`) and fills `governing_law` only
when the deterministic tier found nothing — it never overwrites a deterministic
value. As a **clause-map fallback**, when the deterministic cascade returned no
clauses the LLM is asked for the section headings (the clause keys are added to
the prompt only then); the titles are normalized through the same
`_canonicalize_clause` vocabulary, located in the text for a best-effort span,
and emitted with `tier: "llm"` / `source: "llm"`. This covers DOCX that
auto-number with no heading style (their numbers live only in `numbering.xml`).

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
