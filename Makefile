PYTHON_PROJECT := services/agent-api

.PHONY: bootstrap check format format-check lint typecheck test

bootstrap:
	pnpm install --frozen-lockfile
	uv sync --directory $(PYTHON_PROJECT) --frozen

format:
	pnpm format
	uv run --directory $(PYTHON_PROJECT) ruff format src tests

format-check:
	pnpm format:check
	uv run --directory $(PYTHON_PROJECT) ruff format --check src tests

lint:
	pnpm lint
	uv run --directory $(PYTHON_PROJECT) ruff check src tests

typecheck:
	pnpm typecheck
	uv run --directory $(PYTHON_PROJECT) mypy src tests

test:
	uv run --directory $(PYTHON_PROJECT) pytest

check: format-check lint typecheck test
