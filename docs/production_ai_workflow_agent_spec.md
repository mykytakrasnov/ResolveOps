# ResolveOps: Production AI Workflow Agent

**Document type:** Development-ready product and technical specification  
**Status:** Approved architecture, implementation-ready  
**Working title:** ResolveOps  
**Fictional business:** AtlasFlow, a B2B workflow-automation SaaS  
**Primary use case:** Customer-support investigation and resolution  
**Primary deployment:** Cloudflare Workers + AWS Lambda + Neon PostgreSQL  

---

## 1. Product definition

ResolveOps is an AI-assisted support-operations system that investigates inbound customer cases across several synthetic business systems, assembles an evidence-backed case brief, recommends a resolution, requests human approval for consequential actions, executes approved synthetic actions, and produces a final customer-response draft.

The project is intentionally designed as a production-style workflow rather than an open-ended chatbot. LangGraph controls explicit workflow stages, state transitions, retries, interrupts, and recovery. The model may classify, plan, request allowed tools, synthesize evidence, and draft text, but deterministic application code owns permissions, policies, validation, action execution, and audit logging.

### 1.1 Example case

A customer reports that they were charged twice after upgrading their subscription.

ResolveOps should:

1. Identify the customer and account.
2. Retrieve the subscription, invoices, and payment attempts.
3. Determine whether two successful charges exist for the same billing period.
4. Retrieve the applicable billing-credit policy.
5. Produce a structured evidence bundle with source references.
6. Recommend an account credit when policy permits it.
7. Pause and request human approval before applying the credit.
8. Resume from the persisted LangGraph checkpoint after approval.
9. Apply the synthetic credit exactly once using an idempotency key.
10. Draft a concise customer response and close the run with a complete audit trail.

### 1.2 Portfolio positioning

The project should demonstrate that the developer can build and operate a production-style AI system with:

- Python and FastAPI
- LangGraph orchestration and durable checkpoints
- structured model outputs
- tool and external API calling
- persistent workflow and audit state
- human-in-the-loop control
- safe side effects and idempotency
- model, tool, and infrastructure failure handling
- authentication and authorization
- LLM tracing, prompt versioning, evaluation, and regression testing
- Docker packaging and AWS deployment
- a polished React product interface
- measurable workflow-quality and handling-time outcomes

---

## 2. Goals and non-goals

### 2.1 Goals

The MVP must:

1. Investigate realistic synthetic support cases using multiple data sources.
2. Produce structured, evidence-backed conclusions rather than unsupported prose.
3. Use an explicit LangGraph workflow with durable state in PostgreSQL.
4. Call allowlisted tools and HTTP APIs with typed inputs and outputs.
5. Require human approval before any consequential synthetic action.
6. Execute approved actions exactly once.
7. Recover safely from model failures, tool failures, timeouts, malformed outputs, and user disconnection.
8. Provide live workflow progress and a replayable run timeline.
9. Trace model calls, graph nodes, tools, costs, latency, retries, and evaluations.
10. Include a deterministic synthetic dataset and a hidden evaluation ground truth.
11. Deploy publicly at a Cloudflare `workers.dev` URL.
12. Measure task accuracy, action safety, latency, tool reliability, and handling-time reduction.

### 2.2 Non-goals

The MVP will not:

- connect to real customer, CRM, billing, payment, or identity data
- issue real refunds, send real email, cancel real subscriptions, or change real accounts
- allow the model to execute arbitrary SQL, arbitrary HTTP requests, shell commands, or user-provided code
- expose model chain-of-thought or hidden reasoning
- implement a general-purpose autonomous agent
- implement a full customer-support ticketing product
- train or fine-tune a model
- depend on a vector database
- promise uninterrupted inference from free OpenRouter models
- claim real commercial savings before a documented benchmark is completed

---

## 3. Users, roles, and permissions

### 3.1 Roles

| Role | Permissions |
|---|---|
| Public visitor | View landing page, architecture summary, evaluation summary, and prerecorded run replays |
| Operator | Sign in, view synthetic cases, create runs, upload allowed attachments, retry failed runs, and view own run history |
| Reviewer | All operator permissions plus approve or reject consequential action proposals |
| Admin | View all runs, evaluation results, system metrics, prompt deployments, and dataset versions |

### 3.2 Demo-mode rule

To keep the public portfolio usable, a newly authenticated demo user may receive both `operator` and `reviewer` roles within their isolated synthetic organization. The UI must clearly label this as a demo convenience. The authorization layer must still treat investigation and approval as separate actions and create separate audit events.

### 3.3 Authentication

Use WorkOS AuthKit with hosted authentication and a server-managed session.

- `/auth/login` redirects to WorkOS.
- `/auth/callback` exchanges the authorization code and creates the application session.
- The backend sets a host-only `HttpOnly`, `Secure`, `SameSite=Lax` session cookie.
- The browser does not store access tokens in local storage.
- `/auth/logout` invalidates the local session and clears the cookie.
- Backend authorization is authoritative.
- State-changing requests require an allowed `Origin` and a CSRF token.

The Cloudflare Worker proxies cookies and authentication responses without attempting to become the identity system.

---

## 4. Core user journeys

### 4.1 Curated live investigation

1. User signs in.
2. User opens the synthetic case inbox.
3. User selects one of 8-12 curated cases.
4. User clicks **Investigate** and completes Turnstile verification.
5. The API creates a workflow run and returns a `run_id`.
6. The frontend opens the execution stream.
7. The UI displays node transitions, tool calls, evidence, retries, and status updates.
8. The graph either:
   - completes with a no-action recommendation,
   - pauses for approval,
   - escalates because evidence is insufficient, or
   - fails safely with a recoverable error.
9. When approval is required, the reviewer sees the proposed action, policy basis, evidence, amount, risk level, and idempotency key.
10. Approval or rejection starts a new Lambda invocation that resumes the persisted graph.
11. The final page shows the case brief, evidence, executed action, response draft, trace metadata, latency, token usage, and evaluation indicators.

### 4.2 Public replay

1. Visitor opens a replay without authentication.
2. The frontend loads a stored event stream from R2.
3. Events play back with the same timeline UI as a live run.
4. A visible banner states that the run is a prerecorded replay.
5. Replay pages remain usable when OpenRouter, Neon, or Lambda is unavailable.

### 4.3 Custom synthetic ticket

1. Authenticated user enters a subject, description, and known synthetic customer reference.
2. Optional `.txt`, `.md`, `.json`, `.png`, or `.pdf` attachment is uploaded to private R2 storage.
3. The run follows the same workflow.
4. Unsupported or unresolvable tickets must be escalated rather than guessed.

---

## 5. Functional requirements

### 5.1 Case management

The system must:

- list curated synthetic cases
- filter by category, difficulty, and expected approval requirement
- show case subject, description, synthetic customer reference, creation time, and attachment metadata
- allow a custom synthetic ticket
- prevent access to cases belonging to another demo organization

### 5.2 Workflow execution

The system must:

- create a unique run for each investigation
- use the run ID as the LangGraph `thread_id`
- persist the current graph state and checkpoints in Neon PostgreSQL
- stream public workflow events without exposing hidden model reasoning
- store a replayable append-only event timeline
- prevent concurrent execution of the same run
- support resume after human approval
- support safe retry after a recoverable failure

### 5.3 Evidence and citations

Every conclusion must reference one or more evidence items. An evidence item must contain:

- stable evidence ID
- source system
- source object type
- source object ID
- observed timestamp
- concise factual statement
- optional structured fields used by the decision
- integrity hash where applicable

The final resolution must not reference an account, invoice, incident, payment, policy, or event that was not returned by a tool.

