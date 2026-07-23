"""Serializable state for the bounded duplicate-charge evidence graph."""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict
from uuid import UUID

from resolveops.models.contracts import (
    ActionResult,
    ApprovalDecisionType,
    CaseClassification,
    DuplicateChargeValidation,
    EvidenceGapAssessment,
    EvidenceItem,
    EvidenceVerification,
    FinalResponse,
    InternalTraceIdentifiers,
    InvestigationPlan,
    PolicyDecision,
    ResolutionProposal,
    RunArtifact,
    TicketInput,
    WorkflowEvent,
    WorkflowOutcome,
)


class DuplicateChargeState(TypedDict, total=False):
    run_id: UUID
    case_id: UUID
    organization_id: UUID
    ticket: TicketInput
    case_created_at: str
    classification: CaseClassification
    classification_requires_escalation: bool
    investigation_plan: InvestigationPlan
    account_id: str
    invoice_ids: list[str]
    evidence: Annotated[list[EvidenceItem], add]
    tool_errors: Annotated[list[str], add]
    evidence_gap_assessment: EvidenceGapAssessment
    evidence_verification: EvidenceVerification
    duplicate_charge_validation: DuplicateChargeValidation
    resolution: ResolutionProposal
    model_failure_node: str
    policy_decision: PolicyDecision
    final_response: FinalResponse
    finalized_artifacts: list[RunArtifact]
    workflow_outcome: WorkflowOutcome
    workflow_reason_code: str
    approval_decision: ApprovalDecisionType
    action_result: ActionResult
    emitted_events: Annotated[list[WorkflowEvent], add]
    internal_trace_identifiers: InternalTraceIdentifiers
