"""Core, transport-safe contracts for the bounded ResolveOps workflow."""

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator


class ContractModel(BaseModel):
    """Base configuration for data accepted across workflow boundaries."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AttachmentMetadata(ContractModel):
    """Metadata for a private, previously authorized attachment."""

    object_key: str = Field(min_length=1, max_length=512)
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(ge=0, le=5 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TicketInput(ContractModel):
    """Normalized synthetic support ticket supplied to a workflow run."""

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)
    customer_reference: str = Field(min_length=1, max_length=128)
    attachments: list[AttachmentMetadata] = Field(default_factory=list, max_length=3)


class SourceSystem(StrEnum):
    """Allowlisted systems that may contribute workflow evidence."""

    CRM = "crm"
    BILLING = "billing"
    TELEMETRY = "telemetry"
    INCIDENTS = "incidents"
    KNOWLEDGE_BASE = "knowledge_base"
    POLICY = "policy"
    CASE_HISTORY = "case_history"
    CALCULATION = "calculation"
    SYNTHETIC_ACTIONS = "synthetic_actions"


class EvidenceItem(ContractModel):
    """A single factual observation returned by an allowlisted source."""

    evidence_id: str = Field(min_length=1, max_length=160)
    source_system: SourceSystem
    source_object_type: str = Field(min_length=1, max_length=80)
    source_object_id: str = Field(min_length=1, max_length=160)
    observed_at: AwareDatetime
    fact: str = Field(min_length=1, max_length=2_000)
    structured_fields: dict[str, JsonValue] = Field(default_factory=dict)
    integrity_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class EvidenceBundle(ContractModel):
    """Verified evidence and deterministic completeness metadata."""

    items: list[EvidenceItem]
    completeness_score: float = Field(ge=0, le=1)
    contradictions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> "EvidenceBundle":
        evidence_ids = [item.evidence_id for item in self.items]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        return self


class EvidenceClaim(ContractModel):
    """Untrusted factual claim tied to exact evidence returned by tools."""

    fact: str = Field(min_length=1, max_length=2_000)
    cited_evidence_ids: list[str] = Field(min_length=1)


class EvidenceVerification(ContractModel):
    """Deterministic, public-safe result of checking evidence and citations."""

    verified: bool
    completeness_score: float = Field(ge=0, le=1)
    validated_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence_types: list[str] = Field(default_factory=list)
    hallucinated_evidence_ids: list[str] = Field(default_factory=list)
    unsupported_claim_count: int = Field(default=0, ge=0)
    contradictions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def verified_result_is_internally_consistent(self) -> "EvidenceVerification":
        has_failure = bool(
            self.completeness_score < 1
            or self.missing_evidence_types
            or self.hallucinated_evidence_ids
            or self.unsupported_claim_count
            or self.contradictions
        )
        if self.verified == has_failure:
            raise ValueError("verified evidence result is inconsistent with its findings")
        return self


class DuplicateChargeValidation(ContractModel):
    """Code-calculated duplicate-charge finding; no model arithmetic is accepted."""

    confirmed: bool
    reason_code: str = Field(min_length=1, max_length=100)
    account_id: str | None = Field(default=None, max_length=160)
    allowed_credit_cents: int | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    invoice_evidence_ids: list[str] = Field(default_factory=list)
    payment_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def confirmed_result_has_calculated_fields(self) -> "DuplicateChargeValidation":
        calculated = (self.account_id, self.allowed_credit_cents, self.currency)
        if self.confirmed and (
            any(value is None for value in calculated)
            or not self.invoice_evidence_ids
            or len(self.payment_evidence_ids) < 2
        ):
            raise ValueError(
                "confirmed duplicate charge requires calculated evidence-backed fields"
            )
        if not self.confirmed and any(value is not None for value in calculated):
            raise ValueError("unconfirmed duplicate charge cannot contain calculated action fields")
        return self


class CaseCategory(StrEnum):
    DUPLICATE_CHARGE = "duplicate_charge"
    BILLING = "billing"
    ACCESS = "access"
    INCIDENT = "incident"
    PRODUCT_ISSUE = "product_issue"
    PLAN_LIMIT = "plan_limit"
    UNKNOWN = "unknown"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class RiskIndicator(StrEnum):
    PROMPT_INJECTION = "prompt_injection"
    UNSUPPORTED_ACTION = "unsupported_action"
    IDENTITY_OR_SECURITY = "identity_or_security"
    LEGAL_OR_PRIVACY = "legal_or_privacy"
    CONFLICTING_DATA = "conflicting_data"


class CaseClassification(ContractModel):
    """Validated structured output from case classification."""

    category: CaseCategory
    urgency: Urgency
    confidence: float = Field(ge=0, le=1)
    suspected_account_reference: str | None = Field(default=None, max_length=128)
    requested_outcome: str = Field(min_length=1, max_length=500)
    risk_indicators: list[RiskIndicator] = Field(default_factory=list)


class ReadToolName(StrEnum):
    """Tools the model may request during bounded evidence collection."""

    LOOKUP_CUSTOMER = "lookup_customer"
    GET_SUBSCRIPTION = "get_subscription"
    LIST_INVOICES = "list_invoices"
    GET_PAYMENT_ATTEMPTS = "get_payment_attempts"
    GET_PRODUCT_EVENTS = "get_product_events"
    LIST_SERVICE_INCIDENTS = "list_service_incidents"
    SEARCH_KNOWLEDGE_BASE = "search_knowledge_base"
    GET_POLICY = "get_policy"
    GET_CASE_HISTORY = "get_case_history"


class InvestigationPlan(ContractModel):
    """Deterministically selected allowlisted evidence recipe."""

    recipe_id: str = Field(min_length=1, max_length=100)
    category: CaseCategory
    required_tools: list[ReadToolName] = Field(min_length=1)
    optional_tools: list[ReadToolName] = Field(default_factory=list)
    max_additional_rounds: int = Field(default=1, ge=0, le=1)


class RequestedToolCall(ContractModel):
    """A bounded request for one additional evidence lookup."""

    missing_fact: str = Field(min_length=1, max_length=500)
    tool: ReadToolName
    arguments: dict[str, JsonValue]
    reason: str = Field(min_length=1, max_length=1_000)


class EvidenceGapAssessment(ContractModel):
    """Validated model assessment; deterministic code still owns tool execution."""

    sufficient: bool
    requested_tools: list[RequestedToolCall] = Field(default_factory=list, max_length=4)
    missing_data: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def sufficient_assessment_has_no_requests(self) -> "EvidenceGapAssessment":
        if self.sufficient and (self.requested_tools or self.missing_data):
            raise ValueError("sufficient evidence cannot contain gap requests")
        if not self.sufficient and not (self.requested_tools or self.missing_data):
            raise ValueError("insufficient evidence must identify a gap")
        return self


class ActionType(StrEnum):
    CREATE_INTERNAL_CASE_NOTE = "create_internal_case_note"
    APPLY_ACCOUNT_CREDIT = "apply_account_credit"
    CHANGE_CASE_STATUS = "change_case_status"
    ESCALATE_CASE = "escalate_case"


class ActionProposalInput(ContractModel):
    """Untrusted model recommendation awaiting deterministic policy enforcement."""

    action_type: ActionType
    target_reference: str = Field(min_length=1, max_length=160)
    parameters: dict[str, JsonValue]
    rationale: str = Field(min_length=1, max_length=1_000)
    cited_evidence_ids: list[str] = Field(min_length=1)


class ResolutionProposal(ContractModel):
    """Evidence-cited model recommendation, not an authorization to act."""

    resolution_code: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=2_000)
    cited_evidence_ids: list[str] = Field(min_length=1)
    recommended_next_step: str = Field(min_length=1, max_length=1_000)
    action_proposal: ActionProposalInput | None = None
    uncertain: bool = False
    missing_data: list[str] = Field(default_factory=list)


class RiskLevel(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class WorkflowOutcome(StrEnum):
    ESCALATE = "escalate"
    NO_ACTION = "no_action"
    APPROVAL_REQUIRED = "approval_required"


class PolicyDecision(ContractModel):
    """Sanitized deterministic policy result, never an executable proposal."""

    outcome: WorkflowOutcome
    risk_level: RiskLevel
    reason_code: str = Field(min_length=1, max_length=100)
    action_type: ActionType | None = None
    target_reference: str | None = Field(default=None, max_length=160)
    canonical_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    policy_key: str | None = Field(default=None, max_length=160)
    policy_version: str | None = Field(default=None, max_length=80)
    approval_required: bool = False

    @model_validator(mode="after")
    def action_fields_exist_only_for_approval(self) -> "PolicyDecision":
        has_action_fields = bool(
            self.action_type is not None
            or self.target_reference is not None
            or self.canonical_parameters
        )
        if self.outcome is WorkflowOutcome.APPROVAL_REQUIRED:
            if (
                not self.approval_required
                or self.action_type is None
                or self.target_reference is None
                or not self.canonical_parameters
                or self.policy_key is None
                or self.policy_version is None
            ):
                raise ValueError("approval outcome requires a complete sanitized action")
        elif self.approval_required or has_action_fields:
            raise ValueError("non-approval outcome cannot contain executable action fields")
        return self


class ProposalStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    EXECUTED = "executed"
    INVALIDATED = "invalidated"


class ActionProposal(ContractModel):
    """Immutable, policy-enforced action proposal shown to a reviewer."""

    proposal_id: UUID
    run_id: UUID
    action_type: ActionType
    target_reference: str = Field(min_length=1, max_length=160)
    canonical_parameters: dict[str, JsonValue]
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_level: RiskLevel
    policy_key: str = Field(min_length=1, max_length=160)
    policy_version: str = Field(min_length=1, max_length=80)
    status: ProposalStatus
    idempotency_key: str = Field(min_length=1, max_length=255)
    created_at: AwareDatetime


class ApprovalDecisionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalDecision(ContractModel):
    """Persisted human decision bound to an exact proposal hash."""

    proposal_id: UUID
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: ApprovalDecisionType
    comment: str | None = Field(default=None, max_length=2_000)
    decided_by: UUID
    decided_at: AwareDatetime

    @model_validator(mode="after")
    def rejection_has_comment(self) -> "ApprovalDecision":
        if self.decision is ApprovalDecisionType.REJECT and not self.comment:
            raise ValueError("comment is required when rejecting a proposal")
        return self


class ApprovalRequest(ContractModel):
    """Reviewer-facing approval state for a persisted proposal."""

    request_id: UUID
    proposal: ActionProposal
    requested_by: UUID
    requested_at: AwareDatetime
    decision: ApprovalDecision | None = None


class ActionExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class ActionResult(ContractModel):
    """Result of deterministic exactly-once synthetic action execution."""

    proposal_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    status: ActionExecutionStatus
    result: dict[str, JsonValue] = Field(default_factory=dict)
    executed_at: AwareDatetime


class FinalResponse(ContractModel):
    """Validated customer draft and internal note without hidden reasoning."""

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    internal_case_note: str = Field(min_length=1, max_length=10_000)
    cited_evidence_ids: list[str] = Field(min_length=1)
    uncertainty_disclosure: str | None = Field(default=None, max_length=2_000)


class ToolResult[T](ContractModel):
    """Standard envelope returned by every typed tool adapter."""

    ok: bool
    data: T | None = None
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=1_000)
    source_system: SourceSystem
    source_ids: list[str] = Field(default_factory=list)
    observed_at: AwareDatetime
    latency_ms: int = Field(ge=0)
    attempt: int = Field(ge=1)

    @model_validator(mode="after")
    def failures_have_safe_error_details(self) -> "ToolResult[T]":
        if not self.ok and (not self.error_code or not self.error_message):
            raise ValueError("failed tool results require an error code and message")
        return self


class RunError(ContractModel):
    """Safe terminal or recoverable workflow error."""

    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1_000)
    recoverable: bool
    node_name: str | None = Field(default=None, max_length=100)


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    FAILED = "failed"


class CaseStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class SupportCase(ContractModel):
    """Frontend-safe synthetic support case shape."""

    case_id: UUID
    organization_id: UUID
    dataset_case_id: str | None = Field(default=None, max_length=160)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)
    customer_reference: str = Field(min_length=1, max_length=128)
    status: CaseStatus
    attachments: list[AttachmentMetadata] = Field(default_factory=list, max_length=3)
    created_by: UUID
    created_at: AwareDatetime


class WorkflowRun(ContractModel):
    """Public workflow run state without checkpoint or hidden reasoning data."""

    run_id: UUID
    organization_id: UUID
    case_id: UUID
    thread_id: str = Field(min_length=1, max_length=255)
    initiated_by: UUID
    status: RunStatus
    current_node: str | None = Field(default=None, max_length=100)
    graph_version: str = Field(min_length=1, max_length=80)
    prompt_bundle_version: str = Field(min_length=1, max_length=80)
    dataset_version: str | None = Field(default=None, max_length=80)
    resolved_model: str | None = Field(default=None, max_length=255)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    execution_attempt: int = Field(default=0, ge=0)
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    last_error: RunError | None = None
    created_at: AwareDatetime


class WorkflowEventType(StrEnum):
    RUN_STARTED = "run.started"
    NODE_STARTED = "node.started"
    NODE_COMPLETED = "node.completed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    MODEL_RETRY = "model.retry"
    MODEL_FALLBACK = "model.fallback"
    EVIDENCE_ADDED = "evidence.added"
    EVIDENCE_VERIFIED = "evidence.verified"
    POLICY_EVALUATED = "policy.evaluated"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    ACTION_EXECUTED = "action.executed"
    RUN_ESCALATED = "run.escalated"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class WorkflowEvent(ContractModel):
    """Append-only event payload safe to expose to the product UI."""

    event_id: int = Field(ge=1)
    run_id: UUID
    sequence: int = Field(ge=1)
    event_type: WorkflowEventType
    node_name: str | None = Field(default=None, max_length=100)
    status: str = Field(min_length=1, max_length=50)
    public_payload: dict[str, JsonValue] = Field(default_factory=dict)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime


class ArtifactKind(StrEnum):
    JSON_REPORT = "json_report"
    MARKDOWN_BRIEF = "markdown_brief"
    CUSTOMER_RESPONSE = "customer_response"
    PUBLIC_EVENTS = "public_events"


class RunArtifact(ContractModel):
    """Reference to a private report object; no storage credentials are exposed."""

    artifact_id: UUID
    run_id: UUID
    kind: ArtifactKind
    object_key: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=127)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    created_at: AwareDatetime


class InternalTraceIdentifiers(ContractModel):
    """Private correlation identifiers included only in authenticated reports."""

    workflow_run_id: UUID
    langgraph_thread_id: str = Field(min_length=1, max_length=255)
    langfuse_trace_id: str | None = Field(default=None, min_length=1, max_length=255)
    aws_request_id: str | None = Field(default=None, min_length=1, max_length=255)
