/** Generated from ResolveOps Pydantic contracts. Do not edit by hand. */

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type SourceSystem = "crm" | "billing" | "telemetry" | "incidents" | "knowledge_base" | "policy" | "case_history" | "calculation" | "synthetic_actions";

export type CaseCategory = "duplicate_charge" | "billing" | "access" | "incident" | "product_issue" | "plan_limit" | "unknown";

export type Urgency = "low" | "normal" | "high" | "critical";

export type RiskIndicator = "prompt_injection" | "unsupported_action" | "identity_or_security" | "legal_or_privacy" | "conflicting_data";

export type ReadToolName = "lookup_customer" | "get_subscription" | "list_invoices" | "get_payment_attempts" | "get_product_events" | "list_service_incidents" | "search_knowledge_base" | "get_policy" | "get_case_history";

export type ActionType = "create_internal_case_note" | "apply_account_credit" | "change_case_status" | "escalate_case";

export type RiskLevel = "R0" | "R1" | "R2" | "R3" | "R4";

export type WorkflowOutcome = "escalate" | "no_action" | "approval_required";

export type ProposalStatus = "pending_approval" | "approved" | "rejected" | "blocked" | "executed" | "invalidated";

export type ApprovalDecisionType = "approve" | "reject";

export type ActionExecutionStatus = "succeeded" | "failed" | "ambiguous";

export type RunStatus = "created" | "running" | "waiting_for_approval" | "completed" | "escalated" | "failed";

export type CaseStatus = "open" | "investigating" | "waiting_for_approval" | "resolved" | "escalated";

export type WorkflowEventType = "run.started" | "node.started" | "node.completed" | "tool.started" | "tool.completed" | "tool.failed" | "model.retry" | "model.fallback" | "evidence.added" | "evidence.verified" | "policy.evaluated" | "approval.requested" | "approval.decided" | "action.executed" | "run.escalated" | "run.completed" | "run.failed";

export type ArtifactKind = "json_report" | "markdown_brief" | "customer_response" | "public_events";

export interface AttachmentMetadata {
  object_key: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
}

export interface TicketInput {
  subject: string;
  body: string;
  customer_reference: string;
  attachments?: Array<AttachmentMetadata>;
}

export interface CaseClassification {
  category: CaseCategory;
  urgency: Urgency;
  confidence: number;
  suspected_account_reference?: string | null;
  requested_outcome: string;
  risk_indicators?: Array<RiskIndicator>;
}

export interface InvestigationPlan {
  recipe_id: string;
  category: CaseCategory;
  required_tools: Array<ReadToolName>;
  optional_tools?: Array<ReadToolName>;
  max_additional_rounds?: number;
}

export interface RequestedToolCall {
  missing_fact: string;
  tool: ReadToolName;
  arguments: Record<string, JsonValue>;
  reason: string;
}

export interface EvidenceItem {
  evidence_id: string;
  source_system: SourceSystem;
  source_object_type: string;
  source_object_id: string;
  observed_at: string;
  fact: string;
  structured_fields?: Record<string, JsonValue>;
  integrity_hash?: string | null;
}

export interface EvidenceBundle {
  items: Array<EvidenceItem>;
  completeness_score: number;
  contradictions?: Array<string>;
}

export interface EvidenceClaim {
  fact: string;
  cited_evidence_ids: Array<string>;
}

export interface EvidenceVerification {
  verified: boolean;
  completeness_score: number;
  validated_evidence_ids?: Array<string>;
  missing_evidence_types?: Array<string>;
  hallucinated_evidence_ids?: Array<string>;
  unsupported_claim_count?: number;
  contradictions?: Array<string>;
}

export interface DuplicateChargeValidation {
  confirmed: boolean;
  reason_code: string;
  account_id?: string | null;
  allowed_credit_cents?: number | null;
  currency?: string | null;
  invoice_evidence_ids?: Array<string>;
  payment_evidence_ids?: Array<string>;
}

export interface ActionProposalInput {
  action_type: ActionType;
  target_reference: string;
  parameters: Record<string, JsonValue>;
  rationale: string;
  cited_evidence_ids: Array<string>;
}

