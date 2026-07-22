import { render, screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { WorkflowEvent, WorkflowEventType } from "@/lib/api-contracts";

import { RunTimeline } from "./run-timeline";

const RUN_ID = "55555555-5555-5555-8555-555555555555";

function event(
  sequence: number,
  eventType: WorkflowEventType,
  options?: {
    nodeName?: string;
    publicPayload?: Record<string, unknown>;
    status?: string;
  },
): WorkflowEvent {
  return {
    event_id: sequence,
    run_id: RUN_ID,
    sequence,
    event_type: eventType,
    node_name: options?.nodeName ?? null,
    status: options?.status ?? "completed",
    public_payload: options?.publicPayload ?? {},
    payload_hash: "a".repeat(64),
    created_at: `2026-07-22T12:00:${sequence.toString().padStart(2, "0")}Z`,
  };
}

describe("workflow timeline event families", () => {
  test("renders every required state with distinct accessible semantics", () => {
    render(
      <RunTimeline
        events={[
          event(1, "node.started", {
            nodeName: "collect_initial_evidence",
            publicPayload: { summary: "Evidence collection started." },
            status: "running",
          }),
          event(2, "tool.failed", {
            publicPayload: {
              tool: "search_knowledge_base",
              summary: "The bounded tool call failed safely.",
            },
            status: "failed",
          }),
          event(3, "model.retry", {
            publicPayload: { summary: "Retrying structured model output." },
            status: "retrying",
          }),
          event(4, "approval.requested", {
            publicPayload: {
              summary: "Approval required before synthetic credit.",
            },
            status: "waiting_for_approval",
          }),
          event(5, "run.escalated", {
            publicPayload: { summary: "Escalated for manual review." },
            status: "escalated",
          }),
          event(6, "run.completed", {
            publicPayload: { summary: "Investigation completed." },
          }),
          event(7, "run.failed", {
            publicPayload: { summary: "Investigation failed safely." },
            status: "failed",
          }),
        ]}
      />,
    );

    const timeline = screen.getByRole("list", { name: "Workflow timeline" });
    expect(within(timeline).getAllByRole("listitem")).toHaveLength(7);

    const node = screen.getByLabelText("Node event");
    const tool = screen.getByLabelText("Tool event");
    const retry = screen.getByLabelText("Retry event");
    const approval = screen.getByLabelText("Approval event");
    const escalation = screen.getByLabelText("Escalation event");
    const completion = screen.getByLabelText("Completed event");
    const failure = screen.getByLabelText("Failed event");

    expect(node).toHaveTextContent("collect initial evidence");
    expect(tool).toHaveTextContent("search knowledge base");
    expect(tool).toHaveTextContent("Error");
    expect(retry).toHaveTextContent("Retry");
    expect(approval).toHaveTextContent("Approval");
    expect(approval).toHaveTextContent("The workflow is paused");
    expect(escalation).toHaveTextContent("Escalation");
    expect(completion).toHaveTextContent("Completed");
    expect(failure).toHaveTextContent("Failed");
  });
});
