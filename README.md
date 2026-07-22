# ResolveOps

ResolveOps is a production-style AI workflow agent for synthetic AtlasFlow support cases. It
will investigate a bounded case, assemble cited evidence, apply deterministic policy, pause for
human approval before consequential synthetic actions, and resume safely from a durable
checkpoint. It is deliberately not a general-purpose chatbot.

This repository currently contains the foundation monorepo only. The bootstrap checks run
without cloud credentials, Docker, or external services.

## Prerequisites

- Node.js `22.22.3` (pinned in `.nvmrc`)
- pnpm `11.0.7` through Corepack (pinned in `package.json`)
- Python `3.12.3` (pinned in `.python-version`)
- uv `0.11.31`
- GNU Make

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
```

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

The empty package areas are intentional scaffolding. Contracts, migrations, datasets, synthetic
APIs, and workflow execution will be introduced in their dedicated follow-up issues.

## First tracer-bullet goal

The first end-to-end workflow will handle one deterministic duplicate-charge case: normalize a
synthetic ticket, collect allowlisted billing evidence, verify cited evidence, calculate an
account credit in deterministic code, persist an approval interrupt, resume after a reviewer
decision, execute the approved synthetic credit exactly once, and produce an audited response
draft. LangGraph will own workflow transitions and checkpoints; deterministic code will own
authorization, validation, policy, idempotency, and side effects.

See [the approved production workflow specification](docs/production_ai_workflow_agent_spec.md)
for the full product and architecture contract.
