"""The module __version__ must match the pyproject.toml version.

These two drifted apart once (__version__ stuck at 0.1.15 while the package
shipped 0.1.16), making `--version` and `--catalog json` under-report. Pin them
together so a release that bumps one but not the other fails CI.
"""
from __future__ import annotations

import re

from conftest import REPO_ROOT

import extract_cli as ex


def test_version_matches_pyproject() -> None:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert m is not None, "could not find version in pyproject.toml"
    assert m.group(1) == ex.__version__, (
        f"pyproject version {m.group(1)!r} != extract_cli.__version__ {ex.__version__!r}"
    )
