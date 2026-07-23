import { DownloadSimpleIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";

import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getRunReport } from "@/lib/api";

const labels = {
  json_report: "JSON report",
  markdown_brief: "Markdown case brief",
  customer_response: "Customer response",
  public_events: "Public event summary",
} as const;

function formatBytes(value: number) {
  return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KB`;
}

export function ReportDownloads({ runId }: { runId: string }) {
  const reportQuery = useQuery({
    queryKey: ["run-report", runId],
    queryFn: () => getRunReport(runId),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Report downloads</CardTitle>
        <CardDescription>
          Authenticated, integrity-checked private artifacts.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {reportQuery.isPending ? <Skeleton className="h-24 w-full" /> : null}
        {reportQuery.error ? (
          <p className="text-sm text-destructive">
            Report metadata could not be loaded.
          </p>
        ) : null}
        {reportQuery.data?.artifacts.map((artifact) => (
          <div
            key={artifact.kind}
            className="flex flex-col gap-2 rounded-lg border p-3"
          >
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium">{labels[artifact.kind]}</p>
              <span className="text-xs text-muted-foreground">
                {formatBytes(artifact.size_bytes)}
              </span>
            </div>
            <p
              className="truncate font-mono text-xs text-muted-foreground"
              title={artifact.sha256}
            >
              SHA-256 {artifact.sha256}
            </p>
            <a
              href={artifact.download_url}
              download
              className={buttonVariants({ variant: "outline", size: "sm" })}
              aria-label={`Download ${labels[artifact.kind]}`}
            >
              <DownloadSimpleIcon data-icon="inline-start" aria-hidden />
              Download
            </a>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