export interface ResolutionProposal {
  resolution_code: string;
  explanation: string;
  cited_evidence_ids: Array<string>;
  recommended_next_step: string;
  action_proposal?: ActionProposalInput | null;
  uncertain?: boolean;
  missing_data?: Array<string>;
}

export interface PolicyDecision {
  outcome: WorkflowOutcome;
  risk_level: RiskLevel;
  reason_code: string;
  action_type?: ActionType | null;
  target_reference?: string | null;
  canonical_parameters?: Record<string, JsonValue>;
  policy_key?: string | null;
  policy_version?: string | null;
  approval_required?: boolean;
}

export interface ActionProposal {
  proposal_id: string;
  run_id: string;
  action_type: ActionType;
  target_reference: string;
  canonical_parameters: Record<string, JsonValue>;
  proposal_hash: string;
  risk_level: RiskLevel;
  policy_key: string;
  policy_version: string;
  status: ProposalStatus;
  idempotency_key: string;
  created_at: string;
}

export interface ApprovalDecision {
  proposal_id: string;
  proposal_hash: string;
  decision: ApprovalDecisionType;
  comment?: string | null;
  decided_by: string;
  decided_at: string;
}

export interface ApprovalRequest {
  request_id: string;
  proposal: ActionProposal;
  requested_by: string;
  requested_at: string;
  decision?: ApprovalDecision | null;
}

export interface ActionResult {
  proposal_id: string;
  idempotency_key: string;
  status: ActionExecutionStatus;
  result?: Record<string, JsonValue>;
  executed_at: string;
}

export interface FinalResponse {
  subject: string;
  body: string;
  internal_case_note: string;
  cited_evidence_ids: Array<string>;
  uncertainty_disclosure?: string | null;
}

export interface ToolResult<T = JsonValue> {
  ok: boolean;
  data?: T | null;
  error_code?: string | null;
  error_message?: string | null;
  source_system: SourceSystem;
  source_ids?: Array<string>;
  observed_at: string;
  latency_ms: number;
  attempt: number;
}

export interface RunError {
  code: string;
  message: string;
  recoverable: boolean;
  node_name?: string | null;
}

export interface SupportCase {
  case_id: string;
  organization_id: string;
  dataset_case_id?: string | null;
  subject: string;
  body: string;
  customer_reference: string;
  status: CaseStatus;
  attachments?: Array<AttachmentMetadata>;
  created_by: string;
  created_at: string;
}

export interface WorkflowRun {
  run_id: string;
  organization_id: string;
  case_id: string;
  thread_id: string;
  initiated_by: string;
  status: RunStatus;
  current_node?: string | null;
  graph_version: string;
  prompt_bundle_version: string;
  dataset_version?: string | null;
  resolved_model?: string | null;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  execution_attempt?: number;
  started_at?: string | null;
  completed_at?: string | null;
  last_error?: RunError | null;
  created_at: string;
}

export interface WorkflowEvent {
  event_id: number;
  run_id: string;
  sequence: number;
  event_type: WorkflowEventType;
  node_name?: string | null;
  status: string;
  public_payload?: Record<string, JsonValue>;
  payload_hash: string;
  created_at: string;
}

export interface RunArtifact {
  artifact_id: string;
  run_id: string;
  kind: ArtifactKind;
  object_key: string;
  mime_type: string;
  sha256: string;
  size_bytes: number;
  created_at: string;
}

export interface ApprovalDecisionRequest {
  proposal_id: string;
  proposal_hash: string;
  decision: ApprovalDecisionType;
  comment?: string | null;
}

export interface ApprovalEvidence {
  evidence_id: string;
  source_system: string;
  object_type: string;
  object_id: string;
  fact: string;
}

export interface ApprovalQueueItem {
  run_id: string;
  case_id: string;
  case_subject: string;
  approval: ApprovalRequest;
  cited_evidence: Array<ApprovalEvidence>;
}

export interface ApprovalQueuePage {
  items: Array<ApprovalQueueItem>;
}

export interface ApprovalDecisionResponse {
  run_id: string;
  approval: ApprovalRequest;
  idempotent_replay: boolean;
}
