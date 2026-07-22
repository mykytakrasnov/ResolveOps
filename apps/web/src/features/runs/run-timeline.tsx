import {
  ArrowCounterClockwiseIcon,
  CheckCircleIcon,
  CircleIcon,
  FlagIcon,
  LightningIcon,
  RobotIcon,
  ShieldCheckIcon,
  XCircleIcon,
} from "@phosphor-icons/react";

import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationRejected,
  ConfirmationRequest,
  ConfirmationTitle,
} from "@/components/ai-elements/confirmation";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";
import { Badge } from "@/components/ui/badge";
import type { WorkflowEvent } from "@/lib/api-contracts";

import { eventSummary, eventTitle, timelineKind } from "./run-utils";

function ToolEvent({ event }: { event: WorkflowEvent }) {
  const state =
    event.event_type === "tool.failed"
      ? "output-error"
      : event.event_type === "tool.completed"
        ? "output-available"
        : "input-available";
  return (
    <Tool
      defaultOpen={event.event_type === "tool.failed"}
      aria-label="Tool event"
    >
      <ToolHeader
        type="dynamic-tool"
        toolName={eventTitle(event)}
        state={state}
      />
      <ToolContent>
        <p className="text-sm text-muted-foreground">{eventSummary(event)}</p>
      </ToolContent>
    </Tool>
  );
}

function ApprovalEvent({ event }: { event: WorkflowEvent }) {
  const isRequest = event.event_type === "approval.requested";
  const rejected = event.public_payload.decision === "reject";
  const approval = isRequest
    ? { id: String(event.sequence) }
    : { id: String(event.sequence), approved: !rejected };
  return (
    <Confirmation
      approval={approval}
      state={isRequest ? "approval-requested" : "approval-responded"}
      aria-label="Approval event"
    >
      <ShieldCheckIcon aria-hidden weight="fill" />
      <ConfirmationTitle className="flex items-center justify-between gap-3 font-medium text-foreground">
        <span>{eventSummary(event)}</span>
        <Badge variant={rejected ? "destructive" : "outline"}>
          {isRequest ? "Approval" : rejected ? "Rejected" : "Approved"}
        </Badge>
      </ConfirmationTitle>
      <ConfirmationRequest>
        <p className="text-sm text-muted-foreground">
          The workflow is paused. No action can execute before a persisted
          decision.
        </p>
      </ConfirmationRequest>
      <ConfirmationAccepted>
        <p className="text-sm text-muted-foreground">
          The persisted decision approved the proposal.
        </p>
      </ConfirmationAccepted>
      <ConfirmationRejected>
        <p className="text-sm text-muted-foreground">
          The persisted decision rejected the proposal.
        </p>
      </ConfirmationRejected>
    </Confirmation>
  );
}

const kindMeta = {
  node: { label: "Node", icon: RobotIcon },
  retry: { label: "Retry", icon: ArrowCounterClockwiseIcon },
  escalation: { label: "Escalation", icon: FlagIcon },
  completion: { label: "Completed", icon: CheckCircleIcon },
  failure: { label: "Failed", icon: XCircleIcon },
  activity: { label: "Activity", icon: LightningIcon },
} as const;

function StandardEvent({ event }: { event: WorkflowEvent }) {
  const kind = timelineKind(event.event_type);
  const meta = kindMeta[kind as keyof typeof kindMeta] ?? kindMeta.activity;
  const Icon = meta.icon;
  return (
    <article
      className="grid grid-cols-[2rem_minmax(0,1fr)] gap-3"
      aria-label={`${meta.label} event`}
    >
      <div className="relative flex justify-center">
        <span className="flex size-8 items-center justify-center rounded-full border bg-background">
          <Icon aria-hidden />
        </span>
        <span
          className="absolute top-8 bottom-[-1rem] w-px bg-border last:hidden"
          aria-hidden
        />
      </div>
      <div className="min-w-0 rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-medium capitalize">{eventTitle(event)}</h3>
          <Badge variant={kind === "failure" ? "destructive" : "outline"}>
            {meta.label}
          </Badge>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          {eventSummary(event)}
        </p>
        <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <span>#{event.sequence}</span>
          <span aria-hidden>·</span>
          <time dateTime={event.created_at}>
            {new Intl.DateTimeFormat(undefined, { timeStyle: "medium" }).format(
              new Date(event.created_at),
            )}
          </time>
        </div>
      </div>
    </article>
  );
}

export function RunTimeline({ events }: { events: WorkflowEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="flex min-h-56 flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-8 text-center">
        <CircleIcon className="text-muted-foreground" aria-hidden />
        <div>
          <p className="font-medium">Waiting for the first persisted event</p>
          <p className="mt-1 text-sm text-muted-foreground">
            The run status and elapsed time remain visible while the workflow
            starts.
          </p>
        </div>
      </div>
    );
  }

  return (
    <ol className="flex flex-col gap-4" aria-label="Workflow timeline">
      {events.map((event) => {
        const kind = timelineKind(event.event_type);
        return (
          <li key={event.sequence}>
            {kind === "tool" ? <ToolEvent event={event} /> : null}
            {kind === "approval" ? <ApprovalEvent event={event} /> : null}
            {kind !== "tool" && kind !== "approval" ? (
              <StandardEvent event={event} />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
