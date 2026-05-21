# Changelog

All notable changes to `extract-cli` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres
to [Semantic Versioning](https://semver.org/). Per the suite convention
(see [`docs/INTEROP.md`](docs/INTEROP.md)), **backward-incompatible changes to
the output schema require a major version bump**; new optional fields are minor.

## [0.1.6] - 2026-05-21

### Docs
- **Rewrote the README composability section to verified, runnable examples.**
  Testing extract-cli against the real sibling CLIs (`template-vault-cli`,
  `nda-review-cli`) showed the previous pipes were aspirational — the siblings
  expose no `--from-extract`/`--stdin` flag (`nda-review review` takes
  `--file`/`--text`; `template-vault` reads its own vault). The integration
  contract is the **output schema + the shared canonical clause vocabulary**,
  glued by stdout JSON and standard tools (`jq`, `comm`): `extract`'s
  `canonical_title` values are the same names template-vault detects and
  nda-review keys policy on, so a foreign document's clauses line up with the
  suite's with no bespoke adapter. New examples cover clause-coverage gap
  analysis against a vault template and a combined extract+nda-review intake
  report — all runnable today. (Also fixed a broken `jq input_filename` in the
  folder-triage example.) No code or schema change.

## [0.1.5] - 2026-05-21

### Added
- **LLM clause-map fallback** (opt-in, `--llm` only). When the deterministic
  cascade detects no clauses — e.g. a `.docx` that auto-numbers via Word's
  numbering with no heading style, the limitation noted in 0.1.4 — the LLM is
  asked for the section headings (the clause request is added to the prompt
  only in that case). Returned titles are normalized through the same canonical
  vocabulary as the deterministic path, located in the document for a
  best-effort span, and emitted with `tier: "llm"`, `source: "llm"`, and a
  modest confidence. The LLM is never consulted for clauses the deterministic
  cascade already found, and the deterministic core remains fully useful with
  no LLM. No schema change (the clause `tier`/`source` enums already allow
  `llm`).

## [0.1.4] - 2026-05-21

DOCX clause detection, driven by testing against 20 real `.docx` contracts
(Common Paper / Bonterms / YC templates via open-agreements, plus government
samples) — the format we expect most.

### Fixed
- **The DOCX reader now honors Word heading styles.** Real Word contracts carry
  their clause structure in `Heading1`–`Heading9`/`Title` paragraph styles with
  *auto-generated* numbers (absent from the raw text), so the prior cascade
  found almost no clauses. Heading-styled paragraphs are now emitted as `##`
  headings (detected by the strongest tier); run-in headings
  (`Payment.  Customer will pay …`) are split into title + body, and a full
  sentence that merely carries a heading style is rejected (not a clause).
  Across the 20-doc sample this took heading-styled agreements from ~0 clauses
  to a clean 14–21 distinct suite-vocabulary clauses each.
- Binary DOCX test fixtures are now generated deterministically (fixed zip
  timestamp) so their sha256 — and the goldens — are stable across regenerations.

### Known limitations (documented)
- DOCX that auto-number clauses via `numbering.xml` with **no heading style and
  no bold lead** (some Bonterms/older templates use a flat `Plain`/`ListParagraph`
  style) still yield no clause map: the heading text carries no detectable
  signal without reconstructing Word's numbering counters. Parties/dates/
  governing-law still extract.

## [0.1.3] - 2026-05-21

Clause-map de-noising and party cleanup, driven by testing against 10 more
contracts (SEC EDGAR credit, loan, employment, lease, asset-purchase, and
consulting HTML exhibits; Apache PDFs).

### Fixed
- **Clause map drops structural noise** common in dense real documents:
  a heading whose title repeats 3+ times is treated as a running header/footer
  (one lease's `Ks 112708-2` page code went from 44 "clauses" to 0), and
  front/back-matter (`Table of Contents`, `Exhibit B`, `Schedule 2.1`) and
  document codes/page numbers (4+ consecutive digits) are filtered out.
- **Party-name cleanup** extended: trailing `together with …`, `, as
  administrative agent`, and a dangling unclosed parenthetical
  (`(each of them being`) are trimmed.

### Notes
- On dense documents the deterministic clause map can still surface a few
  non-clause headings (e.g. address lines in a notices block); consumers
  wanting only suite-vocabulary clauses should filter on `mapped == true`,
  which isolates the real clauses (the noise is always `mapped == false`).
- Known best-effort edge cases on varied real paper: a bare role word as a
  party name ("Landlord"), and a middle-initial period truncating a personal
  name ("John C." → "John C"). Best-effort fields carry confidence/source.

## [0.1.2] - 2026-05-21

More real-world hardening, driven by testing against five additional contracts
(SEC EDGAR consulting/MSA, lease, and Visteon services agreements; Common Paper
and Perigon Cloud Service Agreements).

### Added
- **HTML input** (`.html`/`.htm`, and HTML auto-detected inside `.txt` such as
  SEC EDGAR full submissions). Stdlib `html.parser`-based reader strips
  script/style, frames block elements so heading detection still works, and
  unescapes entities. `document.format` enum gains `html` (backward-compatible
  widening). This turns the large class of HTML contracts (SEC exhibits, web
  ToS) from garbage into structured output.

### Fixed
- **Field extraction now runs on whitespace-flattened text**, so values that
  wrap across a line break are matched whole — e.g. governing law
  `the laws of the Province\nof Ontario` now yields `Province of Ontario`, and
  line-wrapped party names/defined terms are captured.
- **Party extraction** (continues issue #2): names are trimmed of trailing
  descriptors (`, a Delaware corporation`, `doing business as …`,
  `having its offices at …`, `as of …`), and each party must begin with a
  capital so an `and` *inside* a party's own description no longer splits the
  parties (`…V6E 3S7 and doing business as …` → real parties recovered).

### Known limitations (documented, not bugs)
- The stdlib PDF reader cannot decode PDFs that use embedded subset fonts with
  hex-encoded glyph strings (common in professionally-typeset PDFs); these
  degrade gracefully to a low-signal warning. Install the `[pdf]` extra (pypdf)
  for them — verified to recover full text and clause structure.
- Two-line `ARTICLE N` / title headings (number on one line, title on the next)
  are not yet detected.

## [0.1.1] - 2026-05-21

Real-world hardening, driven by testing against a SEC EDGAR employment
agreement and the Common Paper Mutual NDA (PDF/DOCX).

### Added
- **`numbered` clause-detection tier** for plain numbered headings
  (`1. Termination`, `Section 3. Payment`, `Article IV. …`) — the dominant
  format in foreign paper, missed by the H2/bold/ALL-CAPS tiers. A title-case
  heuristic rejects numbered sentences and list items. The output schema's
  clause `tier` enum gains `numbered` (a backward-compatible widening).

### Fixed
- **PDF reader** now extracts text only from inside `BT … ET` text objects, so
  embedded fonts, digital-signature blobs, and metadata streams no longer leak
  binary noise (a real signed PDF dropped from ~188 KB of garbage to ~8.7 KB of
  clean text). Added a printable-ratio backstop.
- **Effective date**: anchor on `(the "Effective Date")` and a bare
  `as of <date>` cue; handle dates that wrap across a line break.
- **Term length**: require a real number, dropping false positives such as
  `…consecutive days`.
- **Title**: skip SGML/XML wrapper lines (e.g. SEC EDGAR `<DOCUMENT>` headers).
- Strip trailing punctuation from clause titles (`Other Benefits.` →
  `Other Benefits`).

## [0.1.0] - 2026-05-21

Initial release — the open-loop front door of the contract-ops CLI suite.

### Added
- Single-file, stdlib-only CLI `extract_cli.py` (`extract` entry point).
- `extract <path>` — parse `.md`/`.txt`/`.docx`/`.pdf` into structured JSON.
- `extract schema` / `fields` / `demo` / `completion` subcommands; hidden
  `__complete` handler for shell completion.
- **Two explicit extraction tiers**: a deterministic, network-free default
  (parties, dates, defined terms, clause map, governing law, best-effort
  term/notice/value) and an opt-in `--llm` tier for fuzzy fields. Every field
  carries a `confidence` and a `source` ∈ {deterministic, llm, none}.
- **Clause map**: ported template-vault-cli's three-tier clause-detection
  cascade (H2 → bold-numbered → ALL-CAPS, Roman numerals 1–39 with longer
  alternatives first) plus a built-in canonical clause-alias vocabulary that
  normalizes a foreign document's clause titles onto the suite's shared names.
- Cross-CLI output contract published as JSON Schema 2020-12 at
  [`docs/spec/extract-output.schema.json`](docs/spec/extract-output.schema.json)
  and registered in [`docs/INTEROP.md`](docs/INTEROP.md).
- Suite UX conventions: `--json`/`--why`/`-q`/`--silent`/`--no-color`
  (`NO_COLOR`/`FORCE_COLOR` honored)/`--demo`/`-V`; meaningful exit codes
  (0/1/2); locale-safe UTF-8 stdout/stderr; ASCII-safe output.
- Shared LLM config lookup (`~/.config/contract-ops/llm.json` → `./config/llm.json`)
  with `config/llm.json.example`.
- Test suite: per-tier unit tests, seeded property-based invariants (stdlib
  `random.Random`, no hypothesis), a real-contract fixture corpus spanning all
  input formats and clause tiers with `.expected.json` goldens, and a
  schema-conformance test using a dependency-free JSON Schema validator.
- CI matrix (Ubuntu × macOS × Python 3.9–3.12) + typecheck + build-smoke jobs;
  PyPI Trusted Publishing workflow on `v*` tags. `Makefile` and
  `scripts/release.py`.

### Decisions (documented per the autonomous-build playbook)
- **`.docx`/`.pdf` work without their extras.** The playbook says heavy parsing
  lives behind `[docx]`/`[pdf]`; we honor the spirit (extras enhance fidelity)
  while degrading *up*, not down: stdlib `zipfile`/XML reads `.docx` and a
  stdlib `zlib`/text-operator reader reads `.pdf` out of the box. The extras
  (`python-docx`, `pypdf`) are preferred when installed. Rationale: the
  playbook's hard rule is "fully functional on `.md`/`.txt` with zero extras
  and degrade gracefully" — a stdlib best-effort reader satisfies that *and*
  the graceful-degradation rule better than refusing the format outright.
- **`[llm]` extra is empty.** LLM enrichment uses only stdlib `urllib`, so no
  runtime dependency is required; the extra exists for suite parity.
- **Schema validation in tests uses a vendored mini-validator**, not
  `jsonschema`, to keep the dev surface stdlib-aligned (dev extra is just
  pytest/coverage/mypy/build).
- **`--no-confidence`** produces a reduced convenience projection that is
  intentionally *not* governed by the output schema (the schema describes the
  full default output).

[0.1.6]: https://github.com/DrBaher/extract-cli/releases/tag/v0.1.6
[0.1.5]: https://github.com/DrBaher/extract-cli/releases/tag/v0.1.5
[0.1.4]: https://github.com/DrBaher/extract-cli/releases/tag/v0.1.4
[0.1.3]: https://github.com/DrBaher/extract-cli/releases/tag/v0.1.3
[0.1.2]: https://github.com/DrBaher/extract-cli/releases/tag/v0.1.2
[0.1.1]: https://github.com/DrBaher/extract-cli/releases/tag/v0.1.1
[0.1.0]: https://github.com/DrBaher/extract-cli/releases/tag/v0.1.0