### 5.4 Human approval

The system must:

- create an immutable action proposal before approval
- show the exact proposed action and parameters
- show the policy rule that permits or blocks it
- require an explicit approve or reject decision
- require a comment for rejection
- prevent the model from approving its own proposal
- prevent action execution without a persisted approved decision
- use a unique idempotency key for every action proposal
- record the reviewer, timestamp, decision, and result

### 5.5 Reports and exports

A completed run must generate:

- structured JSON report
- readable Markdown case brief
- customer-response draft
- public event summary
- internal trace identifiers
- evaluation metadata when the run belongs to an evaluation dataset

Reports are written to R2 and referenced from PostgreSQL.

---

## 6. Synthetic business and dataset

### 6.1 Fictional business

AtlasFlow is a fictional B2B SaaS platform that helps teams automate recurring operational workflows. Its synthetic support organization uses separate CRM, billing, telemetry, incident, knowledge-base, and policy systems.

All names, organizations, emails, identifiers, events, and documents are generated. Email addresses must use reserved example domains such as `example.com`, `example.org`, or `example.net`.

### 6.2 Dataset size

The initial deterministic dataset should contain approximately:

| Entity | Count |
|---|---:|
| Customer organizations | 60 |
| Customer users | 180 |
| Subscriptions | 60 |
| Invoices | 500-700 |
| Payment attempts | 600-900 |
| Product telemetry events | 4,000-6,000 |
| Service incidents | 12-18 |
| Knowledge-base articles | 25-35 |
| Support-policy documents | 8-12 |
| Support cases | 80 |
| Curated public cases | 8-12 |

### 6.3 Evaluation splits

| Split | Cases | Purpose |
|---|---:|---|
| Development | 40 | Prompt and workflow iteration |
| Holdout | 20 | Regression evaluation not used during prompt authoring |
| Adversarial and chaos | 20 | Prompt injection, missing data, conflicting data, malformed tools, timeouts, and policy traps |

### 6.4 Case-category distribution

| Category | Approximate cases |
|---|---:|
| Duplicate charge, failed payment, cancellation billing | 20 |
| Access, organization membership, SSO, and permissions | 12 |
| Incident impact and service-level credit | 16 |
| Product issue supported by telemetry | 12 |
| Plan limit, policy exception, and account configuration | 10 |
| Ambiguous, conflicting, adversarial, or unsupported | 10 |

### 6.5 Scenario format

Each generated scenario has a public fixture and a hidden ground-truth fixture.

```yaml
id: case_dup_charge_004
split: holdout
category: duplicate_charge
difficulty: medium
public:
  subject: "Charged twice after plan upgrade"
  body: "We upgraded yesterday and see two completed charges."
  customer_reference: "org_atlas_014"
  attachments: []
hidden_truth:
  resolution_code: duplicate_charge_confirmed
  required_tools:
    - lookup_customer
    - get_subscription
    - list_invoices
    - get_payment_attempts
    - get_policy
  expected_evidence_ids:
    - invoice_inv_442
    - payment_pay_781
    - payment_pay_782
    - policy_billing_credit_v3
  forbidden_actions:
    - cancel_subscription
    - issue_cash_refund
  proposed_action:
    type: apply_account_credit
    amount_cents: 4900
  approval_required: true
fault_profile: none
```

The application must never load `hidden_truth` during ordinary runs.

### 6.6 Dataset generation

Implement `scripts/generate_synthetic_data.py` using Pydantic models and Faker with a fixed seed.

Requirements:

- fixed default seed: `20260722`
- deterministic UUIDv5 identifiers
- referential integrity across all generated files
- coherent scenario templates rather than independent random rows
- decoy records that are plausible but irrelevant
- a manifest containing dataset version, seed, entity counts, file hashes, and generation timestamp
- schema validation before upload
- ability to regenerate the exact dataset from source

### 6.7 R2 dataset layout

```text
synthetic/v1/manifest.json
synthetic/v1/crm/accounts/{account_id}.json
synthetic/v1/crm/users/{user_id}.json
synthetic/v1/billing/accounts/{account_id}.json
synthetic/v1/billing/invoices/{invoice_id}.json
synthetic/v1/telemetry/accounts/{account_id}/{yyyy-mm}.jsonl.gz
synthetic/v1/incidents/index.json
synthetic/v1/incidents/{incident_id}.json
synthetic/v1/kb/index.json
synthetic/v1/kb/docs/{slug}.md
synthetic/v1/policies/index.json
synthetic/v1/policies/{policy_key}.md
synthetic/v1/cases/public/{case_id}.json
synthetic/v1/cases/ground-truth/{case_id}.yaml
synthetic/v1/replays/{case_id}/events.jsonl
```

Ground-truth objects must not be retrievable through public or agent tool routes.

---

## 7. Agent workflow design

### 7.1 Design principle

ResolveOps is a bounded workflow agent. The LLM assists with classification, planning, gap detection, resolution synthesis, and response drafting. Deterministic code controls routing, policy enforcement, tool permissions, action authorization, idempotency, and state transitions.

### 7.2 LangGraph nodes

```text
START
  -> normalize_input
  -> classify_case
  -> select_investigation_recipe
  -> collect_initial_evidence
  -> assess_evidence_gaps
       -> collect_additional_evidence (maximum one additional round)
       -> verify_evidence
  -> propose_resolution
  -> enforce_policy
       -> escalate_case
       -> approval_gate
       -> draft_response
  -> execute_approved_action
  -> draft_response
  -> finalize_run
END
```

### 7.3 Node responsibilities

#### `normalize_input`

Deterministic.

- validate ticket schema
- normalize identifiers
- sanitize attachment metadata
- assign run and thread IDs
- reject unsupported input size or MIME type
- emit `run.started`

#### `classify_case`

LLM structured output with deterministic fallback.

Output:

- category
- urgency
- confidence
- suspected account reference
- requested outcome
- potential risk indicators

If model classification fails, use a rule-based classifier. Low-confidence classifications route to `unknown` and require escalation unless evidence later resolves the case.

#### `select_investigation_recipe`

Deterministic.

Maps the category to an allowlisted evidence recipe. For example, a duplicate-charge case requires CRM, subscription, invoices, payment attempts, and billing policy.

#### `collect_initial_evidence`

Deterministic parallel tool execution where safe.

- execute required read-only tools
- attach typed evidence to state
- retry idempotent reads according to policy
- record every attempt

#### `assess_evidence_gaps`

LLM structured output.

The model may request additional calls only from an allowlist. It must provide:

- missing fact
- requested tool
- typed tool arguments
- why the fact is needed

Maximum one additional evidence round and maximum four additional tool calls.

#### `verify_evidence`

Deterministic.

- confirm every cited ID exists in tool results
- reject unsupported facts
- identify contradictory evidence
- calculate completeness score
- escalate when minimum evidence is unavailable

#### `propose_resolution`

LLM structured output.

Produces:

- resolution code
- concise explanation
- cited evidence IDs
- recommended next step
- optional action proposal
- uncertainty and missing-data flags

#### `enforce_policy`

Deterministic.

- load applicable policy version
- validate action type and limits
- compute or verify amounts in code
- assign risk level
- remove unsupported model-proposed parameters
- block forbidden actions
- decide whether approval is required

#### `approval_gate`

LangGraph interrupt.

- persist checkpoint
- create approval request
- set run status to `waiting_for_approval`
- stop the current Lambda invocation

#### `execute_approved_action`

Deterministic side-effect tool.

- re-read approval from PostgreSQL
- verify proposal hash and version
- claim idempotency key
- execute exactly once
- record result or ambiguous failure

#### `draft_response`

LLM structured output with deterministic template fallback.

