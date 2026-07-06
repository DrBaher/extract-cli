"""Shared pytest fixtures and path setup for the extract-cli test suite."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Make the single-file CLI importable without installation.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests._fixtures_build import ensure_binary_fixtures  # noqa: E402

# Self-heal: regenerate any missing binary (.docx/.pdf) fixtures.
ensure_binary_fixtures()

# (fixture filename, expected detection tier, expected format)
CORPUS: Tuple[Tuple[str, str, str], ...] = (
    ("nda_h2.md", "h2", "markdown"),
    ("services_bold.txt", "bold-numbered", "text"),
    ("lease_allcaps.txt", "all-caps", "text"),
    ("employment_docx.docx", "bold-numbered", "docx"),
    ("heading_docx.docx", "h2", "docx"),
    ("numbered_docx.docx", "h2", "docx"),
    ("license_pdf.pdf", "all-caps", "pdf"),
    ("esigned_pdf.pdf", "all-caps", "pdf"),
    ("services_html.html", "numbered", "html"),
)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(params=[name for name, _tier, _fmt in CORPUS])
def corpus_doc(request: "pytest.FixtureRequest") -> Path:
    return FIXTURES / request.param


def all_fixture_names() -> List[str]:
    return [name for name, _tier, _fmt in CORPUS] + ["scanned.pdf"]
