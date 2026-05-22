# Benchmark corpus — sources & licensing

The accuracy benchmark (`tests/eval/`) scores extract-cli against a small set of
**real, executed contracts** filed publicly with the U.S. Securities and
Exchange Commission (SEC EDGAR). SEC filings are public records; these exhibits
are reproduced here, unmodified, solely as a regression/accuracy test fixture.

| File | Source (SEC EDGAR) |
|---|---|
| `emp_celsci.txt` | CEL-SCI Corporation — Exhibit 10(ooo), employment agreement |
| `msa_kpmg.txt` | Blade Internet Ventures / KPMG Consulting — master services agreement |
| `services_visteon.txt` | Visteon Corporation — salaried employee lease agreement |
| `consulting_mtm.htm` | MTM Technologies — consulting agreement |
| `emp_arcp.htm` | American Realty Capital Properties — employment agreement |
| `emp_quadgraphics.htm` | Quad/Graphics, Inc. — employment agreement |

Ground truth (`gold.json`) was hand-verified against each document's text — the
parties, effective date, governing law, normalized jurisdiction, and a
verified subset of section headings. It is intentionally independent of what the
extractor currently produces.