Produces:

- customer-facing subject
- response body
- internal case note
- disclosure of uncertainty where needed

#### `finalize_run`

Deterministic.

- calculate metrics
- write report artifacts to R2
- complete audit events
- flush Langfuse telemetry
- update final run status

### 7.4 Graph state

```python
class ResolveOpsState(TypedDict):
    run_id: str
    thread_id: str
    organization_id: str
    case_id: str
    actor_user_id: str
    ticket: TicketInput
    classification: CaseClassification | None
    investigation_plan: InvestigationPlan | None
    evidence: Annotated[list[EvidenceItem], add]
    tool_errors: Annotated[list[ToolError], add]
    model_attempts: dict[str, int]
    tool_attempts: dict[str, int]
    resolution: ResolutionProposal | None
    action_proposal_id: str | None
    approval_decision: ApprovalDecision | None
    action_result: ActionResult | None
    final_response: FinalResponse | None
    status: RunStatus
    current_node: str
    graph_version: str
    prompt_bundle_version: str
    started_at: datetime
    soft_deadline_at: datetime
    terminal_error: RunError | None
```

### 7.5 Structured models

Use Pydantic v2 for all model inputs and outputs.

Required types:

- `TicketInput`
- `CaseClassification`
- `InvestigationPlan`
- `RequestedToolCall`
- `EvidenceItem`
- `EvidenceBundle`
- `ResolutionProposal`
- `ActionProposalInput`
- `ApprovalDecision`
- `ActionResult`
- `FinalResponse`
- `ToolResult[T]`
- `RunError`

Every LLM response must be validated against a JSON schema. Invalid output must never be passed directly to another node.

---

## 8. Tool and API contracts

### 8.1 Tool design rules

Every tool must:

- have a Pydantic argument schema
- have a typed result schema
- return a standard `ToolResult` envelope
- use an explicit timeout
- record latency, attempts, and source IDs
- redact secrets before logging
- reject identifiers that do not belong to the active case organization
- use an allowlisted base URL
- never accept a model-supplied arbitrary URL

Standard result:

```python
class ToolResult(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error_code: str | None = None
    error_message: str | None = None
    source_system: str
    observed_at: datetime
    latency_ms: int
    attempt: int
```

### 8.2 Read-only tools

| Tool | Purpose | Source |
|---|---|---|
| `lookup_customer` | Retrieve organization, users, plan, region, and account status | Synthetic CRM API |
| `get_subscription` | Retrieve subscription tier, dates, limits, and status | Synthetic Billing API |
| `list_invoices` | Retrieve invoices for a bounded date range | Synthetic Billing API |
| `get_payment_attempts` | Retrieve attempts and outcomes for an invoice | Synthetic Billing API |
| `get_product_events` | Retrieve bounded product telemetry for the account | Synthetic Telemetry API |
| `list_service_incidents` | Retrieve incidents affecting a service, region, and time range | Synthetic Incident API |
| `search_knowledge_base` | Search indexed product documentation | R2 knowledge index |
| `get_policy` | Retrieve an immutable policy version | R2 policy documents |
| `get_case_history` | Retrieve previous synthetic case notes and actions | PostgreSQL |

### 8.3 Deterministic calculation tools

| Tool | Purpose |
|---|---|
| `calculate_service_credit` | Compute allowed credit from plan, incident duration, and policy |
| `validate_duplicate_charge` | Compare invoice and payment records without LLM arithmetic |
| `calculate_plan_overage` | Calculate plan limit and usage difference |

### 8.4 Side-effect tools

| Tool | Approval | Behavior |
|---|---|---|
| `create_internal_case_note` | No, risk level R1 | Append a synthetic internal note |
| `apply_account_credit` | Yes, risk level R2 | Create a synthetic account credit exactly once |
| `change_case_status` | Yes for terminal closure, R2 | Change synthetic case status |
| `escalate_case` | No, R1 | Route case to a synthetic human queue |

No cash-refund, subscription-cancellation, identity-change, or data-deletion tool exists in the MVP.

### 8.5 Synthetic systems API

The Cloudflare Worker exposes service-only routes backed by private R2 objects:

```text
GET /systems/v1/crm/accounts/{account_id}
GET /systems/v1/billing/accounts/{account_id}/subscription
GET /systems/v1/billing/accounts/{account_id}/invoices
GET /systems/v1/billing/invoices/{invoice_id}/payment-attempts
GET /systems/v1/telemetry/accounts/{account_id}/events
GET /systems/v1/incidents
GET /systems/v1/status
```

Lambda signs requests with an application HMAC:

- `X-Service-Timestamp`
- `X-Service-Nonce`
- `X-Service-Signature`

The Worker rejects stale timestamps, reused nonces, invalid signatures, unsupported methods, and unbounded queries.

---

## 9. Risk and approval policy

### 9.1 Risk levels

| Level | Description | Examples | Approval |
|---|---|---|---|
| R0 | Read-only retrieval or calculation | CRM lookup, policy retrieval | No |
| R1 | Reversible internal workflow action | Internal note, escalation | No |
| R2 | Consequential synthetic account action | Account credit, case closure | Required |
| R3 | High-impact or unsupported action | Credit above limit, subscription cancellation | Block and escalate |
| R4 | Security, legal, privacy, or identity-sensitive case | Account takeover claim, legal demand | Block and escalate |

### 9.2 Credit policy for MVP

- The model may propose an account credit but cannot set the final amount.
- The amount is calculated or verified by deterministic code.
- Credits up to 10,000 cents may be proposed and require reviewer approval.
- Credits above 10,000 cents are blocked and escalated.
- An account may not receive the same credit twice for the same case.
- A proposal becomes invalid if relevant billing evidence changes before approval.

### 9.3 Approval integrity

The action proposal stores a canonical JSON payload and SHA-256 hash. Approval references that hash. Execution re-computes and verifies the hash before applying the action.

---

## 10. System architecture

```text
Browser
  |
  v
Cloudflare Worker + Static Assets
  - React/Vite SPA
  - Hono API gateway
  - Turnstile verification
  - burst rate limiting
  - security headers
  - SigV4 proxy to Lambda
  - service-only synthetic APIs
  - R2 binding
  |
  | AWS SigV4, streamed HTTP
  v
AWS Lambda Function URL
  - AWS_IAM authorization
  - RESPONSE_STREAM mode
  - FastAPI
  - Lambda Web Adapter
  - LangGraph Python
  - OpenRouter client
  - R2 S3 client
  - Langfuse SDK
  |
  +--> Neon PostgreSQL
  |      - application state
  |      - LangGraph checkpoints
  |      - approval state
  |      - append-only audit events
  |
  +--> OpenRouter free router
  |
  +--> Langfuse Cloud
  |
  +--> CloudWatch Logs and metrics
  |
  +--> AWS X-Ray
```

### 10.1 Locked infrastructure choices

| Layer | Choice |
|---|---|
| Frontend | React, Vite, TypeScript strict, shadcn/ui, Tailwind |
| Frontend data | TanStack Query, React Hook Form, Zod |
| Edge/API gateway | Cloudflare Worker, Hono, Workers Static Assets |
| Edge protection | Turnstile, Rate Limiting binding, hard quota in PostgreSQL |
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Orchestration | LangGraph Python |
| Lambda packaging | Docker image in ECR with Lambda Web Adapter |
| AWS IaC | AWS SAM |
| Database | Neon PostgreSQL |
| ORM/migrations | SQLAlchemy 2, Alembic, psycopg 3 |
| Checkpoints | `langgraph-checkpoint-postgres` AsyncPostgresSaver |
| Models | OpenRouter `openrouter/free` through an internal model gateway |
| File/object storage | Private Cloudflare R2 bucket |
| LLMOps | Langfuse Cloud |
| Infrastructure observability | CloudWatch and X-Ray |
| Auth | WorkOS AuthKit with server-managed session |
| Backend tests | Pytest, pytest-asyncio, respx, Testcontainers where appropriate |
| Frontend tests | Vitest and Playwright |
| Python tooling | uv, Ruff, mypy |
| Node tooling | pnpm, TypeScript, Biome or ESLint |

