import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { AppRoutes } from "@/app";

const CASE_ID = "33333333-3333-5333-8333-333333333333";
const RUN_ID = "55555555-5555-5555-8555-555555555555";
const ORGANIZATION_ID = "11111111-1111-5111-8111-111111111111";
const USER_ID = "22222222-2222-5222-8222-222222222222";

const supportCase = {
  case_id: CASE_ID,
  split: "development",
  category: "duplicate_charge",
  difficulty: "medium",
  curated: true,
  expected_approval_required: true,
  subject: "Charged twice after plan upgrade",
  body: "We upgraded yesterday and see two completed charges for the same period.",
  customer_reference: "org_atlas_001",
  created_at: "2026-07-22T11:00:00Z",
  attachments: [],
};

function workflowRun(
  status:
    | "created"
    | "running"
    | "waiting_for_approval"
    | "completed" = "completed",
) {
  return {
    run_id: RUN_ID,
    organization_id: ORGANIZATION_ID,
    case_id: CASE_ID,
    thread_id: RUN_ID,
    initiated_by: USER_ID,
    status,
    current_node:
      status === "running"
        ? "collect_initial_evidence"
        : status === "waiting_for_approval"
          ? "approval_gate"
          : null,
    graph_version: "1.0.0",
    prompt_bundle_version: "1.0.0",
    dataset_version: "v1",
    resolved_model: null,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    execution_attempt: status === "created" ? 0 : 1,
    started_at: "2026-07-22T12:00:00Z",
    completed_at: status === "completed" ? "2026-07-22T12:00:04Z" : null,
    last_error: null,
    created_at: "2026-07-22T12:00:00Z",
  };
}

function eventPage(events: unknown[] = []) {
  return {
    events,
    after_sequence: 0,
    last_sequence: events.length,
  };
}

function completionEvent() {
  return {
    event_id: 1,
    run_id: RUN_ID,
    sequence: 1,
    event_type: "run.completed",
    node_name: null,
    status: "completed",
    public_payload: { summary: "Synthetic investigation completed." },
    payload_hash: "a".repeat(64),
    created_at: "2026-07-22T12:00:04Z",
  };
}

function approvalRequestedEvent() {
  return {
    event_id: 2,
    run_id: RUN_ID,
    sequence: 2,
    event_type: "approval.requested",
    node_name: "approval_gate",
    status: "waiting_for_approval",
    public_payload: { summary: "Approval required before synthetic credit." },
    payload_hash: "b".repeat(64),
    created_at: "2026-07-22T12:00:05Z",
  };
}

function approvalItem(decision: "approve" | "reject" | null = null) {
  return {
    run_id: RUN_ID,
    case_id: CASE_ID,
    case_subject: supportCase.subject,
    approval: {
      request_id: "66666666-6666-5666-8666-666666666666",
      proposal: {
        proposal_id: "77777777-7777-5777-8777-777777777777",
        run_id: RUN_ID,
        action_type: "apply_account_credit",
        target_reference: "org_atlas_001",
        canonical_parameters: {
          account_id: "org_atlas_001",
          amount_cents: 4900,
          currency: "USD",
        },
        proposal_hash: "a".repeat(64),
        risk_level: "R2",
        policy_key: "billing_duplicate_credit",
        policy_version: "3.0",
        status:
          decision === "approve"
            ? "approved"
            : decision === "reject"
              ? "rejected"
              : "pending_approval",
        idempotency_key: `resolveops:${RUN_ID}:apply_account_credit:v1`,
        created_at: "2026-07-22T12:00:05Z",
      },
      requested_by: USER_ID,
      requested_at: "2026-07-22T12:00:05Z",
      decision: decision
        ? {
            proposal_id: "77777777-7777-5777-8777-777777777777",
            proposal_hash: "a".repeat(64),
            decision,
            comment: decision === "reject" ? "Needs specialist review." : null,
            decided_by: USER_ID,
            decided_at: "2026-07-22T12:01:00Z",
          }
        : null,
    },
    cited_evidence: [
      {
        evidence_id: "payment_001",
        source_system: "billing",
        object_type: "payment_attempt",
        object_id: "pay_001",
        fact: "Two synthetic payments succeeded for one invoice period.",
      },
    ],
  };
}

