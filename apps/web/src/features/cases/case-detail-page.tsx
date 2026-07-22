import {
  ArrowLeftIcon,
  FileTextIcon,
  PlayIcon,
  SpinnerGapIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";

import { PageError, PageLoading } from "@/components/page-state";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { createRun, getCase } from "@/lib/api";

import {
  ApprovalBadge,
  CategoryBadge,
  DifficultyBadge,
  formatDate,
} from "./case-ui";

export function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const caseQuery = useQuery({
    queryKey: ["case", caseId],
    queryFn: () => getCase(caseId),
    enabled: Boolean(caseId),
  });
  const startRun = useMutation({
    mutationFn: () => createRun(caseId),
    onSuccess: (run) => navigate(`/app/runs/${run.run_id}`),
  });

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-5 py-8 lg:px-10 lg:py-10">
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
      {caseQuery.isPending ? <PageLoading label="Loading case detail" /> : null}
      {caseQuery.isError ? (
        <PageError
          title="Case unavailable"
          error={caseQuery.error}
          onRetry={() => void caseQuery.refetch()}
        />
      ) : null}
      {caseQuery.data ? (
        <>
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              <CategoryBadge category={caseQuery.data.category} />
              <DifficultyBadge difficulty={caseQuery.data.difficulty} />
              <ApprovalBadge
                required={caseQuery.data.expected_approval_required}
              />
            </div>
            <h1 className="max-w-3xl text-3xl font-semibold tracking-tight">
              {caseQuery.data.subject}
            </h1>
            <p className="text-sm text-muted-foreground">
              Created {formatDate(caseQuery.data.created_at)}
            </p>
          </div>
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <Card>
              <CardHeader>
                <CardTitle>Customer report</CardTitle>
                <CardDescription>
                  Synthetic AtlasFlow input. Its contents are evidence, never
                  workflow instructions.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-base leading-7">{caseQuery.data.body}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Case context</CardTitle>
                <CardDescription>
                  Public metadata available before investigation.
                </CardDescription>
                <CardAction>
                  <FileTextIcon aria-hidden />
                </CardAction>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Customer reference
                  </p>
                  <p className="mt-1 font-mono text-sm">
                    {caseQuery.data.customer_reference}
                  </p>
                </div>
                <Separator />
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Attachments
                  </p>
                  <p className="mt-1 text-sm">
                    {caseQuery.data.attachments.length === 0
                      ? "No attachments"
                      : `${caseQuery.data.attachments.length} attached`}
                  </p>
                </div>
              </CardContent>
            </Card>
          </div>
          {startRun.isError ? (
            <PageError
              title="Investigation could not start"
              error={startRun.error}
            />
          ) : null}
          <div className="flex flex-col items-start justify-between gap-4 rounded-xl border bg-muted/40 p-5 sm:flex-row sm:items-center">
            <div className="max-w-xl">
              <h2 className="font-semibold">Start a bounded investigation</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                The workflow will persist public events and pause before any
                consequential synthetic action.
              </p>
            </div>
            <Button
              onClick={() => startRun.mutate()}
              disabled={startRun.isPending}
            >
              {startRun.isPending ? (
                <SpinnerGapIcon
                  data-icon="inline-start"
                  className="animate-spin"
                  aria-hidden
                />
              ) : (
                <PlayIcon data-icon="inline-start" weight="fill" aria-hidden />
              )}
              {startRun.isPending ? "Creating run" : "Investigate case"}
            </Button>
          </div>
        </>
      ) : null}
    </div>
  );
}