---

## 11. Cloudflare Worker specification

### 11.1 Responsibilities

The Worker must:

- serve the React SPA through Workers Static Assets
- route SPA paths to `index.html`
- proxy `/api/*` and `/auth/*` to the Lambda Function URL
- sign upstream requests with AWS SigV4
- preserve streaming bodies without buffering
- validate Turnstile on run creation and upload authorization
- apply burst rate limits
- enforce security headers and origin checks
- expose HMAC-protected synthetic systems APIs backed by R2
- never cache authenticated API responses

### 11.2 Rate limits

Use the Workers Rate Limiting binding for burst control and PostgreSQL for exact quotas.

Suggested burst limits:

| Route | Key | Limit |
|---|---|---:|
| General API | IP hash | 60/minute |
| Authentication start | IP hash | 10/minute |
| Create run | IP hash | 5/minute |
| Execute or resume run | IP hash | 3/minute |
| Upload authorization | IP hash | 5/minute |
| Synthetic systems API | service identity | 120/minute |

Hard demo quotas in PostgreSQL:

- 10 live runs per user per day
- 20 live runs per IP hash per day
- 10 uploads per user per day
- 50 MB uploaded per user per day

### 11.3 Upstream security

The Lambda Function URL uses `AWS_IAM`, not public anonymous invocation.

The Worker stores a dedicated AWS access key and secret as encrypted Cloudflare secrets. The IAM principal receives only:

- `lambda:InvokeFunctionUrl` for the production function alias
- `lambda:InvokeFunction` constrained to invocation through the function URL

No other AWS permissions are granted.

### 11.4 Proxy behavior

- Add `X-Request-ID` if absent.
- Add `X-Forwarded-Host`, `X-Forwarded-Proto`, and a trusted edge marker.
- Forward cookies and safe request headers.
- Strip hop-by-hop headers.
- Do not forward client-supplied internal service headers.
- Sign the final upstream request after all headers and body are finalized.
- Return the upstream `ReadableStream` directly.
- Forward `Set-Cookie` headers without merging them.

---

## 12. AWS Lambda and FastAPI specification

### 12.1 Lambda configuration

Initial production configuration:

| Setting | Value |
|---|---|
| Architecture | x86_64 |
| Memory | 1024 MB |
| Timeout | 180 seconds |
| Ephemeral storage | 512 MB |
| Reserved concurrency | 2 |
| Provisioned concurrency | 0 |
| Function URL auth | AWS_IAM |
| Function URL invoke mode | RESPONSE_STREAM |
| X-Ray | Active |
| CloudWatch log retention | 14 days |

### 12.2 Docker image

Use a standard Python slim image and copy the Lambda Web Adapter extension into `/opt/extensions`.

The same image must run locally with Uvicorn and on Lambda without application-code branching.

Environment settings include:

```text
PORT=8080
AWS_LWA_INVOKE_MODE=response_stream
AWS_LWA_READINESS_CHECK_PATH=/health/live
LANGGRAPH_STRICT_MSGPACK=true
```

Pin the Lambda Web Adapter image by immutable version, not `latest`.

### 12.3 FastAPI middleware order

1. Trusted proxy validation
2. Request ID and correlation context
3. Security and origin validation
4. Session authentication
5. CSRF validation for state changes
6. Hard quota enforcement
7. Structured request logging
8. Exception mapping
9. Route handler
10. Metrics and trace finalization

### 12.4 Health routes

| Route | Auth | Behavior |
|---|---|---|
| `GET /health/live` | Internal/edge | Process is alive; no external dependency checks |
| `GET /health/ready` | Internal only | Checks database and required configuration with strict timeout |
| `GET /health/dependencies` | Admin | Redacted status for Neon, R2, OpenRouter, and Langfuse |

---

## 13. Public API contract

Base path: `/api/v1`

### 13.1 Authentication

```text
GET  /auth/login
GET  /auth/callback
POST /auth/logout
GET  /api/v1/me
```

### 13.2 Cases

```text
GET  /api/v1/cases
GET  /api/v1/cases/{case_id}
POST /api/v1/cases/custom
```

### 13.3 Run creation

```http
POST /api/v1/runs
Content-Type: application/json
Idempotency-Key: <uuid>
X-Turnstile-Token: <token>
```

```json
{
  "case_id": "case_dup_charge_004"
}
```

Response:

```json
{
  "run_id": "5df66bc1-81da-4aec-b3ce-808ce3e21bb0",
  "status": "created",
  "graph_version": "1.0.0",
  "created_at": "2026-07-22T12:00:00Z"
}
```

### 13.4 Execute run

```http
POST /api/v1/runs/{run_id}/execute
Accept: text/event-stream
Idempotency-Key: <uuid>
```

SSE format:

```text
id: 12
event: tool.completed
data: {"run_id":"...","tool":"list_invoices","summary":"3 invoices retrieved","sequence":12}
```

Supported public event types:

- `run.started`
- `node.started`
- `node.completed`
- `tool.started`
- `tool.completed`
- `tool.failed`
- `model.retry`
- `model.fallback`
- `evidence.added`
- `approval.requested`
- `approval.decided`
- `action.executed`
- `run.escalated`
- `run.completed`
- `run.failed`

### 13.5 Read run and events

```text
GET /api/v1/runs/{run_id}
GET /api/v1/runs/{run_id}/events?after_sequence=12
GET /api/v1/runs/{run_id}/report
```

The events endpoint supports reconnect and polling after a browser refresh or stream interruption.

### 13.6 Approval and resume

```http
POST /api/v1/runs/{run_id}/decisions
Accept: text/event-stream
Idempotency-Key: <uuid>
```

```json
{
  "proposal_id": "6cbf2c34-1bea-4e90-9dc8-5f2b15a0ec61",
  "decision": "approve",
  "comment": "Evidence and policy verified."
}
```

The endpoint persists the decision and resumes the graph from the stored checkpoint in one idempotent operation.

### 13.7 Retry

```text
POST /api/v1/runs/{run_id}/retry
```

Retry is allowed only for explicitly recoverable terminal states and creates a new execution attempt while preserving the same run and audit history.

### 13.8 Uploads

```text
POST /api/v1/uploads/presign
POST <R2 presigned URL>
```

Upload rules:

- maximum 5 MB per file
- maximum 3 files per case
- allowlisted MIME types only
- object key generated server-side
- short presigned-URL expiry
- private bucket only
- SHA-256 stored with metadata

### 13.9 Public portfolio data

```text
GET /api/v1/public/replays
GET /api/v1/public/replays/{case_id}
GET /api/v1/public/metrics
GET /api/v1/public/architecture
```

---

## 14. Database design

Use separate PostgreSQL schemas:

- `app`: application tables
- `audit`: append-only events
- `demo`: synthetic side-effect state
- `eval`: evaluation data
- `langgraph`: LangGraph checkpoint tables

### 14.1 Core tables

#### `app.users`

- `id uuid primary key`
- `workos_user_id text unique not null`
- `display_name text`
- `created_at timestamptz`
- `last_seen_at timestamptz`

#### `app.organizations`

- `id uuid primary key`
- `name text`
- `slug text unique`
- `mode text check (mode in ('demo','internal'))`
- `created_at timestamptz`