function reportMetadata() {
  return {
    run_id: RUN_ID,
    status: "completed",
    internal_trace_identifiers: {
      workflow_run_id: RUN_ID,
      langgraph_thread_id: RUN_ID,
    },
    artifacts: [
      {
        kind: "json_report",
        mime_type: "application/json",
        sha256: "c".repeat(64),
        size_bytes: 512,
        download_url: `/api/v1/runs/${RUN_ID}/report/json_report`,
        created_at: "2026-07-22T12:00:04Z",
      },
      {
        kind: "markdown_brief",
        mime_type: "text/markdown; charset=utf-8",
        sha256: "d".repeat(64),
        size_bytes: 256,
        download_url: `/api/v1/runs/${RUN_ID}/report/markdown_brief`,
        created_at: "2026-07-22T12:00:04Z",
      },
      {
        kind: "customer_response",
        mime_type: "text/plain; charset=utf-8",
        sha256: "e".repeat(64),
        size_bytes: 128,
        download_url: `/api/v1/runs/${RUN_ID}/report/customer_response`,
        created_at: "2026-07-22T12:00:04Z",
      },
    ],
  };
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderRoute(route: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("case workflow surface", () => {
  test("review queue opens a persistent proposal and requires a rejection comment", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/v1/runs/approvals")) {
          return jsonResponse({ items: [approvalItem()] });
        }
        if (url.endsWith(`/runs/${RUN_ID}`)) {
          return jsonResponse(workflowRun("waiting_for_approval"));
        }
        if (url.endsWith(`/runs/${RUN_ID}/approval`)) {
          return jsonResponse(approvalItem());
        }
        if (url.endsWith(`/cases/${CASE_ID}`)) return jsonResponse(supportCase);
        if (url.includes(`/runs/${RUN_ID}/events`)) {
          return jsonResponse(eventPage([approvalRequestedEvent()]));
        }
        if (
          url.endsWith(`/runs/${RUN_ID}/decisions`) &&
          init?.method === "POST"
        ) {
          return jsonResponse({
            run_id: RUN_ID,
            approval: approvalItem("reject").approval,
            idempotent_replay: false,
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    renderRoute("/app/review");

    await userEvent.click(
      await screen.findByRole("link", { name: supportCase.subject }),
    );
    expect(await screen.findByText("Action proposal")).toBeInTheDocument();
    expect(screen.getByText("$49.00")).toBeInTheDocument();
    expect(screen.getAllByText("org_atlas_001").length).toBeGreaterThan(0);
    expect(
      screen.getByText("billing_duplicate_credit · v3.0"),
    ).toBeInTheDocument();
    expect(screen.getByText("payment_001")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    const confirm = screen.getByRole("button", { name: "Reject proposal" });
    expect(confirm).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText("Review comment (required)"),
      "Needs specialist review.",
    );
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);

    const decisionCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith(`/runs/${RUN_ID}/decisions`) &&
        init?.method === "POST",
    );
    expect(JSON.parse(String(decisionCall?.[1]?.body))).toMatchObject({
      proposal_id: approvalItem().approval.proposal.proposal_id,
      proposal_hash: "a".repeat(64),
      decision: "reject",
      comment: "Needs specialist review.",
    });
  });

  test("browser refresh restores an approved proposal without offering another decision", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith(`/runs/${RUN_ID}`)) {
          return jsonResponse(workflowRun("waiting_for_approval"));
        }
        if (url.endsWith(`/runs/${RUN_ID}/approval`)) {
          return jsonResponse(approvalItem("approve"));
        }
        if (url.endsWith(`/cases/${CASE_ID}`)) return jsonResponse(supportCase);
        if (url.includes(`/runs/${RUN_ID}/events`)) {
          return jsonResponse(eventPage([approvalRequestedEvent()]));
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderRoute(`/app/runs/${RUN_ID}`);

    expect(
      await screen.findByText("Approved · awaiting execution"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Reject" }),
    ).not.toBeInTheDocument();
  });

  test("selecting an inbox case opens its detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes(`/cases/${CASE_ID}`)) return jsonResponse(supportCase);
        if (url.includes("/cases?")) {
          return jsonResponse({
            items: [supportCase],
            page: { limit: 50, next_cursor: null },
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderRoute("/app/cases");

    await userEvent.click(
      await screen.findByRole("link", { name: supportCase.subject }),
    );

    expect(
      await screen.findByRole("heading", { name: supportCase.subject }),
    ).toBeInTheDocument();
    expect(screen.getByText(supportCase.body)).toBeInTheDocument();
    expect(screen.getByText("Approval expected")).toBeInTheDocument();
  });

  test("replay gallery opens a prerecorded timeline through public static endpoints", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/public/replays?")) {
          return jsonResponse({
            items: [supportCase],
            page: { limit: 50, next_cursor: null },
          });
        }
        if (url.endsWith(`/api/v1/public/replays/${CASE_ID}`)) {
          return jsonResponse({
            case: supportCase,
            events: [completionEvent()],
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderRoute("/replays");

    await userEvent.click(
      await screen.findByRole("button", { name: "View replay" }),
    );

    expect(await screen.findByText("Prerecorded replay")).toBeInTheDocument();
    expect(
      screen.getByText("Synthetic investigation completed."),
    ).toBeInTheDocument();
  });

  test("completed run offers authenticated report downloads with integrity metadata", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith(`/runs/${RUN_ID}`)) {
          return jsonResponse(workflowRun());
        }
        if (url.endsWith(`/runs/${RUN_ID}/report`)) {
          return jsonResponse(reportMetadata());
        }
        if (url.endsWith(`/cases/${CASE_ID}`)) return jsonResponse(supportCase);
        if (url.includes(`/runs/${RUN_ID}/events`)) {
          return jsonResponse(eventPage([completionEvent()]));
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderRoute(`/app/runs/${RUN_ID}`);

    expect(await screen.findByText("Report downloads")).toBeInTheDocument();
    expect(await screen.findByText("JSON report")).toBeInTheDocument();
    expect(screen.getByText("Markdown case brief")).toBeInTheDocument();
    expect(screen.getByText("Customer response")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /^Download / })).toHaveLength(3);
    expect(screen.getByText(`SHA-256 ${"c".repeat(64)}`)).toBeInTheDocument();
  });

  test("starting an investigation creates a run and navigates to its page", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith(`/cases/${CASE_ID}`)) return jsonResponse(supportCase);
        if (url.endsWith("/api/v1/runs") && init?.method === "POST") {
          return jsonResponse(
            {
              run_id: RUN_ID,
              status: "created",
              graph_version: "1.0.0",
              created_at: "2026-07-22T12:00:00Z",
            },
            201,
          );
        }
        if (url.endsWith(`/runs/${RUN_ID}`)) return jsonResponse(workflowRun());
        if (url.includes(`/runs/${RUN_ID}/events`)) {
          return jsonResponse(eventPage([completionEvent()]));
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    renderRoute(`/app/cases/${CASE_ID}`);

    await userEvent.click(
      await screen.findByRole("button", { name: "Investigate case" }),
    );

    expect(await screen.findByText(`RUN ${RUN_ID}`)).toBeInTheDocument();
    const createCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/api/v1/runs") && init?.method === "POST",
    );
    expect(createCall).toBeDefined();
    expect(createCall?.[1]?.headers).toMatchObject({
      "Idempotency-Key": expect.any(String),
    });
  });

  test("an interrupted stream recovers from persisted events", async () => {
    let runReads = 0;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith(`/cases/${CASE_ID}`)) return jsonResponse(supportCase);
        if (url.endsWith(`/runs/${RUN_ID}`)) {
          runReads += 1;
          return jsonResponse(
            workflowRun(runReads === 1 ? "created" : "completed"),
          );
        }
        if (url.includes(`/runs/${RUN_ID}/events`)) {
          return jsonResponse(
            eventPage(runReads === 1 ? [] : [completionEvent()]),
          );
        }
        if (
          url.endsWith(`/runs/${RUN_ID}/execute`) &&
          init?.method === "POST"
        ) {
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.error(new Error("simulated disconnect"));
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    renderRoute(`/app/runs/${RUN_ID}`);

    expect(await screen.findByText("Timeline recovered")).toBeInTheDocument();
    expect(
      screen.getByText("Synthetic investigation completed."),
    ).toBeInTheDocument();
    expect(screen.getByText("terminal")).toBeInTheDocument();
  });

  test("an interrupted stream settles when polling finds approval is required", async () => {
    let runReads = 0;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith(`/cases/${CASE_ID}`)) return jsonResponse(supportCase);
        if (url.endsWith(`/runs/${RUN_ID}`)) {
          runReads += 1;
          return jsonResponse(
            workflowRun(runReads === 1 ? "created" : "waiting_for_approval"),
          );
        }
        if (url.includes(`/runs/${RUN_ID}/events`)) {
          return jsonResponse(
            eventPage(runReads === 1 ? [] : [approvalRequestedEvent()]),
          );
        }
        if (
          url.endsWith(`/runs/${RUN_ID}/execute`) &&
          init?.method === "POST"
        ) {
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.error(new Error("simulated disconnect"));
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    renderRoute(`/app/runs/${RUN_ID}`);

    expect(
      await screen.findByText("Approval required before synthetic credit."),
    ).toBeInTheDocument();
    expect(screen.getByText("connected")).toBeInTheDocument();
  });

  test("a running stream always shows node and elapsed state instead of an indefinite spinner", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith(`/cases/${CASE_ID}`)) return jsonResponse(supportCase);
        if (url.endsWith(`/runs/${RUN_ID}`))
          return jsonResponse(workflowRun("running"));
        if (url.includes(`/runs/${RUN_ID}/events`))
          return jsonResponse(eventPage());
        if (
          url.endsWith(`/runs/${RUN_ID}/execute`) &&
          init?.method === "POST"
        ) {
          return new Response("", {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    renderRoute(`/app/runs/${RUN_ID}`);

    expect(
      await screen.findByText("collect_initial_evidence"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/Elapsed time/)).toBeInTheDocument();
    expect(
      screen.getByText("Waiting for the first persisted event"),
    ).toBeInTheDocument();
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
