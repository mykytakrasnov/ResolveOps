import { ArrowLeftIcon, InfoIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";

import { PageError, PageLoading } from "@/components/page-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CategoryBadge, DifficultyBadge } from "@/features/cases/case-ui";
import { RunTimeline } from "@/features/runs/run-timeline";
import { getPublicReplay } from "@/lib/api";

export function ReplayDetailPage() {
  const { caseId = "" } = useParams();
  const replayQuery = useQuery({
    queryKey: ["public-replay", caseId],
    queryFn: () => getPublicReplay(caseId),
    enabled: Boolean(caseId),
  });

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-5 py-8 lg:px-10 lg:py-10">
      <Button
        variant="ghost"
        size="sm"
        render={<Link to="/replays" />}
        nativeButton={false}
        className="self-start"
      >
        <ArrowLeftIcon data-icon="inline-start" aria-hidden />
        Back to replay gallery
      </Button>

      <Alert>
        <InfoIcon aria-hidden />
        <AlertTitle>Prerecorded replay</AlertTitle>
        <AlertDescription>
          This sanitized timeline is a stored synthetic artifact. It does not
          invoke live inference or depend on the authenticated run service.
        </AlertDescription>
      </Alert>

      {replayQuery.isPending ? (
        <PageLoading label="Loading prerecorded timeline" />
      ) : null}
      {replayQuery.error ? (
        <PageError
          title="Replay unavailable"
          error={replayQuery.error}
          onRetry={() => void replayQuery.refetch()}
        />
      ) : null}
      {replayQuery.data ? (
        <>
          <header className="border-b pb-6">
            <div className="flex flex-wrap gap-2">
              <CategoryBadge category={replayQuery.data.case.category} />
              <DifficultyBadge difficulty={replayQuery.data.case.difficulty} />
              <Badge variant="outline">
                {replayQuery.data.events.length} events
              </Badge>
            </div>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight">
              {replayQuery.data.case.subject}
            </h1>
            <p className="mt-2 text-muted-foreground">
              {replayQuery.data.case.body}
            </p>
          </header>
          <section>
            <h2 className="mb-4 text-xl font-semibold">Workflow timeline</h2>
            <RunTimeline events={replayQuery.data.events} />
          </section>
        </>
      ) : null}
    </main>
  );
}
