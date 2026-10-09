#!/bin/sh
# The one check: run before every merge, and before every commit as the pre-commit hook (make hooks).
# Tests, lint, types, the secret scan over the full history and what is staged, and the boundary test.
set -eu
cd "$(dirname "$0")/.."

step() { printf '\n== %s\n' "$1"; }

step "tests"
uv run --frozen pytest -q -p no:cacheprovider
step "boundary (nothing in kestra_migration_assistant imports or names the closed half)"
uv run --frozen pytest -q -p no:cacheprovider tests/test_boundary.py
step "ruff"
uv run --frozen ruff check
step "mypy"
uv run --frozen mypy
step "secret scan: every commit of every ref"
uv run --frozen python tools/secret_scan.py --history
step "secret scan: what is staged"
uv run --frozen python tools/secret_scan.py --staged

printf '\nmake check: all passed\n'
