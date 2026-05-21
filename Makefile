# extract-cli -- developer tasks. Stdlib-only project; these wrap the toolchain.
PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip
SPEC   := docs/spec/extract-output.schema.json

.PHONY: help install test test-quick coverage typecheck build smoke \
        spec-check fixtures goldens release clean

help:
	@echo "extract-cli make targets:"
	@echo "  install     editable install with [dev] extra"
	@echo "  test        run the full test suite"
	@echo "  test-quick  run tests, stop on first failure (-x), skip slow property runs"
	@echo "  coverage    run tests under coverage and print a report"
	@echo "  typecheck   mypy --strict"
	@echo "  build       build wheel + sdist into dist/"
	@echo "  smoke       build, install the wheel in a clean venv, and run it"
	@echo "  spec-check  assert docs/spec schema == 'extract schema' output"
	@echo "  fixtures    regenerate binary (.docx/.pdf) test fixtures"
	@echo "  goldens     regenerate .expected.json golden files"
	@echo "  release     make release VERSION=X.Y.Z"
	@echo "  clean       remove build/test artifacts"

install:
	$(PIP) install -e ".[dev]"

test:
	$(PYTHON) -m pytest

test-quick:
	$(PYTHON) -m pytest -x -q -k "not property"

coverage:
	$(PYTHON) -m coverage run --source=extract_cli -m pytest -q
	$(PYTHON) -m coverage report -m

typecheck:
	$(PYTHON) -m mypy --strict extract_cli.py

build: clean
	$(PYTHON) -m build

smoke: build
	@set -e; \
	tmp=$$(mktemp -d); \
	echo "smoke venv: $$tmp"; \
	$(PYTHON) -m venv $$tmp/venv; \
	whl=$$(ls dist/*.whl | head -1); \
	$$tmp/venv/bin/python -m pip install -q --upgrade pip; \
	$$tmp/venv/bin/python -m pip install -q "$$whl"; \
	echo "--- extract --version ---"; $$tmp/venv/bin/extract --version; \
	echo "--- extract demo (validate against spec) ---"; \
	$$tmp/venv/bin/extract demo --no-color | $(PYTHON) scripts/validate_against_spec.py; \
	echo "smoke OK"; \
	rm -rf $$tmp

spec-check:
	@$(PYTHON) extract_cli.py schema > /tmp/extract-spec-check.json
	@if diff -q /tmp/extract-spec-check.json $(SPEC) >/dev/null 2>&1; then \
		echo "spec-check OK: $(SPEC) matches 'extract schema'"; \
	else \
		echo "spec-check FAILED: $(SPEC) is stale. Run: make spec-update"; \
		diff $(SPEC) /tmp/extract-spec-check.json || true; \
		exit 1; \
	fi

.PHONY: spec-update
spec-update:
	$(PYTHON) extract_cli.py schema > $(SPEC)
	@echo "wrote $(SPEC)"

fixtures:
	$(PYTHON) tests/_fixtures_build.py

goldens:
	$(PYTHON) tests/_make_goldens.py

release:
	@test -n "$(VERSION)" || { echo "usage: make release VERSION=X.Y.Z"; exit 2; }
	$(PYTHON) scripts/release.py $(VERSION)

clean:
	rm -rf dist build *.egg-info .pytest_cache .mypy_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
