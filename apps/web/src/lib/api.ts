import type { z } from "zod";

import {
  approvalDecisionResponseSchema,
  approvalQueueItemSchema,
  approvalQueuePageSchema,
  createRunResponseSchema,
  publicCasePageSchema,
  publicCaseSchema,
  workflowEventPageSchema,
  workflowEventSchema,
  workflowRunSchema,
  type WorkflowEvent,
} from "@/lib/api-contracts";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const REQUEST_TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function requestJson<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const timeoutSignal = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  const signal = init?.signal
    ? AbortSignal.any([init.signal, timeoutSignal])
    : timeoutSignal;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    ...init,
    signal,
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
      error?: { code?: string; message?: string };
    } | null;
    throw new ApiError(
      body?.detail ??
        body?.error?.message ??
        `Request failed with status ${response.status}.`,
      response.status,
      body?.error?.code ?? "request_failed",
    );
  }
  return schema.parse(await response.json());
}

export async function listCases(filters?: {
  category?: string;
  difficulty?: string;
}) {
  const search = new URLSearchParams({ limit: "50" });
  if (filters?.category) search.set("category", filters.category);
  if (filters?.difficulty) search.set("difficulty", filters.difficulty);
  return requestJson(`/api/v1/cases?${search}`, publicCasePageSchema);
}

export function getCase(caseId: string) {
  return requestJson(
    `/api/v1/cases/${encodeURIComponent(caseId)}`,
    publicCaseSchema,
  );
}

export function createRun(caseId: string) {
  return requestJson("/api/v1/runs", createRunResponseSchema, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify({ case_id: caseId }),
  });
}

export function getRun(runId: string, signal?: AbortSignal) {
  return requestJson(
    `/api/v1/runs/${encodeURIComponent(runId)}`,
    workflowRunSchema,
    signal ? { signal } : undefined,
  );
}

export function getRunEvents(
  runId: string,
  afterSequence: number,
  signal?: AbortSignal,
) {
  return requestJson(
    `/api/v1/runs/${encodeURIComponent(runId)}/events?after_sequence=${afterSequence}`,
    workflowEventPageSchema,
    signal ? { signal } : undefined,
  );
}

export function listApprovals() {
  return requestJson("/api/v1/runs/approvals", approvalQueuePageSchema);
}

export function getApproval(runId: string) {
  return requestJson(
    `/api/v1/runs/${encodeURIComponent(runId)}/approval`,
    approvalQueueItemSchema,
  );
}

export function decideRun(
  runId: string,
  input: {
    proposal_id: string;
    proposal_hash: string;
    decision: "approve" | "reject";
    comment?: string;
  },
) {
  return requestJson(
    `/api/v1/runs/${encodeURIComponent(runId)}/decisions`,
    approvalDecisionResponseSchema,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify(input),
    },
  );
}

interface SseFrame {
  event: string;
  data: string;
}

export async function readSseStream(
  response: Response,
  onEvent: (event: WorkflowEvent) => void,
): Promise<void> {
  if (!response.body) {
    throw new ApiError(
      "The run stream did not include a response body.",
      502,
      "empty_stream",
    );
  }
  const decoder = new TextDecoder();
  const reader = response.body.getReader();
  let buffer = "";

  const consumeFrame = (frameText: string) => {
    const frame: SseFrame = { event: "message", data: "" };
    for (const line of frameText.split(/\r?\n/)) {
      if (line.startsWith("event:")) frame.event = line.slice(6).trim();
      if (line.startsWith("data:")) frame.data += line.slice(5).trimStart();
    }
    if (!frame.data) return;
    const payload = JSON.parse(frame.data) as Record<string, unknown>;
    onEvent(
      workflowEventSchema.parse({
        event_id: payload.event_id ?? payload.sequence,
        run_id: payload.run_id,
        sequence: payload.sequence,
        event_type: frame.event,
        node_name: payload.node_name ?? null,
        status: payload.status,
        public_payload: Object.fromEntries(
          Object.entries(payload).filter(
            ([key]) =>
              ![
                "event_id",
                "run_id",
                "sequence",
                "node_name",
                "status",
              ].includes(key),
          ),
        ),
        payload_hash: payload.payload_hash ?? "streamed",
        created_at: payload.created_at ?? new Date().toISOString(),
      }),
    );
  };

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      consumeFrame(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) {
      if (buffer.trim()) consumeFrame(buffer);
      return;
    }
  }
}

export async function executeRunStream(
  runId: string,
  signal: AbortSignal,
  onEvent: (event: WorkflowEvent) => void,
): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/runs/${encodeURIComponent(runId)}/execute`,
    {
      method: "POST",
      credentials: "include",
      signal,
      headers: {
        Accept: "text/event-stream",
        "Idempotency-Key": crypto.randomUUID(),
      },
    },
  );
  if (!response.ok) {
    throw new ApiError(
      `Run execution failed with status ${response.status}.`,
      response.status,
      "execute_failed",
    );
  }
  await readSseStream(response, onEvent);
}