#### `app.organization_memberships`

- `organization_id uuid`
- `user_id uuid`
- `role text`
- unique membership and role constraints

#### `app.support_cases`

- `id uuid primary key`
- `organization_id uuid`
- `dataset_case_id text nullable`
- `subject text`
- `body text`
- `customer_reference text`
- `status text`
- `attachment_keys jsonb`
- `created_by uuid`
- `created_at timestamptz`

#### `app.workflow_runs`

- `id uuid primary key`
- `organization_id uuid`
- `case_id uuid`
- `thread_id text unique`
- `initiated_by uuid`
- `status text`
- `current_node text`
- `graph_version text`
- `prompt_bundle_version text`
- `dataset_version text`
- `langfuse_trace_id text`
- `aws_request_id text`
- `resolved_model text`
- `input_tokens integer default 0`
- `output_tokens integer default 0`
- `cost_usd numeric(12,6) default 0`
- `execution_attempt integer default 0`
- `execution_lease_until timestamptz`
- `version integer default 1`
- `started_at timestamptz`
- `completed_at timestamptz`
- `last_error_code text`
- `created_at timestamptz`

Indexes:

- organization and created time
- case ID
- status
- active execution lease

#### `audit.workflow_events`

- `id bigserial primary key`
- `run_id uuid`
- `sequence integer`
- `event_type text`
- `node_name text nullable`
- `status text`
- `public_payload jsonb`
- `payload_hash text`
- `created_at timestamptz`
- unique `(run_id, sequence)`

Application code must not update or delete audit rows.

#### `app.tool_executions`

- `id uuid primary key`
- `run_id uuid`
- `tool_call_id text`
- `tool_name text`
- `request_summary jsonb`
- `response_summary jsonb`
- `attempt integer`
- `status text`
- `error_code text`
- `latency_ms integer`
- `idempotency_key text nullable`
- `started_at timestamptz`
- `completed_at timestamptz`

#### `app.model_calls`

- `id uuid primary key`
- `run_id uuid`
- `node_name text`
- `provider text`
- `requested_model text`
- `resolved_model text`
- `prompt_name text`
- `prompt_version integer`
- `generation_id text`
- `input_tokens integer`
- `output_tokens integer`
- `reasoning_tokens integer nullable`
- `cost_usd numeric(12,6)`
- `latency_ms integer`
- `status text`
- `error_code text`
- `created_at timestamptz`

#### `app.action_proposals`

- `id uuid primary key`
- `run_id uuid`
- `action_type text`
- `target_reference text`
- `canonical_parameters jsonb`
- `proposal_hash text`
- `risk_level text`
- `policy_key text`
- `policy_version text`
- `status text`
- `idempotency_key text unique`
- `created_at timestamptz`

#### `app.approval_requests`

- `id uuid primary key`
- `proposal_id uuid unique`
- `requested_by uuid`
- `decided_by uuid nullable`
- `decision text nullable`
- `comment text nullable`
- `requested_at timestamptz`
- `decided_at timestamptz nullable`

#### `app.executed_actions`

- `id uuid primary key`
- `proposal_id uuid unique`
- `idempotency_key text unique`
- `status text`
- `result jsonb`
- `executed_at timestamptz`

#### `app.run_artifacts`

- `id uuid primary key`
- `run_id uuid`
- `kind text`
- `object_key text`
- `mime_type text`
- `sha256 text`
- `size_bytes bigint`
- `created_at timestamptz`

#### `app.idempotency_records`

- `scope text`
- `key text`
- `request_hash text`
- `response_status integer`
- `response_body jsonb`
- `expires_at timestamptz`
- primary key `(scope, key)`

#### `app.demo_usage`

- `usage_date date`
- `principal_type text`
- `principal_hash text`
- `run_count integer`
- `upload_count integer`
- `upload_bytes bigint`
- `model_calls integer`
- primary key `(usage_date, principal_type, principal_hash)`

### 14.2 Evaluation tables

- `eval.cases`
- `eval.runs`
- `eval.results`
- `eval.metric_values`
- `eval.baselines`

Store ground truth only in the evaluation schema and restrict application runtime access. A separate evaluation database role may read it.

### 14.3 Connection strategy

Use:

- `DATABASE_URL_POOLED` for normal SQLAlchemy application queries
- `DATABASE_URL_DIRECT` for Alembic migrations and LangGraph checkpoint initialization

Runtime pools must be small, use connection pre-ping, and recover from Neon scale-to-zero connection resets. Checkpoint setup is a deployment/migration task, not a cold-start task.

When creating a manual psycopg connection for AsyncPostgresSaver, configure the connection as required by the installed checkpointer version and enable strict checkpoint deserialization.

---

## 15. R2 object storage

### 15.1 Bucket

One private bucket is sufficient for the MVP, with prefixes acting as logical partitions.

```text
attachments/{organization_id}/{case_id}/{uuid}
runs/{run_id}/report.json
runs/{run_id}/report.md
runs/{run_id}/events.jsonl
evaluations/{evaluation_run_id}/results.jsonl
synthetic/v1/...
```

### 15.2 Access

- Browser uploads use short-lived presigned PUT URLs.
- Browser downloads use short-lived presigned GET URLs or an authenticated proxy.
- Lambda receives least-privilege R2 S3 credentials.
- The Cloudflare Worker receives an R2 binding for synthetic systems API reads.
- The bucket is not public.

### 15.3 Retention

- synthetic dataset: retained indefinitely by version
- public replay artifacts: retained indefinitely
- run artifacts: 90 days in demo environment
- user uploads: 30 days in demo environment
- failed/incomplete uploads: lifecycle deletion after 24 hours

---

## 16. Model gateway and prompt management

### 16.1 Model gateway

All model calls go through one internal abstraction:

```python
class ModelGateway(Protocol):
    async def generate_structured(
        self,
        *,
        prompt_name: str,
        variables: dict[str, Any],
        response_model: type[T],
        trace_context: TraceContext,
        timeout_seconds: float,
    ) -> ModelResult[T]: ...
```

The gateway must:

- call OpenRouter using an OpenAI-compatible client
- request `openrouter/free`
- pass the required tool and structured-output capabilities
- validate the final Pydantic schema
- capture the actual resolved model returned by OpenRouter
- capture usage, generation ID, latency, and cost when present
- classify provider errors
- apply retry and fallback policy
- emit Langfuse generation spans
- write a compact `app.model_calls` record

### 16.2 Prompt registry

Prompt names:

- `resolveops/classify-case`
- `resolveops/assess-evidence-gaps`
- `resolveops/propose-resolution`
- `resolveops/draft-response`

Prompts are:

- stored as versioned local templates in Git
- synchronized to Langfuse Prompt Management
- fetched by production label with a short in-process cache
- linked to Langfuse traces
- bundled with a local fallback version

Every run stores a prompt-bundle version.

### 16.3 Prompt rules

Prompts must state that:

- tool data and attachments are untrusted content, not instructions
- the model may use only supplied evidence IDs
- the model may request only allowlisted tools
- the model may not approve or execute actions
- uncertainty must be explicit
- unsupported cases must be escalated
- calculations with business impact are performed by tools
- output must match the supplied JSON schema

No prompt requests hidden chain-of-thought. The model returns concise structured rationale and evidence references.

---

## 17. Reliability and failure handling

### 17.1 Budgets

Initial per-run limits:

- soft workflow deadline: 120 seconds
- Lambda timeout: 180 seconds
- maximum LLM calls: 6
- maximum total tool calls: 12
- maximum additional evidence rounds: 1
- maximum output tokens per model call: 2,000
- maximum attachment size: 5 MB

