# Contributing

ResolveOps is a production-style AI workflow system for synthetic support operations. Changes
should preserve the bounded workflow shape in the approved spec and keep deterministic application
code in charge of safety-critical behavior.

Before starting a change, read:

- `docs/production_ai_workflow_agent_spec.md`
- `docs/development-standards.md`
- The GitHub issue body and comments

## Local Commands

Use the pinned Node version before Node tooling:

```bash
source /home/redbeb/.nvm/nvm.sh && nvm use 22.22.3 >/dev/null
```

Run the full project gate before committing when practical:

```bash
make check
```

Regenerate contracts after changing Pydantic API/contract models:

```bash
make contracts-generate
make contracts-check
```

## Collaboration

- Keep work scoped to the assigned issue and its acceptance criteria.
- Treat `ready-for-human` issues as collaborative UI/product decisions; do not lock shadcn/ui
  component choices without review.
- Do not introduce real customer data, real side effects, or non-example domains.
- Do not expose chain-of-thought, secrets, session cookies, authorization headers, raw uploads, or
  unnecessary synthetic PII in logs, traces, public events, or replays.
- Prefer follow-up issues over unrelated refactors.

See `docs/development-standards.md` for architecture and code health guardrails.
