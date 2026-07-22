import {
  ArrowLeftIcon,
  ArrowsClockwiseIcon,
  ClockIcon,
  PlugsConnectedIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { PageError } from "@/components/page-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getCase } from "@/lib/api";

import { ApprovalBadge, CategoryBadge } from "../cases/case-ui";
import { RunTimeline } from "./run-timeline";
import { formatDuration } from "./run-utils";
import { useRunTimeline } from "./use-run-timeline";

function useElapsed(startedAt?: string | null, completedAt?: string | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt || completedAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [completedAt, startedAt]);
  if (!startedAt) return "0:00";
  return formatDuration(
    new Date(completedAt ?? now).getTime() - new Date(startedAt).getTime(),
  );
}

function statusLabel(status: string) {
  return status.replaceAll("_", " ");
}

export function RunPage() {
  const { runId = "" } = useParams();
  const { run, events, connectionState, connectionError } =
    useRunTimeline(runId);
  const caseQuery = useQuery({
    queryKey: ["case", run?.case_id],
    queryFn: () => getCase(run?.case_id ?? ""),
    enabled: Boolean(run?.case_id),
  });
  const elapsed = useElapsed(
    run?.started_at ?? run?.created_at,
    run?.completed_at,
  );

  if (connectionState === "error" && !run && connectionError) {
    return (
      <div className="mx-auto w-full max-w-5xl px-5 py-8 lg:px-10 lg:py-10">
        <PageError title="Run unavailable" error={connectionError} />
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-5 py-8 lg:px-10 lg:py-10">
      <Button
        variant="ghost"
        size="sm"
        render={<Link to="/app/cases" />}
        nativeButton={false}
        className="self-start"
      >
        <ArrowLeftIcon data-icon="inline-start" aria-hidden />
        Back to cases
      </Button>

      <div className="flex flex-col justify-between gap-5 border-b pb-6 lg:flex-row lg:items-start">
        <div className="min-w-0">
          <p className="font-mono text-xs text-muted-foreground">RUN {runId}</p>
          {caseQuery.data ? (
            <h1 className="mt-2 max-w-3xl text-3xl font-semibold tracking-tight">
              {caseQuery.data.subject}
            </h1>
          ) : (
            <Skeleton className="mt-3 h-9 w-80 max-w-full" />
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge
              variant={run?.status === "failed" ? "destructive" : "secondary"}
            >
              {run ? statusLabel(run.status) : "loading status"}
            </Badge>
            {run?.current_node ? (
              <Badge variant="outline">{run.current_node}</Badge>
            ) : null}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <div className="rounded-lg border bg-card px-4 py-3">
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <ClockIcon aria-hidden /> Elapsed
            </p>
            <output
              className="mt-1 font-mono text-lg font-medium"
              aria-label={`Elapsed time ${elapsed}`}
            >
              {elapsed}
            </output>
          </div>
          <div className="rounded-lg border bg-card px-4 py-3">
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <PlugsConnectedIcon aria-hidden /> Connection
            </p>
            <p className="mt-1 text-sm font-medium capitalize">
              {connectionState}
            </p>
          </div>
          <div className="col-span-2 rounded-lg border bg-card px-4 py-3 sm:col-span-1">
            <p className="text-xs text-muted-foreground">Graph</p>
            <p className="mt-1 font-mono text-sm font-medium">
              {run?.graph_version ?? "—"}
            </p>
          </div>
        </div>
      </div>

      {connectionState === "polling" ? (
        <Alert>
          <ArrowsClockwiseIcon aria-hidden />
          <AlertTitle>Live stream interrupted</AlertTitle>
          <AlertDescription>
            Reconnecting through persisted event polling. The workflow continues
            independently.
          </AlertDescription>
        </Alert>
      ) : null}
      {connectionError && connectionState === "terminal" ? (
        <Alert>
          <ArrowsClockwiseIcon aria-hidden />
          <AlertTitle>Timeline recovered</AlertTitle>
          <AlertDescription>
            The live connection ended early; persisted run state and events were
            recovered.
          </AlertDescription>
        </Alert>
      ) : null}
      {run?.last_error ? (
        <Alert variant="destructive">
          <WarningCircleIcon aria-hidden />
          <AlertTitle>{run.last_error.code}</AlertTitle>
          <AlertDescription className="flex flex-col gap-1">
            <span>{run.last_error.message}</span>
            <span className="text-xs">
              {run.last_error.node_name
                ? `Node: ${run.last_error.node_name}. `
                : ""}
              {run.last_error.recoverable
                ? "This run can be retried safely."
                : "Manual review is required."}
            </span>
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid min-h-0 gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(18rem,0.8fr)]">
        <section className="min-w-0">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold">Workflow timeline</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Public, persisted events only. Hidden model reasoning is never
                displayed.
              </p>
            </div>
            <Badge variant="outline">{events.length} events</Badge>
          </div>
          <RunTimeline events={events} />
        </section>
        <aside className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Case context</CardTitle>
              <CardDescription>
                Input metadata associated with this workflow run.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              {caseQuery.data ? (
                <>
                  <div className="flex flex-wrap gap-2">
                    <CategoryBadge category={caseQuery.data.category} />
                    <ApprovalBadge
                      required={caseQuery.data.expected_approval_required}
                    />
                  </div>
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Customer reference
                    </p>
                    <p className="mt-1 font-mono text-sm">
                      {caseQuery.data.customer_reference}
                    </p>
                  </div>
                  <p className="text-sm leading-6 text-muted-foreground">
                    {caseQuery.data.body}
                  </p>
                </>
              ) : (
                <Skeleton className="h-32 w-full" />
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Execution details</CardTitle>
              <CardDescription>
                Safe public metadata for this run.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-sm">
                <dt className="text-muted-foreground">Attempt</dt>
                <dd className="text-right font-medium">
                  {run?.execution_attempt ?? 0}
                </dd>
                <dt className="text-muted-foreground">Dataset</dt>
                <dd className="text-right font-mono">
                  {run?.dataset_version ?? "—"}
                </dd>
                <dt className="text-muted-foreground">Model</dt>
                <dd className="truncate text-right font-mono">
                  {run?.resolved_model ?? "Not selected"}
                </dd>
                <dt className="text-muted-foreground">Prompt bundle</dt>
                <dd className="text-right font-mono">
                  {run?.prompt_bundle_version ?? "—"}
                </dd>
              </dl>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
