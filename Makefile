PYTHON_PROJECT := services/agent-api
PYTHON_SRC := $(CURDIR)/$(PYTHON_PROJECT)/src
LOCAL_DATABASE_URL := postgresql+psycopg://resolveops:resolveops@localhost:5432/resolveops

.PHONY: bootstrap check contracts-check contracts-generate format format-check generate-data infra-down infra-up lint migrate migration-sql synthetic-data-generate test typecheck

bootstrap:
	pnpm install --frozen-lockfile
	uv sync --directory $(PYTHON_PROJECT) --frozen

format:
	pnpm format
	uv run --directory $(PYTHON_PROJECT) ruff format src tests migrations ../../scripts

format-check:
	pnpm format:check
	uv run --directory $(PYTHON_PROJECT) ruff format --check src tests migrations ../../scripts

lint:
	pnpm lint
	uv run --directory $(PYTHON_PROJECT) ruff check src tests migrations ../../scripts

typecheck:
	pnpm typecheck
	uv run --directory $(PYTHON_PROJECT) mypy src tests

test:
	uv run --directory $(PYTHON_PROJECT) pytest

contracts-generate:
	PYTHONPATH=$(PYTHON_SRC) uv run --project $(PYTHON_PROJECT) python -m resolveops.models.contract_generation --repository-root $(CURDIR)

contracts-check:
	PYTHONPATH=$(PYTHON_SRC) uv run --project $(PYTHON_PROJECT) python -m resolveops.models.contract_generation --check --repository-root $(CURDIR)

generate-data:
	uv run --project $(PYTHON_PROJECT) python scripts/generate_synthetic_data.py

synthetic-data-generate: generate-data

infra-up:
	docker compose up -d postgres minio minio-init

infra-down:
	docker compose down

migrate:
	DATABASE_URL_DIRECT=$${DATABASE_URL_DIRECT:-$(LOCAL_DATABASE_URL)} uv run --project $(PYTHON_PROJECT) alembic -c $(PYTHON_PROJECT)/alembic.ini upgrade head

migration-sql:
	uv run --project $(PYTHON_PROJECT) alembic -c $(PYTHON_PROJECT)/alembic.ini upgrade head --sql

check: format-check lint typecheck contracts-check test
