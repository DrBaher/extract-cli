# extract-cli

> **Ingest any contract — yours or a counterparty's foreign paper — and get
> structured JSON.** Hand `extract-cli` a `.md` / `.txt` / `.html` / `.docx` /
> `.pdf` and it returns the parties, dates, term, governing law, a normalized
> **clause map**, defined terms, and a headline value — each with a `confidence`
> and a `source`, so you **verify, don't trust**. Stdlib-only, single-file, local-first.
>
> Works standalone — and also composes with the [contract-ops CLI suite](https://cli.drbaher.com/)
> as its **open-loop front door**: it turns foreign paper into the suite's canonical,
> structured vocabulary that [**nda-review-cli**](https://github.com/DrBaher/nda-review-cli),
> [**compare-cli**](https://github.com/DrBaher/compare-cli), and
> [**contract-vault**](https://github.com/DrBaher/contract-vault-cli) can consume. Its
> output is a **cross-CLI data contract** — see [`docs/INTEROP.md`](docs/INTEROP.md) and
> [`docs/spec/extract-output.schema.json`](docs/spec/extract-output.schema.json).

```
ingest (extract) → review → diff → convert → sign
   ^you are here
```

## Run this

```bash
pipx run extract-cli demo        # zero-config: extract a bundled NDA → structured JSON
# or, installed:  pip install extract-cli && extract demo
```

That prints the full output contract — parties, dates, term, governing law, and
a clause map normalized onto the suite's canonical vocabulary — for a bundled
fixture, with no setup and no network. Point it at your own file with
`extract path/to/contract.docx`.

## Where to go next

- **New here?** Keep reading — [What it does](#what-it-does) and
  [The two extraction tiers](#the-two-extraction-tiers).
- **Driving it from an agent?** See [`AGENTS.md`](AGENTS.md) and call
  `extract --catalog json` at startup to discover commands/flags. The output
  shape is locked by [`docs/spec/extract-output.schema.json`](docs/spec/extract-output.schema.json).
- **Wiring it into the pipeline?** See [`docs/INTEROP.md`](docs/INTEROP.md) — the
  contract is the output schema + the shared clause vocabulary.
- **Contributing / building a sibling CLI?** [`CONTRIBUTING.md`](CONTRIBUTING.md)
  and [ARCHITECTURE.md](ARCHITECTURE.md).

## What it does

Give it a contract in **`.md` / `.txt` / `.html`** (native), **`.docx`**, or
**`.pdf`**, and it returns structured JSON: the parties, dates, term, governing law, a
**clause map** normalized onto the suite's canonical clause vocabulary, a
defined-term inventory, and a headline value. Every field carries a
`confidence` and a `source` so downstream tools **verify, don't trust**.

It is **stdlib-only**, single-file, terminal-first, and composable. No DB, no
daemon, no network in the default path.

## Install

```bash
pip install extract-cli                 # core: .md/.txt/.html + best-effort .docx/.pdf
pip install "extract-cli[docx]"         # higher-fidelity .docx (python-docx)
pip install "extract-cli[pdf]"          # higher-fidelity .pdf (pypdf)
pip install "extract-cli[docx,pdf]"     # both
```

The core has **zero runtime dependencies** and is fully functional on
`.md`/`.txt`/`.html` with no extras (HTML is also auto-detected when it hides
inside a `.txt`, e.g. SEC EDGAR filings). `.docx` and `.pdf` work out of the box via stdlib readers; the
`[docx]`/`[pdf]` extras improve fidelity on complex documents (see
[ARCHITECTURE.md](ARCHITECTURE.md)).

## The two extraction tiers

`extract-cli` is explicit about *how* it knows each field — encoded in every
field's `source` and in `_meta.tiers_used`.

| Tier | When | Fields | Network? |
|---|---|---|---|
| **deterministic** | always on (default) | parties, dates, defined terms, **clause map**, governing law, best-effort term/notice/value | none |
| **llm** | opt-in via `--llm` only | renewal mechanics, obligation phrasing, ambiguous governing law | yes (your provider) |

The deterministic core is **fully useful without the LLM**. The LLM tier is
opt-in, never in a hot path, and gated behind an explicit flag and a config
file — if no config is present, `--llm` degrades gracefully with a warning and
you still get the full deterministic output.

**Clause-map fallback.** Some documents (e.g. `.docx` that auto-number clauses
via Word's numbering with no heading style) carry no signal the deterministic
cascade can see, so its clause map comes back empty. When `--llm` is set *and*
no clauses were detected, the LLM is asked for the section headings; the result
is normalized through the same canonical vocabulary and emitted with
`tier: "llm"`, `source: "llm"`, and a modest confidence (verify, not trust).
When the deterministic cascade already found clauses, the LLM is not consulted
for them.

## Commands

```bash
extract <path>            # parse a document → structured JSON on stdout (default)
extract --catalog json    # machine-readable catalog of commands/flags (agents call at startup)
extract schema            # print the output JSON Schema (the cross-CLI contract)
extract fields            # list extractable fields and their tier
extract demo              # run on a bundled fixture and show the narrative
extract completion bash   # emit a shell-completion script (bash|zsh)
```

### Flags

| Flag | Meaning |
|---|---|
| `--catalog json` | Print the machine-readable command/flag catalog and exit (the suite discovery contract; agents call this at startup) |
| `--llm` | Opt-in LLM enrichment of fuzzy fields (off by default) |
| `--fields a,b,c` | Emit only a subset of top-level fields (e.g. `parties,clauses`) |
| `--format json\|table` | Output format (default `json`) |
| `--no-confidence` | Omit confidence/source markers (reduced convenience view) |
| `--json` | Force JSON to stdout (the default) |
| `--why` | Rationale block on **stderr** |
| `-q`, `--silent`, `--quiet` | Suppress non-error diagnostics |
| `--no-color` | Disable ANSI color (also honors `NO_COLOR` / `FORCE_COLOR`) |
| `-V`, `--version` | Print `extract-cli X.Y.Z` |

Streams follow the suite convention: **stdout** is the machine payload (JSON),
**stderr** is for humans (`--why`, warnings, errors). Exit codes: `0` success,
`1` low-signal document (e.g. a scanned/empty PDF), `2` bad usage.

## Output shape (abridged)

```jsonc
{
  "document":   { "title": "...", "format": "markdown", "sha256": "…", "source_path": "nda.md" },
  "parties":    [ { "name": "Acme Robotics, Inc.", "role": "Disclosing Party", "confidence": 0.9, "source": "deterministic" } ],
  "dates":      { "effective": { "value": "2024-03-01", "confidence": 0.85, "source": "deterministic" }, "expiration": { "value": null, "confidence": 0.0, "source": "none" } },
  "term":       { "length": { "value": "3 years", ... }, "auto_renew": { "value": true, ... }, "notice_period_days": { "value": 60, ... } },
  "governing_law": { "value": "State of Delaware", "confidence": 0.85, "source": "deterministic" },
  "jurisdiction": { "value": "US-DE", "confidence": 0.8, "source": "deterministic" },
  "clauses":    [ { "canonical_title": "Confidentiality", "detected_title": "## Confidentiality Obligations", "tier": "h2", "span": {"start": 0, "end": 120}, "confidence": 0.95, "source": "deterministic", "mapped": true } ],
  "defined_terms": [ { "term": "Confidential Information", "confidence": 0.6, "source": "deterministic" } ],
  "value":      { "value": "$50,000", "confidence": 0.6, "source": "deterministic" },
  "amounts":    [ { "value": "$50,000", "confidence": 0.6, "source": "deterministic" } ],
  "signatories": [ { "name": "Jane Doe", "title": "CEO", "confidence": 0.55, "source": "deterministic" } ],
  "_meta":      { "extractor_version": "0.1.11", "tiers_used": ["deterministic"], "llm_used": false }
}
```

## The clause map (the differentiator)

A counterparty's "SECTION 7. NON-DISCLOSURE" and your template's
"## Confidentiality" are the same clause. `extract-cli` extends
template-vault-cli's **clause-detection cascade** — `## H2` headings →
bold-numbered `**1. …**` → plain numbered (`1. Term`, `Section 3. …`, two-line
`ARTICLE N`) → ALL-CAPS lines (and an opt-in `--llm` fallback) — plus a built-in
**canonical alias vocabulary** to normalize foreign clause titles onto the
names the rest of the suite already speaks. Clauses it can't map are kept with
`mapped: false` (and a `*` in the table view) so nothing is silently dropped.

```bash
extract counterparty.pdf | jq '.clauses[] | {canonical_title, detected_title, mapped}'
```

## Composability — piping into the rest of the suite

`extract-cli` is built to be the first stage of a Unix pipe. The glue is its
**stdout JSON + standard tools** (`jq`, `comm`) and the **shared clause
vocabulary** — `extract`'s `canonical_title` values are the same names
`template-vault-cli` detects and `nda-review-cli` keys policy on, so a foreign
document's clauses line up with the suite's with no bespoke adapter. Every
example below is runnable today (verified against the real sibling CLIs).

```bash
# 1) Inspect any contract's structure (.md/.txt/.html/.docx/.pdf, one tool).
extract counterparty.docx | jq '{parties: [.parties[].name],
  governing_law: .governing_law.value, clauses: [.clauses[].canonical_title]}'

# 2) Clause-coverage gap vs your canonical template in template-vault-cli.
#    extract normalizes the counterparty's *foreign* headings onto the same
#    clause vocabulary template-vault detects, so a plain `comm` diffs them.
template-vault info nda/mutual-standard --json | jq -r '.clauses[].title' | sort > ours.txt
extract counterparty_nda.docx | jq -r '.clauses[].canonical_title' | sort -u > theirs.txt
comm -23 ours.txt theirs.txt    # clauses in OUR standard that THEY are missing
comm -13 ours.txt theirs.txt    # clauses THEY added that we don't have

# 3) Intake: extract for structure, nda-review-cli for a policy verdict on the
#    same foreign doc; merge both views with jq.
extract counterparty_nda.docx > extract.json
nda-review review --file counterparty_nda.docx --playbook output/nda_playbook.json \
  --out-json review.json
jq -n --slurpfile e extract.json --slurpfile r review.json \
  '{parties: [$e[0].parties[].name], governing_law: $e[0].governing_law.value,
    clauses: ($e[0].clauses | length), decision: $r[0].decision, risk: $r[0].risk_score}'

# 4) Triage a folder of inbound contracts: governing law + parties per file.
for f in inbox/*; do
  extract "$f" --fields parties,governing_law --no-confidence \
    | jq -c --arg f "$f" '{file: $f, gov: .governing_law, parties: [.parties[].name]}'
done

# 5) Gate a workflow on extraction confidence (non-zero exit if any clause is shaky).
extract draft.docx | jq -e '.clauses | all(.confidence > 0.7)' && echo "ok to review"
```

> The integration contract is the **output schema** and the **canonical clause
> vocabulary**, not per-tool flags. See [`docs/INTEROP.md`](docs/INTEROP.md) for
> the shared conventions and the schema's versioning commitment.

## LLM configuration (opt-in)

`--llm` reads a shared suite config from a fixed user config location:

- `~/.config/contract-ops/llm.json`  (or `$XDG_CONFIG_HOME/contract-ops/llm.json`)

Copy [`config/llm.json.example`](config/llm.json.example) to that path.
Configure it once and every suite tool that adopts the same lookup gets LLM
features for free. The config is never read from the current working directory,
so running the CLI in an untrusted directory cannot inject an endpoint/api_key.
Without it, `--llm` just warns and returns the deterministic output.

## Accuracy

Line coverage tells you the code runs; it doesn't tell you the extraction is
*correct*. `make eval` scores the deterministic tier against a small corpus of
**real, executed contracts** (SEC EDGAR filings) with hand-verified ground truth
([`tests/eval/`](tests/eval/)), reporting precision/recall per field:

| Field | Score |
|---|---|
| parties | P 1.00 · R 0.92 · F1 0.96 |
| effective date | accuracy 1.00 |
| governing law | accuracy 1.00 |
| jurisdiction (normalized) | accuracy 1.00 |
| clauses (recall on verified sections) | 0.86 |

Clause recall improved sharply once the HTML reader learned to treat
emphasis (heading tags, <b>/<u>, CSS font-weight/underline) as section
headings; the residual misses are compound/combined heading titles. A test (`tests/test_eval.py`) gates these so
accuracy can't silently regress.

## Development

```bash
make install      # editable install with the [dev] extra
make test         # full suite
make coverage     # suite + coverage report (installs extras; fails under 100%)
make typecheck    # mypy --strict
make eval         # accuracy benchmark vs the labeled corpus
make build        # wheel + sdist
make smoke        # build, install the wheel in a clean venv, run it
make spec-check   # assert docs/spec schema == `extract schema`
make release VERSION=X.Y.Z
```

See [ARCHITECTURE.md](ARCHITECTURE.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
