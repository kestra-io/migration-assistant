.PHONY: check hooks

# Tests, ruff, mypy, the secret scan over the full history, and the boundary test.
check:
	@scripts/check.sh

# Run `make check` before every commit.
hooks:
	@printf '#!/bin/sh\nexec "$$(git rev-parse --show-toplevel)/scripts/check.sh"\n' > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "pre-commit hook installed: make check runs before every commit"
