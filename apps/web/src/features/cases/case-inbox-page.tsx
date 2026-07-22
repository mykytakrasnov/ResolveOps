import {
  CaretRightIcon,
  FunnelSimpleIcon,
  TrayIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { PageError, PageLoading } from "@/components/page-state";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listCases } from "@/lib/api";

import {
  ApprovalBadge,
  CategoryBadge,
  DifficultyBadge,
  formatDate,
  humanize,
} from "./case-ui";

export function CaseInboxPage() {
  const [category, setCategory] = useState("all");
  const [difficulty, setDifficulty] = useState("all");
  const [approval, setApproval] = useState("all");
  const casesQuery = useQuery({
    queryKey: ["cases", category, difficulty],
    queryFn: () =>
      listCases({
        ...(category === "all" ? {} : { category }),
        ...(difficulty === "all" ? {} : { difficulty }),
      }),
  });
  const cases = useMemo(
    () =>
      (casesQuery.data?.items ?? []).filter((supportCase) => {
        if (approval === "required")
          return supportCase.expected_approval_required;
        if (approval === "not-required")
          return !supportCase.expected_approval_required;
        return true;
      }),
    [approval, casesQuery.data],
  );

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-5 py-8 lg:px-10 lg:py-10">
      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div className="flex flex-col gap-2">
          <h1 className="text-3xl font-semibold tracking-tight">Case inbox</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Curated AtlasFlow cases use synthetic data and deterministic
            workflow guardrails.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className="size-2 rounded-full bg-primary" aria-hidden />
          {casesQuery.data
            ? `${cases.length} cases shown`
            : "Loading case count"}
        </div>
      </div>

      <fieldset className="flex flex-wrap items-center gap-2">
        <legend className="sr-only">Case filters</legend>
        <FunnelSimpleIcon className="text-muted-foreground" aria-hidden />
        <Select
          value={category}
          onValueChange={(value) => setCategory(value ?? "all")}
        >
          <SelectTrigger aria-label="Filter by category">
            <SelectValue>
              {category === "all" ? "All categories" : humanize(category)}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="all">All categories</SelectItem>
              <SelectItem value="duplicate_charge">Duplicate charge</SelectItem>
              <SelectItem value="billing">Billing</SelectItem>
              <SelectItem value="access">Access</SelectItem>
              <SelectItem value="incident">Incident</SelectItem>
              <SelectItem value="product_issue">Product issue</SelectItem>
              <SelectItem value="plan_limit">Plan limit</SelectItem>
              <SelectItem value="unknown">Unknown</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        <Select
          value={difficulty}
          onValueChange={(value) => setDifficulty(value ?? "all")}
        >
          <SelectTrigger aria-label="Filter by difficulty">
            <SelectValue>
              {difficulty === "all" ? "All difficulties" : humanize(difficulty)}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="all">All difficulties</SelectItem>
              <SelectItem value="easy">Easy</SelectItem>
              <SelectItem value="medium">Medium</SelectItem>
              <SelectItem value="hard">Hard</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        <Select
          value={approval}
          onValueChange={(value) => setApproval(value ?? "all")}
        >
          <SelectTrigger aria-label="Filter by approval expectation">
            <SelectValue>
              {approval === "all"
                ? "All approval paths"
                : approval === "required"
                  ? "Approval expected"
                  : "No approval expected"}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="all">All approval paths</SelectItem>
              <SelectItem value="required">Approval expected</SelectItem>
              <SelectItem value="not-required">No approval expected</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
      </fieldset>

      {casesQuery.isPending ? (
        <PageLoading label="Loading curated cases" />
      ) : null}
      {casesQuery.isError ? (
        <PageError
          title="Case inbox unavailable"
          error={casesQuery.error}
          onRetry={() => void casesQuery.refetch()}
        />
      ) : null}
      {casesQuery.isSuccess && cases.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <TrayIcon aria-hidden />
            </EmptyMedia>
            <EmptyTitle>No cases match these filters</EmptyTitle>
            <EmptyDescription>
              Adjust a filter to return to the curated inbox.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              variant="outline"
              onClick={() => {
                setCategory("all");
                setDifficulty("all");
                setApproval("all");
              }}
            >
              Clear filters
            </Button>
          </EmptyContent>
        </Empty>
      ) : null}
      {cases.length > 0 ? (
        <div className="overflow-hidden rounded-xl border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Case</TableHead>
                <TableHead className="hidden md:table-cell">Category</TableHead>
                <TableHead className="hidden md:table-cell">
                  Difficulty
                </TableHead>
                <TableHead className="hidden md:table-cell">Approval</TableHead>
                <TableHead className="hidden md:table-cell">Created</TableHead>
                <TableHead>
                  <span className="sr-only">Open case</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {cases.map((supportCase) => (
                <TableRow key={supportCase.case_id}>
                  <TableCell>
                    <div className="flex min-w-64 flex-col gap-1">
                      <Link
                        to={`/app/cases/${supportCase.case_id}`}
                        className="font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {supportCase.subject}
                      </Link>
                      <span className="font-mono text-xs text-muted-foreground">
                        {supportCase.customer_reference}
                      </span>
                      <div className="mt-1 flex flex-wrap gap-1.5 md:hidden">
                        <CategoryBadge category={supportCase.category} />
                        <DifficultyBadge difficulty={supportCase.difficulty} />
                        <ApprovalBadge
                          required={supportCase.expected_approval_required}
                        />
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <CategoryBadge category={supportCase.category} />
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <DifficultyBadge difficulty={supportCase.difficulty} />
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <ApprovalBadge
                      required={supportCase.expected_approval_required}
                    />
                  </TableCell>
                  <TableCell className="hidden whitespace-nowrap text-muted-foreground md:table-cell">
                    {formatDate(supportCase.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      render={<Link to={`/app/cases/${supportCase.case_id}`} />}
                      nativeButton={false}
                      aria-label={`Open ${supportCase.subject}`}
                    >
                      <CaretRightIcon aria-hidden />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </div>
  );
}
