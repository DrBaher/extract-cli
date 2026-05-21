# Changelog

All notable changes to `extract-cli` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres
to [Semantic Versioning](https://semver.org/). Per the suite convention
(see [`docs/INTEROP.md`](docs/INTEROP.md)), **backward-incompatible changes to
the output schema require a major version bump**; new optional fields are minor.

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

[0.1.1]: https://github.com/DrBaher/extract-cli/releases/tag/v0.1.1
[0.1.0]: https://github.com/DrBaher/extract-cli/releases/tag/v0.1.0
