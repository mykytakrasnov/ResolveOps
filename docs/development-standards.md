# Development Standards

These standards keep ResolveOps maintainable as the workflow grows from a duplicate-charge tracer
into the full production-style demo.

## Product Boundary

- Build a bounded workflow agent, not a general chatbot.
- LangGraph owns explicit workflow nodes, durable checkpoints, interrupts, retry boundaries, and
  resume.
- Deterministic code owns authentication, authorization, policy enforcement, evidence validation,
  action execution, idempotency, audit logging, and report persistence.
- Model outputs must be structured, schema-validated, evidence-cited, and treated as untrusted
  until validated.

## Repository Boundaries

- `apps/web` owns the React product surface and Worker boundary.
- `apps/web/worker/synthetic-api` owns service-only synthetic system reads.
- `services/agent-api` owns FastAPI, LangGraph, repositories, policies, tools, and storage.
- `packages/contracts` contains generated frontend-safe contracts.
- `data` and `scripts/generate_synthetic_data.py` own deterministic synthetic fixtures.
- `evals`, `infra`, and `docs` should not absorb application runtime logic.

When adding behavior, place it in the narrowest existing boundary. Create a new module only when it
separates a real domain responsibility or removes meaningful duplication.

## Positive Patterns

- Prefer vertical slices that are demoable or verifiable end to end.
- Keep contracts typed at the boundary: Pydantic for backend truth, generated TypeScript for the
  frontend.
- Keep side effects behind typed adapters and idempotency records.
- Keep repository methods focused on one transaction or query family.
- Keep graph nodes small enough to explain their inputs, outputs, events, and failure behavior.
- Preserve append-only audit semantics.
- Add tests at the level where risk appears: policy unit tests, tool contract tests, graph tests,
  repository/integration tests, and UI tests for visible states.
- Use deterministic fallbacks when model failure must not fail a safe workflow outcome.

## Anti-Patterns To Prevent

- God modules that mix API routing, persistence, graph orchestration, policy, and UI concerns.
- Catch-all `utils` growth for domain behavior that deserves a named module.
- Model-controlled SQL, arbitrary HTTP URLs, shell commands, tool permissions, approval decisions,
  or action execution.
- Stringly typed cross-layer contracts when Pydantic, Zod, generated types, or enums can express
  the shape.
- Raw generated dataset access to hidden ground truth from ordinary app or agent routes.
- Tests that assert implementation trivia while missing the user-visible or safety-critical
  behavior.
- Fake behavior that makes a test pass but cannot support the next vertical slice.
- Logging or exporting secrets, cookies, authorization headers, raw uploaded files, hidden
  reasoning, or unredacted payloads.

## UI Standards

- UI issues labeled `ready-for-human` require collaboration before locking component choices.
- Use shadcn-compatible primitives consistently and prefer clear operational screens over
  decorative marketing layouts for authenticated product surfaces.
- Never show an indefinite spinner without current node/status and elapsed time.
- Distinguish model output, deterministic policy decisions, proposed actions, executed actions,
  stream disconnection, run failure, escalation, and approval waits.

## CodeGraph

Use CodeGraph before broad exploration for structural questions, dependency tracing,
blast-radius checks, or symbol lookup. Read returned source directly before editing. Skip it for
docs-only, tiny one-file, formatting, or direct-rg tasks.

## Definition Of Done

A change is ready when it:

- Satisfies the assigned issue acceptance criteria.
- Preserves the product and repository boundaries above.
- Adds or updates tests proportional to the risk.
- Updates contracts, docs, generated artifacts, or migrations when behavior requires it.
- Passes targeted checks and the full gate when practical.
