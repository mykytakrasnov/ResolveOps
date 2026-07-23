import { ShieldCheckIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { PageError } from "@/components/page-state";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listApprovals } from "@/lib/api";

export function ReviewPage() {
  const query = useQuery({ queryKey: ["approvals"], queryFn: listApprovals });
  if (query.error) {
    return (
      <div className="mx-auto w-full max-w-6xl px-5 py-8 lg:px-10">
        <PageError title="Approval queue unavailable" error={query.error} />
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-5 py-8 lg:px-10 lg:py-10">
      <div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <ShieldCheckIcon aria-hidden />
          Reviewer workspace
        </div>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">
          Approval queue
        </h1>
        <p className="mt-2 text-muted-foreground">
          Consequential synthetic actions paused at a durable checkpoint.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Pending reviews</CardTitle>
          <CardDescription>
            Opening a row shows the immutable proposal, policy basis, and cited
            evidence.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Case</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Risk</TableHead>
                <TableHead>Requested</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data?.items.map((item) => (
                <TableRow key={item.approval.request_id}>
                  <TableCell>
                    <Link
                      to={`/app/runs/${item.run_id}`}
                      className="font-medium underline-offset-4 hover:underline"
                    >
                      {item.case_subject}
                    </Link>
                  </TableCell>
                  <TableCell>
                    {item.approval.proposal.action_type.replaceAll("_", " ")}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">
                      {item.approval.proposal.risk_level}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {new Intl.DateTimeFormat(undefined, {
                      dateStyle: "medium",
                      timeStyle: "short",
                    }).format(new Date(item.approval.requested_at))}
                  </TableCell>
                </TableRow>
              ))}
              {query.data?.items.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="py-10 text-center text-muted-foreground"
                  >
                    No proposals are waiting for review.
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
