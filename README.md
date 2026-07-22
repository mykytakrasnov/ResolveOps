# ResolveOps

ResolveOps is a production-style AI workflow agent for synthetic AtlasFlow support cases. It
will investigate a bounded case, assemble cited evidence, apply deterministic policy, pause for
human approval before consequential synthetic actions, and resume safely from a durable
checkpoint. It is deliberately not a general-purpose chatbot.

The repository includes shared workflow contracts, the initial PostgreSQL migrations, and
optional local PostgreSQL and MinIO services. The ordinary bootstrap checks still run without
cloud credentials, Docker, or external services.

## Prerequisites

- Node.js `22.22.3` (pinned in `.nvmrc`)
- pnpm `11.0.7` through Corepack (pinned in `package.json`)
- Python `3.12.3` (pinned in `.python-version`)
- uv `0.11.31`
- GNU Make
- Docker with Compose (only for local PostgreSQL and MinIO)

## Bootstrap locally

```bash
nvm use
corepack enable
make bootstrap
make check
```

`make bootstrap` installs the locked Node and Python development dependencies. `make check`
runs formatting verification, linting, strict type checking, and the current smoke test. None of
these commands require cloud credentials or running infrastructure.

Useful individual commands:

```bash
make format        # update Python and TypeScript formatting
make format-check  # verify formatting without changing files
make lint          # run Biome and Ruff
make typecheck     # run TypeScript and mypy
make test          # run the Python test suite
make contracts-generate # regenerate OpenAPI and TypeScript contracts
make contracts-check    # fail when generated contracts have drifted
```

## Shared contracts

Pydantic v2 is the source of truth in
`services/agent-api/src/resolveops/models/contracts.py`. It defines the bounded workflow inputs
and outputs, frontend-safe case/run/event/proposal/approval/artifact records, and the generic
typed tool envelope. Unknown input fields are rejected, tool names and event types are
allowlisted, and reviewer decisions are bound to a proposal hash.

Run `make contracts-generate` after changing a contract. It deterministically updates:

- `packages/contracts/openapi.json`, an OpenAPI 3.1 components document
- `packages/contracts/generated/index.ts`, strict TypeScript declarations

`make contracts-check`, the Python test suite, and CI all detect generated-contract drift.

## Local PostgreSQL and MinIO

Copy `.env.example` to `.env` only when you need to override local defaults. The checked-in
defaults are synthetic development credentials and must not be reused outside local development.

```bash
make infra-up       # PostgreSQL 16, private MinIO bucket, and MinIO console
make migrate        # upgrade PostgreSQL with DATABASE_URL_DIRECT
make infra-down     # stop services; named volumes retain local data
```

PostgreSQL listens on `localhost:5432`. MinIO's S3 endpoint listens on `localhost:9000`, and its
console listens on `localhost:9001`. The initialization container creates the private
`resolveops-local` bucket. Override ports, credentials, bucket, and connection strings with the
variables documented in `.env.example`.

`make migration-sql` renders the complete PostgreSQL upgrade offline and does not contact a
database. This is also how the unit suite validates migration startup without credentials.

## Repository layout

```text
apps/web/             React application and Cloudflare Worker boundary
services/agent-api/   FastAPI and LangGraph Python service boundary
packages/contracts/   Shared generated API contract boundary
data/                 Synthetic-data schemas, templates, and generated output
evals/                Evaluation runners, evaluators, and baselines
infra/                AWS and Cloudflare infrastructure definitions
scripts/              Repository automation
docs/                 Product, architecture, reliability, and security documentation
```

Dataset generation, synthetic system routes, and workflow run creation/execution remain
intentional scaffolding for their dedicated follow-up issues.

## First tracer-bullet goal

The first end-to-end workflow will handle one deterministic duplicate-charge case: normalize a
synthetic ticket, collect allowlisted billing evidence, verify cited evidence, calculate an
account credit in deterministic code, persist an approval interrupt, resume after a reviewer
decision, execute the approved synthetic credit exactly once, and produce an audited response
draft. LangGraph will own workflow transitions and checkpoints; deterministic code will own
authorization, validation, policy, idempotency, and side effects.

See [the approved production workflow specification](docs/production_ai_workflow_agent_spec.md)
for the full product and architecture contract.
