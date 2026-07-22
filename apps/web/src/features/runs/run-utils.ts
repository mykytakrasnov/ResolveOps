import type { WorkflowEvent, WorkflowEventType } from "@/lib/api-contracts";

export type TimelineKind =
  | "node"
  | "tool"
  | "retry"
  | "approval"
  | "escalation"
  | "completion"
  | "failure"
  | "activity";

export function timelineKind(eventType: WorkflowEventType): TimelineKind {
  if (eventType.startsWith("node.")) return "node";
  if (eventType.startsWith("tool.")) return "tool";
  if (eventType === "model.retry" || eventType === "model.fallback")
    return "retry";
  if (eventType.startsWith("approval.")) return "approval";
  if (eventType === "run.escalated") return "escalation";
  if (eventType === "run.completed") return "completion";
  if (eventType === "run.failed") return "failure";
  return "activity";
}

export function eventSummary(event: WorkflowEvent): string {
  const summary = event.public_payload.summary;
  if (typeof summary === "string") return summary;
  if (event.event_type === "run.started") return "Investigation started.";
  if (event.event_type === "run.completed") return "Investigation completed.";
  if (event.event_type === "run.failed") return "Investigation failed safely.";
  return event.event_type.replaceAll(".", " ");
}

export function eventTitle(event: WorkflowEvent): string {
  const tool = event.public_payload.tool;
  if (event.event_type.startsWith("tool.") && typeof tool === "string") {
    return tool.replaceAll("_", " ");
  }
  if (event.node_name) return event.node_name.replaceAll("_", " ");
  return event.event_type.replaceAll(".", " ");
}

export function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}
