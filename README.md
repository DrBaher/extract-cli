# extract-cli

> Part of the contract-ops CLI suite. **extract-cli** is the suite's
> *passport control* — the **open-loop front door**. The rest of the suite is a
> closed loop that only handles documents it authored from its own templates;
> `extract-cli` ingests **any** document (yours or a counterparty's foreign
> paper) and emits a structured representation the pipeline can consume:
> [**template-vault-cli**](https://github.com/DrBaher/template-vault-CLI) (storage) feeds
> [**draft-cli**](https://github.com/DrBaher/draft-cli) (fill placeholders) →
> [**nda-review-cli**](https://github.com/DrBaher/nda-review-cli) (review, redline, negotiate) →
> [**docx2pdf-cli**](https://github.com/DrBaher/docx2pdf-cli) (DOCX → PDF) →
> [**sign-cli**](https://github.com/DrBaher/sign-cli) (signing + audit).
> Cross-version drift detection via [**compare-cli**](https://github.com/DrBaher/compare-cli).
> [Showcase site](https://cli.drbaher.com/).
>
> `extract-cli` sits **upstream of review**: it turns foreign paper into the
> suite's canonical, structured vocabulary. Its output is a **cross-CLI data
> contract** — see [`docs/INTEROP.md`](docs/INTEROP.md) and
> [`docs/spec/extract-output.schema.json`](docs/spec/extract-output.schema.json).

```
ingest (extract) → review → diff → convert → sign
   ^you are here
```

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

## Commands

```bash
extract <path>            # parse a document → structured JSON on stdout (default)
extract schema            # print the output JSON Schema (the cross-CLI contract)
extract fields            # list extractable fields and their tier
extract demo              # run on a bundled fixture and show the narrative
extract completion bash   # emit a shell-completion script (bash|zsh)
```

### Flags

| Flag | Meaning |
|---|---|
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
  "clauses":    [ { "canonical_title": "Confidentiality", "detected_title": "## Confidentiality Obligations", "tier": "h2", "span": {"start": 0, "end": 120}, "confidence": 0.95, "source": "deterministic", "mapped": true } ],
  "defined_terms": [ { "term": "Confidential Information", "confidence": 0.6, "source": "deterministic" } ],
  "value":      { "value": "$50,000", "confidence": 0.6, "source": "deterministic" },
  "_meta":      { "extractor_version": "0.1.0", "tiers_used": ["deterministic"], "llm_used": false }
}
```

## The clause map (the differentiator)

A counterparty's "SECTION 7. NON-DISCLOSURE" and your template's
"## Confidentiality" are the same clause. `extract-cli` reuses
template-vault-cli's **clause-detection cascade** (Tier 1 `## H2` headings →
Tier 2 bold-numbered `**1. …**` → Tier 3 ALL-CAPS lines) and a built-in
**canonical alias vocabulary** to normalize foreign clause titles onto the
names the rest of the suite already speaks. Clauses it can't map are kept with
`mapped: false` (and a `*` in the table view) so nothing is silently dropped.

```bash
extract counterparty.pdf | jq '.clauses[] | {canonical_title, detected_title, mapped}'
```

## Composability — piping into the rest of the suite

`extract-cli` is built to be the first stage of a Unix pipe. Its JSON is the
contract every downstream tool reads.

```bash
# 1) Foreign NDA → review. extract normalizes clauses; nda-review runs policy.
extract counterparty_nda.pdf | nda-review review --from-extract -

# 2) Pull just the clause map and feed compare-cli to diff a foreign doc
#    against your canonical template's structure.
extract their_msa.docx --fields clauses | compare-cli align --stdin \
  --against msa/standard

# 3) Archive structured metadata for any inbound paper into the post-signature
#    vault, keyed by content hash.
extract signed_contract.pdf | contract-vault put --from-extract - \
  --id "$(extract signed_contract.pdf | jq -r .document.sha256)"

# 4) Triage a folder of inbound contracts: list governing law + parties.
for f in inbox/*.pdf; do
  extract "$f" --fields parties,governing_law --no-confidence \
    | jq -c '{file: input_filename, gov: .governing_law, parties: [.parties[].name]}'
done

# 5) Gate a workflow on extraction confidence.
extract draft.docx | jq -e '.clauses | all(.confidence > 0.7)' && echo "ok to review"
```

> The `--from-extract`/`--stdin` flags above are the consumption points the
> sibling CLIs expose (or are adopting) for this contract; see
> [`docs/INTEROP.md`](docs/INTEROP.md) for the shared conventions and the
> versioning commitment on the schema.

## LLM configuration (opt-in)

`--llm` reads a shared suite config, in this order:

1. `~/.config/contract-ops/llm.json`  (suite-wide — preferred)
2. `./config/llm.json`                 (repo-local override)

Copy [`config/llm.json.example`](config/llm.json.example) to one of those
paths. Configure it once and every suite tool that adopts the same lookup gets
LLM features for free. Without it, `--llm` just warns and returns the
deterministic output.

## Development

```bash
make install      # editable install with the [dev] extra
make test         # full suite
make coverage     # suite + coverage report
make typecheck    # mypy --strict
make build        # wheel + sdist
make smoke        # build, install the wheel in a clean venv, run it
make spec-check   # assert docs/spec schema == `extract schema`
make release VERSION=X.Y.Z
```

See [ARCHITECTURE.md](ARCHITECTURE.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
