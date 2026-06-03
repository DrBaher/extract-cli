# Cross-CLI interop contract

This document registers what `extract-cli` **provides** to the rest of the
contract-ops CLI suite, and records the suite conventions it **adopts**. It is
the citation point so sibling repos
([template-vault-cli](https://github.com/DrBaher/template-vault-CLI),
[draft-cli](https://github.com/DrBaher/draft-cli),
[nda-review-cli](https://github.com/DrBaher/nda-review-cli),
[compare-cli](https://github.com/DrBaher/compare-cli),
[docx2pdf-cli](https://github.com/DrBaher/docx2pdf-cli),
[sign-cli](https://github.com/DrBaher/sign-cli)) can link here once instead of
reverse-engineering this repo's output shape.

The suite is a **convention family**, not a code family: each CLI is
implemented independently and stdlib-only / minimal-deps. What's shared is
(1) the **data-contract schemas** at the boundaries, (2) the **UX conventions**
for flags/streams, and (3) the **one actually-shared file**, the LLM provider
config. There is no shared library, by design. The authoritative suite playbook
lives in
[`template-vault-cli/docs/INTEROP.md`](https://github.com/DrBaher/template-vault-CLI/blob/HEAD/docs/INTEROP.md);
this document conforms to it.

## Where extract-cli sits

`extract-cli` is the suite's **open-loop front door**. The rest of the suite is
a closed loop that only handles documents it authored from its own templates;
`extract-cli` ingests **any** document and emits a structured representation
the loop can consume. It is upstream of review:

```
ingest (extract-cli) → review (nda-review-cli) → diff (compare-cli) → convert (docx2pdf-cli) → sign (sign-cli)
```

with `template-vault-cli` as the storage layer behind drafting. `extract-cli`
and `compare-cli` are the document-structure tools that share the clause model.

## Schema this repo ships

Under [`spec/`](spec/), JSON Schema 2020-12.

| File | What | Stable since |
|---|---|---|
| [`extract-output.schema.json`](spec/extract-output.schema.json) | `extract <path>` (and `extract demo`) default JSON output | v0.1.0 |

`extract schema` prints this schema; the committed file is asserted identical
to that output by the test suite and by `make spec-check`. Downstream consumers
(`nda-review-cli`, `compare-cli`, `contract-vault`) can validate against it
instead of trusting field shapes by convention — `scripts/validate_against_spec.py`
is a self-contained reference validator.

### The contract, in one paragraph

Top-level keys: `document` {title, format, sha256, source_path}, `parties[]`,
`dates` {effective, expiration}, `term` {length, auto_renew,
notice_period_days, *renewal_mechanics?*}, `governing_law`, `jurisdiction`
(normalized code, e.g. `US-DE`), `clauses[]` {canonical_title, detected_title,
tier, span, confidence, source, mapped}, `defined_terms[]`, `value`,
`amounts[]` (all monetary amounts), `signatories[]` {name, title}, *`obligations[]?`*,
and `_meta` {extractor_version, tiers_used, llm_used}. Formats: markdown, text,
html, docx, pdf. **Every extracted field carries a `confidence` (0–1) and
a `source` ∈ {deterministic, llm, none}.** Scalar fields use the envelope
`{value, confidence, source}`; "not found" is `{value: null, confidence: 0.0,
source: "none"}`. Italic fields are added only under `--llm`.

### Versioning commitment

Per the suite rule: a backward-incompatible change to this schema (renaming or
removing a field, narrowing a type) requires a **major** version bump of this
CLI. New optional fields are **minor** additions. Consumers should ignore
unknown fields and treat any field as "verify, not trust" using its
`confidence`/`source`.

## The clause model (shared with compare-cli / template-vault-cli)

`extract-cli` reuses template-vault-cli's clause-detection cascade and
`clause_aliases` model so a foreign document's clauses land on the **same
canonical vocabulary** the rest of the suite speaks:

- Detection tiers, first-match-wins: `h2` (`## Heading`) → `bold-numbered`
  (`**1. …**`) → `all-caps` (blank-line-framed shouting). Roman numerals 1–39
  are stripped from titles (longer alternatives first).
- `clause_aliases` shape is `{canonical_title: [alias, …]}`, identical to
  template-vault's `meta.json` field. template-vault stores it per-template;
  `extract-cli` ships a built-in **default** vocabulary (`CANONICAL_CLAUSE_ALIASES`)
  because foreign paper carries no `meta.json`. Each output clause reports its
  `detected_title`, the mapped `canonical_title`, whether it `mapped`, and the
  detection `tier`.

`compare-cli` can align a foreign document's `clauses[]` against a canonical
template's structure; `nda-review-cli` can run clause-keyed policy against the
normalized titles.

## Shared LLM config

`extract-cli` adopts the suite-wide LLM config lookup order (LLM is opt-in via
`--llm`):

```
~/.config/contract-ops/llm.json        # suite-wide (or $XDG_CONFIG_HOME/contract-ops/llm.json)
```

The config is read only from this fixed user config dir, never from the current
working directory.

Schema (matches [`config/llm.json.example`](../config/llm.json.example)):

```json
{
  "provider": "anthropic | openai",
  "model":    "claude-sonnet-4-6 | gpt-4o-mini | ...",
  "api_key":  "sk-...",
  "base_url": "https://api.example/v1   (openai-compatible only)"
}
```

A user who configures `~/.config/contract-ops/llm.json` once gets working LLM
features across every suite tool that adopts this order. The enrichment uses
only stdlib `urllib`, so there is no runtime dependency.

## UX conventions adopted

| Concern | Convention |
|---|---|
| Primary result | **stdout** (JSON payload, default) |
| Discovery | `extract --catalog json` (commands/flags, the suite contract) + `extract schema` / `extract fields --json` |
| `--why`, warnings, errors | **stderr** |
| `--why` envelope | plain-text `[why] <header>` block (as in template-vault-cli / draft-cli) |
| Quiet | `-q` / `--silent` / `--quiet` aliases |
| Color | auto-detect TTY; honor `NO_COLOR` and `FORCE_COLOR` (https://no-color.org/) |
| Version | `-V` / `--version` → `extract-cli X.Y.Z` |
| Demo | `extract demo` zero-config first experience |
| Completion | hidden `__complete` subcommand + `extract completion {bash,zsh}` |
| Exit codes | `0` success, `1` finding (low-signal document), `2` bad usage |

## What this repo does NOT ship

- The four-tier clause-detection rule's canonical spec — lives in
  [`compare-cli/docs/clause-detection.md`](https://github.com/DrBaher/compare-cli/blob/main/docs/clause-detection.md).
  This repo ports the implementation and the `clause_aliases` model from
  template-vault-cli.
- The `nda-review-cli` policy schema — lives in that repo.

When the cross-cutting specs grow, they should move to a neutral
`drbaher/contract-ops-specs` repo, as noted in the suite playbook.
