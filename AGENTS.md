# Agents

Drive `extract-cli` from an LLM agent or non-interactive client. Same agent
contract as the rest of the contract-ops suite: a stable machine-readable
catalog, JSON on stdout, humans on stderr, and a small documented exit-code set.

`extract-cli` is the suite's **open-loop front door**: hand it any contract
(`.md` / `.txt` / `.html` / `.docx` / `.pdf`, yours or a counterparty's) and it
returns structured JSON the rest of the pipeline can consume. Every field
carries a `confidence` and a `source` — **verify, don't trust**.

## Output contract

- **Success**: a single JSON object to **stdout**, exit `0`. This is the machine
  payload; it's the default (no `--json` needed, though `--json` forces it).
- Every extracted scalar is the envelope `{value, confidence, source}`;
  "not found" is the canonical `{value: null, confidence: 0.0, source: "none"}`.
  Lists (`parties`, `clauses`, `defined_terms`) carry per-item
  `confidence`/`source`. `source ∈ {deterministic, llm, none}`.
- `_meta` records `extractor_version`, `tiers_used`, and `llm_used`.
- The output shape is locked by a JSON Schema —
  [`docs/spec/extract-output.schema.json`](docs/spec/extract-output.schema.json),
  also printed by `extract schema`. Validate against it instead of trusting
  field shapes by convention. (Note: the `--no-confidence` projection is a
  reduced convenience view, **not** governed by the schema.)
- **stderr** is for humans only: `--why` rationale, warnings, and errors.
  stdout stays clean JSON even under `--why`.
- **Failure**: a one-line `error: <message>` on **stderr**, non-zero exit.
  The error shape is a flat string (the suite is not uniform on error-object
  shape) — **branch on the exit code, never on the human-readable message.**

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success. |
| `1` | Low-signal document — no high-signal fields (parties/clauses/dates) could be extracted; e.g. a scanned/image-only or empty file. A **finding**, not a crash: valid JSON is still emitted on stdout. |
| `2` | Bad usage / user-actionable error (unreadable path, bad flag value, unsupported completion shell). |

## Discovery

Never hardcode command or flag names — call the catalog at startup:

```bash
extract --catalog json    # {name, bin, version, description, commands[], exitCodes}
```

`--catalog json` is the suite-wide discovery contract (parallel to
`nda-review-cli --catalog json`, `docx2pdf --catalog json`,
`sign --catalog json`). It is **complete, accurate, and stable across minor
versions** — a test asserts it never drifts from the real parser.

Tool-specific discovery extras:

```bash
extract schema            # the output JSON Schema (the cross-CLI data contract)
extract fields            # extractable fields and the tier that produces each
extract fields --json     # ...as JSON
extract demo              # run on a bundled fixture (zero-config first run)
extract --version
```

## Failure → recovery

| Symptom | Diagnose | Recover |
|---|---|---|
| Exit `1`, warning "no high-signal fields" | The document is likely scanned/image-only or has no recognizable structure. JSON is still emitted. | OCR the source first, or feed a text/`.docx`/`.md` version. The empty-but-valid JSON is safe to pass downstream. |
| Exit `2`, `error: ...` | `extract --catalog json` (or `extract <cmd> --help`) for the real surface. | Fix the path/flag and retry. |
| `clauses: []` on a real contract | The `.docx` likely auto-numbers via Word's numbering with no heading style (its numbers live only in `numbering.xml`), so the deterministic cascade sees no headings. | Re-run with `--llm` (opt-in): when no clauses are detected, the LLM is asked for section headings, normalized through the same canonical vocabulary and emitted with `tier: "llm"`, `source: "llm"`, and a modest confidence. Requires `~/.config/contract-ops/llm.json`. |
| Low-fidelity `.docx`/`.pdf` text | The stdlib best-effort reader ran (no extras installed). | `pip install "extract-cli[docx]"` and/or `"extract-cli[pdf]"` for higher fidelity. The core always works without them. |
| `--llm` only printed a warning | No LLM config found. | Copy [`config/llm.json.example`](config/llm.json.example) to `~/.config/contract-ops/llm.json`. Without it, deterministic output is still returned in full. |

## Recommended usage

```bash
# Inspect any contract's structure, one tool for five formats.
extract counterparty.docx | jq '{parties: [.parties[].name],
  governing_law: .governing_law.value, clauses: [.clauses[].canonical_title]}'

# Gate a workflow on extraction confidence (non-zero exit if any clause is shaky).
extract draft.docx | jq -e '.clauses | all(.confidence > 0.7)' && echo ok
```

The integration contract is the **output schema** + the **shared canonical
clause vocabulary** (`canonical_title` values match what `template-vault-cli`
detects and `nda-review-cli` keys policy on) — not per-tool flags. See
[`docs/INTEROP.md`](docs/INTEROP.md).
