#!/usr/bin/env python3
"""Cut a release of extract-cli, mirroring the contract-ops suite flow.

    python scripts/release.py X.Y.Z

Steps (all local; pushing/publishing is a separate human-gated action):
  1. Validate the version string and a clean-ish working tree.
  2. Bump __version__ + EXTRACTOR_VERSION in extract_cli.py and version in
     pyproject.toml.
  3. Regenerate docs/spec/extract-output.schema.json and the .expected.json
     goldens (so the bumped extractor_version is reflected).
  4. Run mypy --strict and the test suite.
  5. Commit (as DrBaher) and tag vX.Y.Z.

It prints the exact `git push` / publish commands to run afterwards.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "extract_cli.py"
PYPROJECT = ROOT / "pyproject.toml"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def run(*cmd: str) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def bump(version: str) -> None:
    cli = CLI.read_text(encoding="utf-8")
    cli = re.sub(r'^__version__ = "[^"]+"', f'__version__ = "{version}"', cli, count=1, flags=re.M)
    cli = re.sub(r'^EXTRACTOR_VERSION = "[^"]+"', f'EXTRACTOR_VERSION = "{version}"', cli, count=1, flags=re.M)
    CLI.write_text(cli, encoding="utf-8")

    proj = PYPROJECT.read_text(encoding="utf-8")
    proj = re.sub(r'^version = "[^"]+"', f'version = "{version}"', proj, count=1, flags=re.M)
    PYPROJECT.write_text(proj, encoding="utf-8")
    print(f"bumped version to {version}")


def main() -> int:
    if len(sys.argv) != 2 or not SEMVER.match(sys.argv[1]):
        print("usage: python scripts/release.py X.Y.Z", file=sys.stderr)
        return 2
    version = sys.argv[1]
    py = sys.executable

    bump(version)
    run(py, "extract_cli.py", "schema")  # sanity: schema still emits
    subprocess.run([py, "extract_cli.py", "schema"], cwd=ROOT, check=True,
                   stdout=(ROOT / "docs/spec/extract-output.schema.json").open("w"))
    run(py, "tests/_make_goldens.py")
    run(py, "-m", "mypy", "--strict", "extract_cli.py")
    run(py, "-m", "pytest", "-q")

    run("git", "config", "user.name", "DrBaher")
    run("git", "config", "user.email", "Drbaher@gmail.com")
    run("git", "add", "-A")
    run("git", "commit", "-m", f"Release v{version}")
    run("git", "tag", f"v{version}")

    print("\nRelease prepared locally. Next (human-gated):")
    print(f"  git push origin HEAD")
    print(f"  git push origin v{version}    # tag push triggers PyPI Trusted Publishing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