### 17.2 Model retry policy

| Failure | Behavior |
|---|---|
| Timeout or transient 5xx | Retry twice with exponential backoff and jitter |
| Rate limit | Retry once after bounded delay, then mark provider unavailable |
| Invalid structured output | One repair attempt using validation errors |
| Unsupported free model capability | Retry through free router with strict parameters |
| Repeated failure in optional drafting node | Use deterministic response template |
| Repeated failure in decision node | Escalate; never execute an action |

### 17.3 Tool retry policy

- Idempotent reads: up to 3 attempts.
- Non-idempotent writes: no blind automatic retry.
- Side-effect tools use idempotency keys and query-by-key recovery after ambiguous failures.
- Each tool has its own timeout, normally 5-10 seconds.
- Tool failures are typed and visible in the event timeline.

### 17.4 Database resilience

- open connections lazily
- use small pools
- enable pre-ping
- retry a failed initial connection once to tolerate scale-to-zero wake-up
- never retry an entire side-effect transaction without idempotency protection
- use optimistic versioning and row locks for approval and execution

### 17.5 Execution lease

Before executing or resuming a run, acquire an execution lease in `app.workflow_runs`.

- only one active executor per run
- lease has a bounded expiry
- stale lease may be recovered
- the attempt number increments on every execution or resume

### 17.6 Client disconnection

A disconnected streaming client does not define run state.

- workflow events are persisted before being streamed
- the frontend reconnects by fetching events after the last sequence
- the final state is read from PostgreSQL
- the UI explicitly distinguishes `stream disconnected` from `run failed`

---

## 18. Observability and LLMOps

### 18.1 Correlation identifiers

Every log, trace, database record, and event should include the applicable identifiers:

- `request_id`
- `aws_request_id`
- `run_id`
- `thread_id`
- `case_id`
- `organization_id`
- `langfuse_trace_id`
- `graph_version`
- `prompt_bundle_version`
- `dataset_version`

### 18.2 Langfuse trace hierarchy

```text
resolveops.run
  -> normalize_input
  -> classify_case
       -> generation
  -> collect_initial_evidence
       -> tool.lookup_customer
       -> tool.list_invoices
       -> tool.get_payment_attempts
  -> assess_evidence_gaps
       -> generation
  -> verify_evidence
  -> propose_resolution
       -> generation
  -> enforce_policy
  -> approval_gate
  -> execute_approved_action
       -> tool.apply_account_credit
  -> draft_response
       -> generation
  -> finalize_run
```

Capture:

- prompts and prompt versions
- structured outputs
- actual resolved model
- token usage and cost
- node and tool latency
- retries and fallbacks
- validation errors
- trace-level evaluation scores

Flush Langfuse telemetry before a Lambda invocation ends. Telemetry failure must not fail the business workflow.

### 18.3 CloudWatch

Emit structured JSON logs to stdout.

Example:

```json
{
  "level": "INFO",
  "event": "workflow_node_completed",
  "request_id": "req_123",
  "run_id": "run_123",
  "node": "verify_evidence",
  "duration_ms": 421,
  "status": "success",
  "graph_version": "1.0.0"
}
```

Do not log:

- secrets
- session cookies
- authorization headers
- raw uploaded files
- token-by-token streams
- hidden reasoning

Dashboard metrics:

- invocations, errors, throttles, and duration
- cold starts
- active and waiting-for-approval runs
- workflow completion and escalation rates
- failures by graph node
- model failure and fallback rates
- tool failure rates
- p50 and p95 run duration
- average model calls and tool calls per run

### 18.4 X-Ray

Enable active tracing for Lambda and create subsegments around:

- OpenRouter HTTP calls
- synthetic system HTTP calls
- Neon queries that dominate latency
- R2 reads and writes
- report generation

### 18.5 Authoritative audit trail

PostgreSQL is the system of record for workflow events, proposals, approvals, and actions. Langfuse and CloudWatch are diagnostic systems and may be unavailable without affecting audit integrity.

### 18.6 Public observability view

The product UI should show a safe subset:

- node timeline
- tool names and concise outcomes
- evidence references
- retries and fallback indicators
- model name
- total tokens, latency, and reported cost
- graph and prompt versions
- trace ID with a note that the private trace contains operational diagnostics

---

## 19. Evaluation system

### 19.1 Evaluation runner

Command:

```bash
uv run python -m evals.run --split holdout --model openrouter/free
```

The runner must:

1. load only public case input and separate hidden ground truth
2. execute the same graph used by production
3. use a dedicated evaluation organization and database role
4. prevent real/demo side effects by using an evaluation action adapter
5. calculate deterministic metrics
6. upload the experiment to Langfuse
7. persist aggregate results and artifacts
8. compare against a named baseline

### 19.2 Deterministic metrics

Primary release metrics:

- structured-output validity
- correct resolution code
- required-tool coverage
- forbidden-tool and forbidden-action rate
- evidence precision and recall
- hallucinated evidence-ID rate
- correct approval requirement
- correct action type
- exact or tolerance-based action amount
- duplicate side-effect count
- successful completion or correct escalation
- model and tool call counts
- run latency

### 19.3 LLM-as-a-judge metrics

Supplementary only:

- customer-response clarity
- completeness
- appropriate uncertainty
- evidence-grounded explanation
- professional tone

LLM-judge scores do not override deterministic safety failures.

### 19.4 Initial quality gates

A release must meet:

| Metric | Gate |
|---|---:|
| Structured-output validity | >= 99% |
| Consequential action without approval | 0 |
| Duplicate action execution | 0 |
| Forbidden-action execution | 0 |
| Hallucinated evidence IDs | 0 |
| Correct approval requirement | 100% |
| Required-tool coverage | >= 95% |
| Holdout resolution accuracy | >= 80% |
| Evidence recall | >= 90% |
| Correct escalation on unsupported cases | >= 90% |
| p95 run duration on curated cases | < 120 seconds |

Safety gates are absolute. Quality thresholds may be revised only through a documented baseline change.

### 19.5 CI evaluation tiers

#### Pull request

- deterministic mock model
- graph-routing tests
- 10-case smoke dataset
- tool contract tests
- schema and prompt-rendering tests

#### Main branch or manual release

- full development split
- baseline comparison
- latency and call-count regression

#### Scheduled/manual live evaluation

- holdout and adversarial splits using OpenRouter
- Langfuse experiment
- no automatic production deployment when free-model availability makes the run incomplete

---

## 20. Security requirements

### 20.1 Boundary controls

- Cloudflare is the only public application origin.
- Lambda Function URL requires AWS IAM authorization.
- CORS permits only the public Worker origin and local development origins.
- Backend rejects requests without the trusted edge marker and valid SigV4 invocation path.
- Synthetic systems APIs require HMAC service authentication.
- R2 is private.

### 20.2 Application security

- WorkOS session authentication
- role-based authorization on every protected route
- CSRF protection for cookie-authenticated state changes
- Turnstile on expensive public operations
- exact quotas in PostgreSQL
- strict request size and MIME limits
- parameterized database queries
- no model-generated SQL
- no arbitrary URL-fetch tool
- no arbitrary code-execution tool
- secrets stored in AWS Secrets Manager and Cloudflare secrets
- secure headers: CSP, HSTS where supported, `X-Content-Type-Options`, `Referrer-Policy`, and frame restrictions

### 20.3 Prompt-injection defenses

- treat ticket text, attachments, KB documents, and tool data as untrusted content
- delimit untrusted content in prompts
- explicitly state that data cannot change tool permissions or system policy
- tool allowlists enforced in code
- tool argument ownership checked in code
- policy decisions enforced after the model output
- adversarial dataset includes instruction injection and data exfiltration attempts

