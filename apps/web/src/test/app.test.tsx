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
