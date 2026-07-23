import { ArrowRightIcon, FilmStripIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { PageError, PageLoading } from "@/components/page-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApprovalBadge,
  CategoryBadge,
  DifficultyBadge,
} from "@/features/cases/case-ui";
import { listPublicReplays } from "@/lib/api";

export function ReplayGalleryPage() {
  const replayQuery = useQuery({
    queryKey: ["public-replays"],
    queryFn: listPublicReplays,
  });

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-8 px-5 py-8 lg:px-10 lg:py-10">
      <header className="border-b pb-6">
        <div className="flex items-center gap-2">
          <FilmStripIcon aria-hidden />
          <Badge variant="outline">Prerecorded</Badge>
        </div>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">
          Replay gallery
        </h1>
        <p className="mt-2 max-w-2xl text-muted-foreground">
          Inspect sanitized AtlasFlow workflow timelines without live inference,
          authentication, or backend run dependencies.
        </p>
      </header>

      {replayQuery.isPending ? (
        <PageLoading label="Loading prerecorded replays" />
      ) : null}
      {replayQuery.error ? (
        <PageError
          title="Replays unavailable"
          error={replayQuery.error}
          onRetry={() => void replayQuery.refetch()}
        />
      ) : null}
      {replayQuery.data ? (
        <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {replayQuery.data.items.map((replayCase) => (
            <Card key={replayCase.case_id} className="flex flex-col">
              <CardHeader>
                <div className="mb-2 flex flex-wrap gap-2">
                  <CategoryBadge category={replayCase.category} />
                  <DifficultyBadge difficulty={replayCase.difficulty} />
                </div>
                <CardTitle>{replayCase.subject}</CardTitle>
                <CardDescription className="line-clamp-3">
                  {replayCase.body}
                </CardDescription>
              </CardHeader>
              <CardContent className="mt-auto">
                <ApprovalBadge
                  required={replayCase.expected_approval_required}
                />
              </CardContent>
              <CardFooter>
                <Button
                  variant="outline"
                  render={<Link to={`/replays/${replayCase.case_id}`} />}
                  nativeButton={false}
                  className="w-full"
                >
                  View replay
                  <ArrowRightIcon data-icon="inline-end" aria-hidden />
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      ) : null}
    </main>
  );
}
