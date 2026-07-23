"""Public request and response models for the bounded run shell."""

from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from resolveops.models.contracts import (
    ApprovalDecisionType,
    ApprovalRequest,
    ContractModel,
    RunStatus,
    WorkflowEvent,
)


class CreateRunRequest(ContractModel):
    case_id: UUID


class CreateRunResponse(ContractModel):
    run_id: UUID
    status: RunStatus
    graph_version: str = Field(min_length=1, max_length=80)
    created_at: AwareDatetime


class WorkflowEventPage(ContractModel):
    events: list[WorkflowEvent]
    after_sequence: int = Field(ge=0)
    last_sequence: int = Field(ge=0)


class ApprovalDecisionRequest(ContractModel):
    proposal_id: UUID
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: ApprovalDecisionType
    comment: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def rejection_has_comment(self) -> "ApprovalDecisionRequest":
        if self.decision is ApprovalDecisionType.REJECT and not (
            self.comment and self.comment.strip()
        ):
            raise ValueError("comment is required when rejecting a proposal")
        return self


class ApprovalEvidence(ContractModel):
    evidence_id: str = Field(min_length=1, max_length=255)
    source_system: str = Field(min_length=1, max_length=100)
    object_type: str = Field(min_length=1, max_length=100)
    object_id: str = Field(min_length=1, max_length=255)
    fact: str = Field(min_length=1, max_length=2_000)


class ApprovalQueueItem(ContractModel):
    run_id: UUID
    case_id: UUID
    case_subject: str = Field(min_length=1, max_length=200)
    approval: ApprovalRequest
    cited_evidence: list[ApprovalEvidence]


class ApprovalQueuePage(ContractModel):
    items: list[ApprovalQueueItem]


class ApprovalDecisionResponse(ContractModel):
    run_id: UUID
    approval: ApprovalRequest
    idempotent_replay: bool
