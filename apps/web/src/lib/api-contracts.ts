import { z } from "zod";

const jsonValueSchema: z.ZodType<unknown> = z.lazy(() =>
  z.union([
    z.string(),
    z.number(),
    z.boolean(),
    z.null(),
    z.array(jsonValueSchema),
    z.record(z.string(), jsonValueSchema),
  ]),
);

export const publicCaseSchema = z.object({
  case_id: z.uuid(),
  split: z.string(),
  category: z.string(),
  difficulty: z.string(),
  curated: z.literal(true),
  expected_approval_required: z.boolean(),
  subject: z.string(),
  body: z.string(),
  customer_reference: z.string(),
  created_at: z.iso.datetime({ offset: true }),
  attachments: z.array(z.record(z.string(), z.string())),
});

export const publicCasePageSchema = z.object({
  items: z.array(publicCaseSchema),
  page: z.object({
    limit: z.number().int().positive(),
    next_cursor: z.string().nullable(),
  }),
});

export const runStatusSchema = z.enum([
  "created",
  "running",
  "waiting_for_approval",
  "completed",
  "escalated",
  "failed",
]);

export const workflowEventTypeSchema = z.enum([
  "run.started",
  "node.started",
  "node.completed",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "model.retry",
  "model.fallback",
  "evidence.added",
  "evidence.verified",
  "policy.evaluated",
  "approval.requested",
  "approval.decided",
  "action.executed",
  "run.escalated",
  "run.completed",
  "run.failed",
]);

export const runErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  recoverable: z.boolean(),
  node_name: z.string().nullable().optional(),
});

export const workflowRunSchema = z.object({
  run_id: z.uuid(),
  organization_id: z.uuid(),
  case_id: z.uuid(),
  thread_id: z.string(),
  initiated_by: z.uuid(),
  status: runStatusSchema,
  current_node: z.string().nullable().optional(),
  graph_version: z.string(),
  prompt_bundle_version: z.string(),
  dataset_version: z.string().nullable().optional(),
  resolved_model: z.string().nullable().optional(),
  input_tokens: z.number().int().nonnegative().optional(),
  output_tokens: z.number().int().nonnegative().optional(),
  cost_usd: z.number().nonnegative().optional(),
  execution_attempt: z.number().int().nonnegative().optional(),
  started_at: z.iso.datetime({ offset: true }).nullable().optional(),
  completed_at: z.iso.datetime({ offset: true }).nullable().optional(),
  last_error: runErrorSchema.nullable().optional(),
  created_at: z.iso.datetime({ offset: true }),
});

export const workflowEventSchema = z.object({
  event_id: z.number().int().positive(),
  run_id: z.uuid(),
  sequence: z.number().int().positive(),
  event_type: workflowEventTypeSchema,
  node_name: z.string().nullable().optional(),
  status: z.string(),
  public_payload: z.record(z.string(), jsonValueSchema).optional().default({}),
  payload_hash: z.string(),
  created_at: z.iso.datetime({ offset: true }),
});

export const workflowEventPageSchema = z.object({
  events: z.array(workflowEventSchema),
  after_sequence: z.number().int().nonnegative(),
  last_sequence: z.number().int().nonnegative(),
});

export const createRunResponseSchema = z.object({
  run_id: z.uuid(),
  status: runStatusSchema,
  graph_version: z.string(),
  created_at: z.iso.datetime({ offset: true }),
});

export const proposalStatusSchema = z.enum([
  "pending_approval",
  "approved",
  "rejected",
  "blocked",
  "executed",
  "invalidated",
]);

export const approvalDecisionSchema = z.object({
  proposal_id: z.uuid(),
  proposal_hash: z.string().regex(/^[0-9a-f]{64}$/),
  decision: z.enum(["approve", "reject"]),
  comment: z.string().nullable().optional(),
  decided_by: z.uuid(),
  decided_at: z.iso.datetime({ offset: true }),
});

export const actionProposalSchema = z.object({
  proposal_id: z.uuid(),
  run_id: z.uuid(),
  action_type: z.string(),
  target_reference: z.string(),
  canonical_parameters: z.record(z.string(), jsonValueSchema),
  proposal_hash: z.string().regex(/^[0-9a-f]{64}$/),
  risk_level: z.enum(["R0", "R1", "R2", "R3", "R4"]),
  policy_key: z.string(),
  policy_version: z.string(),
  status: proposalStatusSchema,
  idempotency_key: z.string(),
  created_at: z.iso.datetime({ offset: true }),
});

export const approvalRequestSchema = z.object({
  request_id: z.uuid(),
  proposal: actionProposalSchema,
  requested_by: z.uuid(),
  requested_at: z.iso.datetime({ offset: true }),
  decision: approvalDecisionSchema.nullable().optional(),
});

export const approvalQueueItemSchema = z.object({
  run_id: z.uuid(),
  case_id: z.uuid(),
  case_subject: z.string(),
  approval: approvalRequestSchema,
  cited_evidence: z.array(
    z.object({
      evidence_id: z.string(),
      source_system: z.string(),
      object_type: z.string(),
      object_id: z.string(),
      fact: z.string(),
    }),
  ),
});

export const approvalQueuePageSchema = z.object({
  items: z.array(approvalQueueItemSchema),
});

export const approvalDecisionResponseSchema = z.object({
  run_id: z.uuid(),
  approval: approvalRequestSchema,
  idempotent_replay: z.boolean(),
});

export type PublicCase = z.infer<typeof publicCaseSchema>;
export type WorkflowRun = z.infer<typeof workflowRunSchema>;
export type WorkflowEvent = z.infer<typeof workflowEventSchema>;
export type WorkflowEventType = z.infer<typeof workflowEventTypeSchema>;
export type RunStatus = z.infer<typeof runStatusSchema>;
export type ApprovalQueueItem = z.infer<typeof approvalQueueItemSchema>;
