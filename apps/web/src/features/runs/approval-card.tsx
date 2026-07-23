import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Modal, ModalOverlay } from "react-aria-components/Modal";

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
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogFooter,
  DialogHeader,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/field";
import { decideRun } from "@/lib/api";
import type { ApprovalQueueItem } from "@/lib/api-contracts";

function amountLabel(parameters: Record<string, unknown>) {
  const amount = parameters.amount_cents;
  const currency = parameters.currency;
  if (typeof amount !== "number" || typeof currency !== "string") return "—";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
  }).format(amount / 100);
}

function DecisionDialog({
  item,
  decision,
  onClose,
  onDecided,
}: {
  item: ApprovalQueueItem;
  decision: "approve" | "reject";
  onClose: () => void;
  onDecided: () => void;
}) {
  const [comment, setComment] = useState("");
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      decideRun(item.run_id, {
        proposal_id: item.approval.proposal.proposal_id,
        proposal_hash: item.approval.proposal.proposal_hash,
        decision,
        ...(comment.trim() ? { comment: comment.trim() } : {}),
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["approval", item.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["approvals"] }),
        queryClient.invalidateQueries({ queryKey: ["run", item.run_id] }),
        queryClient.invalidateQueries({
          queryKey: ["run-events", item.run_id],
        }),
      ]);
      onDecided();
      onClose();
    },
  });
  const rejecting = decision === "reject";
  const invalid = rejecting && !comment.trim();

  return (
    <ModalOverlay
      isOpen
      isDismissable={!mutation.isPending}
      onOpenChange={(open) => !open && onClose()}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4"
    >
      <Modal className="w-full max-w-lg rounded-xl border bg-popover text-popover-foreground shadow-xl">
        <Dialog role={rejecting ? "alertdialog" : "dialog"}>
          <DialogHeader
            title={
              rejecting ? "Reject proposed action?" : "Approve proposed action?"
            }
            description={
              rejecting
                ? "Rejection stops the action and escalates the run. A review comment is required."
                : "Approval is explicit and bound to the proposal fingerprint shown below."
            }
          />
          <DialogBody className="gap-4">
            <div className="rounded-md border p-3 text-xs">
              <p className="text-muted-foreground">Proposal fingerprint</p>
              <p className="mt-1 break-all font-mono">
                {item.approval.proposal.proposal_hash}
              </p>
            </div>
            <div>
              <Label htmlFor={`${decision}-comment`}>
                Review comment {rejecting ? "(required)" : "(optional)"}
              </Label>
              <textarea
                id={`${decision}-comment`}
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                required={rejecting}
                aria-invalid={invalid || undefined}
                className="mt-2 min-h-24 w-full rounded-md border bg-background p-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
            {mutation.error ? (
              <Alert variant="destructive">
                <AlertTitle>Decision not saved</AlertTitle>
                <AlertDescription>{mutation.error.message}</AlertDescription>
              </Alert>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <DialogClose disabled={mutation.isPending}>Cancel</DialogClose>
            <Button
              variant={rejecting ? "destructive" : "default"}
              disabled={invalid || mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending
                ? "Saving decision…"
                : rejecting
                  ? "Reject proposal"
                  : "Approve proposal"}
            </Button>
          </DialogFooter>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

export function ApprovalCard({
  item,
  onDecided,
}: {
  item: ApprovalQueueItem;
  onDecided: () => void;
}) {
  const [dialog, setDialog] = useState<"approve" | "reject" | null>(null);
  const proposal = item.approval.proposal;
  const decision = item.approval.decision;
  const decisionLabel =
    decision?.decision === "approve"
      ? "Approved · awaiting execution"
      : decision?.decision === "reject"
        ? "Rejected"
        : "Review required";

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Action proposal</CardTitle>
              <CardDescription>
                Immutable policy-enforced parameters awaiting a human decision.
              </CardDescription>
            </div>
            <Badge
              variant={
                decision?.decision === "reject" ? "destructive" : "outline"
              }
            >
              {decisionLabel}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">Proposed action</dt>
              <dd className="mt-1 font-medium">
                {proposal.action_type.replaceAll("_", " ")}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Amount</dt>
              <dd className="mt-1 font-medium">
                {amountLabel(proposal.canonical_parameters)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Target</dt>
              <dd className="mt-1 break-all font-mono">
                {proposal.target_reference}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Risk level</dt>
              <dd className="mt-1">
                <Badge variant="secondary">{proposal.risk_level}</Badge>
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Policy basis</dt>
              <dd className="mt-1 font-mono">
                {proposal.policy_key} · v{proposal.policy_version}
              </dd>
            </div>
          </dl>
          <div>
            <p className="text-sm font-medium">Cited evidence</p>
            <ul className="mt-2 space-y-2 text-sm text-muted-foreground">
              {item.cited_evidence.map((evidence) => (
                <li
                  key={evidence.evidence_id}
                  className="rounded-md border p-3"
                >
                  <span className="font-mono text-xs text-foreground">
                    {evidence.evidence_id}
                  </span>
                  <p className="mt-1">{evidence.fact}</p>
                </li>
              ))}
            </ul>
          </div>
          <dl className="grid gap-3 border-t pt-4 text-xs sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">Proposal fingerprint</dt>
              <dd className="mt-1 break-all font-mono">
                {proposal.proposal_hash}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Idempotency key</dt>
              <dd className="mt-1 break-all font-mono">
                {proposal.idempotency_key}
              </dd>
            </div>
          </dl>
          {!decision ? (
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="destructive" onClick={() => setDialog("reject")}>
                Reject
              </Button>
              <Button onClick={() => setDialog("approve")}>Approve</Button>
            </div>
          ) : decision.comment ? (
            <Alert>
              <AlertTitle>Reviewer comment</AlertTitle>
              <AlertDescription>{decision.comment}</AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>
      {dialog ? (
        <DecisionDialog
          item={item}
          decision={dialog}
          onClose={() => setDialog(null)}
          onDecided={onDecided}
        />
      ) : null}
    </>
  );
}
