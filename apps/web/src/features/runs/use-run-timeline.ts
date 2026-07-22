import { useCallback, useEffect, useRef, useState } from "react";

import type { WorkflowEvent, WorkflowRun } from "@/lib/api-contracts";
import { executeRunStream, getRun, getRunEvents } from "@/lib/api";

export type ConnectionState =
  | "connecting"
  | "streaming"
  | "polling"
  | "connected"
  | "terminal"
  | "error";

const TERMINAL_STATUSES = new Set(["completed", "escalated", "failed"]);
const POLL_DELAY_MS = 1_500;

function mergeEvents(current: WorkflowEvent[], incoming: WorkflowEvent[]) {
  const events = new Map(current.map((event) => [event.sequence, event]));
  for (const event of incoming) events.set(event.sequence, event);
  return [...events.values()].sort(
    (left, right) => left.sequence - right.sequence,
  );
}

export function useRunTimeline(runId: string) {
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("connecting");
  const [connectionError, setConnectionError] = useState<Error | null>(null);
  const lastSequence = useRef(0);

  const addEvents = useCallback((incoming: WorkflowEvent[]) => {
    if (incoming.length === 0) return;
    lastSequence.current = Math.max(
      lastSequence.current,
      ...incoming.map((event) => event.sequence),
    );
    setEvents((current) => mergeEvents(current, incoming));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let pollTimer: ReturnType<typeof setTimeout> | undefined;

    const refreshSnapshot = async () => {
      const [nextRun, eventPage] = await Promise.all([
        getRun(runId, controller.signal),
        getRunEvents(runId, lastSequence.current, controller.signal),
      ]);
      setRun(nextRun);
      addEvents(eventPage.events);
      return nextRun;
    };

    const pollUntilTerminal = async () => {
      if (controller.signal.aborted) return;
      setConnectionState("polling");
      try {
        const nextRun = await refreshSnapshot();
        if (TERMINAL_STATUSES.has(nextRun.status)) {
          setConnectionState("terminal");
          return;
        }
        pollTimer = setTimeout(pollUntilTerminal, POLL_DELAY_MS);
      } catch (error) {
        if (controller.signal.aborted) return;
        setConnectionError(
          error instanceof Error ? error : new Error("Polling failed."),
        );
        pollTimer = setTimeout(pollUntilTerminal, POLL_DELAY_MS);
      }
    };

    const start = async () => {
      try {
        const initialRun = await refreshSnapshot();
        if (TERMINAL_STATUSES.has(initialRun.status)) {
          setConnectionState("terminal");
          return;
        }
        if (initialRun.status === "waiting_for_approval") {
          setConnectionState("connected");
          return;
        }
        setConnectionState("streaming");
        try {
          await executeRunStream(runId, controller.signal, (event) =>
            addEvents([event]),
          );
          const finalRun = await refreshSnapshot();
          setConnectionState(
            TERMINAL_STATUSES.has(finalRun.status) ? "terminal" : "connected",
          );
        } catch (error) {
          if (controller.signal.aborted) return;
          setConnectionError(
            error instanceof Error
              ? error
              : new Error("The live stream was interrupted."),
          );
          await pollUntilTerminal();
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        setConnectionError(
          error instanceof Error
            ? error
            : new Error("Run could not be loaded."),
        );
        setConnectionState("error");
      }
    };

    void start();
    return () => {
      controller.abort();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [addEvents, runId]);

  return { run, events, connectionState, connectionError };
}
