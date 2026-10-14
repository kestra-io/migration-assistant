.PHONY: check hooks dist image

# Tests, ruff, mypy, the secret scan over the full history, and the boundary test.
check:
	@scripts/check.sh

# Run `make check` before every commit.
hooks:
	@printf '#!/bin/sh\nexec "$$(git rev-parse --show-toplevel)/scripts/check.sh"\n' > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "pre-commit hook installed: make check runs before every commit"

# The wheel and the source package in dist/, checked, with the image's pinned runtime requirements next to them.
dist:
	@rm -rf dist
	@uv build --quiet -o dist
	@uv run --frozen python scripts/check_dist.py dist
	@uv export --quiet --frozen --no-dev --no-emit-project --extra anthropic -o dist/requirements.txt

# The container image, for this machine's architecture, from dist/.
image: dist
	@docker build --build-arg VERSION="$$(uv run --frozen python -c 'import kestra_migration_assistant as k; print(k.__version__)')" -t migration-assistant:dev .