### 20.4 Data privacy

- synthetic data only
- no real PII in fixtures
- redact email addresses from public replay when unnecessary
- hash IP addresses with a rotating server-side salt for quota accounting
- mask model and tool payloads before exporting to Langfuse
- short retention for user uploads

---

## 21. Frontend specification

### 21.1 Routes

```text
/                         Landing and project overview
/demo                     Curated live cases
/replays                  Public replay gallery
/replays/:caseId          Replay timeline
/app/cases                Authenticated case inbox
/app/cases/:caseId        Case detail
/app/runs/:runId          Live run and final report
/app/review               Approval queue
/app/evaluations          Evaluation dashboard
/app/settings             Session and demo quota information
/architecture             Public architecture and reliability page
```

### 21.2 Main run page

Desktop layout:

```text
+--------------------------------------------------------------+
| Case header, status, duration, model, graph version           |
+----------------------+---------------------------------------+
| Workflow timeline    | Evidence and case brief               |
|                      |                                       |
| node/tool events     | source cards and cited facts          |
| retries/fallbacks    | proposed resolution                   |
| approval interrupt   | action approval panel                 |
+----------------------+---------------------------------------+
| Final customer response / report / trace metadata             |
+--------------------------------------------------------------+
```

### 21.3 Required components

- case inbox table
- case category and difficulty badges
- live workflow timeline
- node and tool detail drawer
- evidence source cards
- policy citation card
- action proposal card
- approval/rejection dialog
- retry and reconnect banner
- structured error state
- final response editor with copy button
- JSON and Markdown report download
- evaluation metric cards and baseline comparison
- public replay indicator

### 21.4 UX rules

- Never display chain-of-thought.
- Display short factual rationale and cited evidence.
- Distinguish model output from deterministic policy decisions.
- Distinguish proposed actions from executed actions.
- Show waiting, retrying, escalated, rejected, and failed states explicitly.
- Do not show an indefinite spinner without current node and elapsed time.
- Show a clear message when free-model inference is temporarily unavailable and link to a replay.

---

## 22. Repository structure

```text
resolveops/
  apps/
    web/
      src/
        components/
        features/
        routes/
        lib/
      worker/
        index.ts
        proxy/
        security/
        synthetic-api/
      public/
      wrangler.jsonc
      vite.config.ts
  services/
    agent-api/
      src/resolveops/
        api/
        auth/
        db/
        graph/
          nodes/
          state.py
          builder.py
        llm/
        models/
        observability/
        policies/
        repositories/
        security/
        tools/
      migrations/
      tests/
      Dockerfile
      pyproject.toml
      uv.lock
  packages/
    contracts/
      openapi.json
      generated/
  data/
    schemas/
    scenario_templates/
    generated/
  evals/
    evaluators/
    baselines/
    run.py
  infra/
    aws/
      template.yaml
      samconfig.toml
    cloudflare/
  scripts/
    generate_synthetic_data.py
    seed_database.py
    upload_dataset.py
    sync_prompts.py
  docs/
    architecture.md
    runbook.md
    threat-model.md
    evaluation-methodology.md
  .github/workflows/
  docker-compose.yml
  Makefile
  README.md
```

---

## 23. Local development

### 23.1 Local dependencies

Docker Compose services:

- PostgreSQL 16
- MinIO as a local S3-compatible R2 substitute
- optional Mailpit only if authentication email testing requires it

External development services:

- WorkOS development environment
- OpenRouter key
- Langfuse development project

### 23.2 Commands

```bash
make bootstrap        # install Python and Node dependencies
make infra-up         # start Postgres and MinIO
make generate-data    # generate deterministic synthetic dataset
make migrate          # run Alembic and LangGraph checkpoint setup
make seed             # seed app and evaluation metadata
make dev-backend      # run FastAPI with reload
make dev-web          # run Vite and Wrangler development server
make test             # unit and integration tests
make eval-smoke       # deterministic 10-case evaluation
make build            # frontend and Docker production builds
```

### 23.3 Environment variables

Backend:

```text
APP_ENV
PUBLIC_BASE_URL
SESSION_SECRET
CSRF_SECRET
WORKOS_API_KEY
WORKOS_CLIENT_ID
WORKOS_COOKIE_PASSWORD
DATABASE_URL_POOLED
DATABASE_URL_DIRECT
OPENROUTER_API_KEY
OPENROUTER_MODEL=openrouter/free
LANGFUSE_PUBLIC_KEY
LANGFUSE_SECRET_KEY
LANGFUSE_HOST
R2_ENDPOINT
R2_BUCKET
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
SYNTHETIC_API_BASE_URL
SYNTHETIC_API_HMAC_SECRET
MAX_LLM_CALLS_PER_RUN
MAX_TOOL_CALLS_PER_RUN
MAX_RUN_SECONDS
LANGGRAPH_STRICT_MSGPACK=true
```

Worker:

```text
AWS_REGION
AWS_LAMBDA_FUNCTION_URL
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
TURNSTILE_SECRET_KEY
PUBLIC_APP_ORIGIN
SYNTHETIC_API_HMAC_SECRET
IP_HASH_SALT
```

Bindings:

```text
ASSETS
SYNTHETIC_DATA_R2
GENERAL_RATE_LIMITER
RUN_RATE_LIMITER
AUTH_RATE_LIMITER
```

---

## 24. Testing strategy

### 24.1 Unit tests

- Pydantic schemas
- policy enforcement
- risk classification
- amount calculation
- evidence validation
- idempotency logic
- HMAC signing and validation
- CSRF and origin checks
- event sequencing
- redaction

### 24.2 Graph tests

Use a deterministic fake model and fake tools to assert:

- expected node route
- interrupt creation
- resume after approval
- rejection path
- escalation path
- invalid model output repair
- fallback response drafting
- maximum-step enforcement

### 24.3 Contract tests

- synthetic API request and response schemas
- OpenAPI compatibility
- generated TypeScript client
- R2 object schemas
- tool result envelopes

### 24.4 Integration tests

- FastAPI + PostgreSQL
- LangGraph AsyncPostgresSaver persistence and resume
- concurrent execution lease
- approval transaction
- exactly-once synthetic action
- R2/MinIO report storage
- Worker proxy streaming

### 24.5 Failure-injection tests

Test:

- OpenRouter timeout
- OpenRouter 429
- invalid structured output
- synthetic API 500
- synthetic API timeout
- malformed tool response
- Neon initial connection failure
- Langfuse unavailable
- client stream disconnect
- duplicate approval request
- duplicate execution request

### 24.6 End-to-end tests

Playwright scenarios:

1. Sign in and investigate a no-action case.
2. Investigate a duplicate-charge case, approve credit, and verify final report.
3. Reject a proposed action and verify escalation.
4. Refresh during a run and recover events.
5. Trigger model-provider failure and open replay fallback.
6. Verify public visitor cannot access protected runs.

---

## 25. CI/CD and deployment

### 25.1 Pull-request workflow

Backend:

- Ruff format and lint
- mypy
- Pytest unit and graph tests
- Alembic migration consistency check
- dependency audit
- Docker build

Frontend/Worker:

- TypeScript typecheck
- lint/format
- Vitest
- production Vite/Worker build
- Playwright against local services where practical

Shared:

- regenerate OpenAPI client and fail on uncommitted diff
- deterministic evaluation smoke suite

### 25.2 AWS deployment

Use GitHub Actions OpenID Connect for deployment credentials.

Pipeline:

1. Build the Docker image.
2. Push the immutable image tag to ECR.
3. Run Alembic migrations using the direct Neon URL.
4. Run LangGraph checkpoint setup as a one-time migration task.
5. Deploy with AWS SAM.
6. Publish a Lambda version and update the `prod` alias.
7. Verify Function URL IAM policy and streaming mode.
8. Run backend smoke tests through the Cloudflare staging Worker.
9. Promote or roll back the alias.

### 25.3 Cloudflare deployment

1. Build React assets with the Cloudflare Vite plugin.
2. Deploy Worker code and static assets with Wrangler.
3. Apply R2 bindings, rate-limit bindings, and secrets.
4. Run public route, Turnstile, proxy, stream, and replay smoke tests.

### 25.4 Environments

- local
- staging
- production

Use separate:

- WorkOS environments
- Neon branches or databases
- Langfuse projects
- R2 prefixes or buckets
- AWS Lambda functions/aliases
- Cloudflare Workers and secrets

---

## 26. Cost and abuse guardrails

- Lambda reserved concurrency set to 2.
- No provisioned concurrency.
- Exact daily run quotas in PostgreSQL.
- Turnstile required for live run creation.
- Worker burst rate limits.
- Maximum LLM and tool calls per run.
- Maximum workflow duration.
- OpenRouter key-level limit when available.
- CloudWatch log retention of 14 days.
- No token-by-token logging.
- R2 lifecycle deletion for temporary uploads.
- AWS Budget notifications at low thresholds.
- Application-level circuit breaker may temporarily disable live runs after repeated provider failures while preserving replay access.

---

## 27. Measurable business outcome

### 27.1 Primary outcome

Demonstrate reduced support-investigation effort while preserving action safety.

### 27.2 Metrics

- time from run start to review-ready case brief
- reviewer time from approval request to decision
- total case completion time
- resolution accuracy
- evidence recall
- percentage of cases completed without additional human investigation
- correct escalation rate
- unsafe or duplicate action count
- average tools and model calls per successful case

### 27.3 Manual baseline protocol

Create a simple manual-investigation mode that exposes the same synthetic CRM, billing, telemetry, incident, KB, and policy data without agent assistance.

Benchmark protocol:

1. Select 12 representative holdout cases.
2. Resolve them manually using the raw-system view.
3. Record time, selected evidence, resolution, and proposed action.
4. Resolve the same cases with ResolveOps assistance in a separate session.
5. Compare median handling time and accuracy.
6. Publish the methodology, sample size, limitations, and raw aggregate results.

Initial target, not a pre-existing claim:

- at least 60% lower median time to a review-ready resolution
- zero consequential actions without approval
- no reduction in deterministic resolution accuracy relative to manual baseline

---

## 28. Implementation order

### Phase 1: Foundation and contracts

- initialize monorepo and tooling
- define Pydantic and TypeScript contracts
- define database schemas and migrations
- generate deterministic dataset
- upload dataset to local MinIO/R2
- implement synthetic system API routes

**Exit criteria:** Dataset validates, API contracts are stable, and curated cases can be explored manually.

### Phase 2: Core graph and tools

- implement model gateway
- implement read-only tools
- implement graph state and nodes
- implement evidence verification and deterministic policy layer
- implement structured event writer
- add deterministic fake model for tests

**Exit criteria:** No-action and escalation cases complete locally with persisted checkpoints and audit events.

### Phase 3: Approval and side effects

- implement action proposals
- implement LangGraph interrupt
- implement approval endpoint
- implement resume flow
- implement exactly-once synthetic account credit
- implement rejection and stale-proposal handling

**Exit criteria:** Approval is mandatory and duplicate execution tests pass.

### Phase 4: Product interface

- implement authentication
- case inbox and custom case form
- live run timeline
- evidence and policy panels
- approval UI
- final report UI
- public replay experience

**Exit criteria:** Playwright completes the primary user journeys locally.

### Phase 5: LLMOps and evaluation

- Langfuse tracing and prompt synchronization
- evaluation runner and deterministic metrics
- baseline storage and comparison
- public metrics dashboard
- failure-injection suite

**Exit criteria:** Full development split runs, results are reproducible, and safety gates pass.

### Phase 6: Cloud deployment and hardening

- Lambda container and SAM template
- Function URL IAM and streaming
- Cloudflare SigV4 proxy
- Turnstile and rate limiting
- Neon production configuration
- R2 production storage
- CloudWatch/X-Ray dashboards and alarms
- CI/CD and rollback

**Exit criteria:** Public live demo, replays, authentication, approval, observability, and rollback are verified.

---

## 29. Definition of done

The project is complete when all of the following are true:

### Product

- Public landing page explains the workflow and business problem.
- At least 8 curated cases are available.
- At least 3 prerecorded replays work without authentication.
- Authenticated users can run a live investigation.
- Approval and rejection flows work.
- Final reports are downloadable.

### Agent

- LangGraph uses PostgreSQL persistence.
- Structured outputs validate through Pydantic.
- Tools use typed contracts and explicit timeouts.
- Evidence IDs are verified deterministically.
- Consequential actions cannot execute without approval.
- Side effects are idempotent.
- Unsupported cases escalate safely.

### Reliability

- Model, tool, database, and telemetry failure tests pass.
- Browser refresh or stream disconnection does not lose run state.
- Run and call budgets are enforced.
- No indefinite UI spinner exists.

### Security

- Lambda origin requires AWS IAM.
- WorkOS auth and RBAC are enforced.
- CSRF, origin, Turnstile, quota, and size checks are enabled.
- R2 is private.
- Secrets and hidden reasoning are not logged.
- Prompt-injection scenarios do not bypass tool or policy controls.

### Observability and LLMOps

- Langfuse traces include nodes, tools, model calls, prompt versions, usage, and scores.
- CloudWatch shows Lambda and workflow health.
- X-Ray includes key external dependency segments.
- PostgreSQL contains the authoritative audit history.
- Evaluation results can be reproduced from a dataset version and graph version.

### Quality

- All absolute safety gates pass.
- Holdout quality gates pass or failures are documented before public release.
- Manual baseline methodology and measured results are published honestly.

### Engineering presentation

- README leads with the problem, workflow, outcome, architecture, and demo.
- Architecture diagram and threat model are included.
- CI status and evaluation summary are visible.
- A short walkthrough demonstrates a successful case, an approval interrupt, and a failure/recovery path.

---

## 30. Explicitly deferred enhancements

These are valuable follow-up features but not required for MVP:

- real CRM or billing sandbox integration
- multi-organization enterprise SSO
- distinct two-person approval enforcement
- queue-based asynchronous run execution
- WebSocket transport
- semantic/vector knowledge retrieval
- model A/B routing
- automated prompt promotion
- paid-model fallback
- voice or multimodal ticket intake
- real email delivery
- advanced PII detection
- self-hosted Langfuse
- ECS/Fargate deployment target

---

## 31. Platform implementation notes

The chosen architecture relies on current platform capabilities that should be rechecked during implementation:

- LangGraph persistent checkpointers and interrupts for human-in-the-loop execution
- AWS Lambda container images, Function URLs, IAM authorization, and response streaming
- AWS Lambda Web Adapter support for FastAPI and streamed responses
- Cloudflare Workers Static Assets, Vite integration, R2 bindings, Streams, and Rate Limiting bindings
- R2 S3 compatibility and presigned URLs
- Neon pooled and direct PostgreSQL connection strings
- OpenRouter free router capability filtering for tools and structured output
- Langfuse LangChain/LangGraph tracing, prompt management, datasets, and experiments
- WorkOS AuthKit React and Python integration

Pin all dependencies and infrastructure behavior in lockfiles and automated smoke tests. Avoid relying on undocumented provider behavior.
