"""Regenerate the .expected.json golden files for the fixture corpus.

Goldens use the fixture *basename* as document.source_path so they're stable
regardless of the working directory. Run via ``make goldens`` after an
intentional change to the extraction logic, then review the diff.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import extract_cli  # noqa: E402
from tests._fixtures_build import ensure_binary_fixtures  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

DOCS = ["nda_h2.md", "services_bold.txt", "lease_allcaps.txt",
        "employment_docx.docx", "heading_docx.docx", "numbered_docx.docx",
        "license_pdf.pdf", "esigned_pdf.pdf", "services_html.html",
        "scanned.pdf"]


def golden_for(name: str) -> dict:
    path = FIXTURES / name
    # Pin to the stdlib readers so goldens are reproducible regardless of which
    # optional extras (python-docx / pypdf) happen to be installed.
    raw, text, fmt, _warnings = extract_cli.load_source(path, prefer_optional=False)
    return extract_cli.build_extraction(text, raw, fmt, name)


def main() -> None:
    ensure_binary_fixtures()
    for name in DOCS:
        result = golden_for(name)
        out = FIXTURES / f"{name}.expected.json"
        out.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
