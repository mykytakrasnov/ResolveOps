"""Public request and response models for the bounded run shell."""

from uuid import UUID

from pydantic import AwareDatetime, Field

from resolveops.models.contracts import ContractModel, RunStatus, WorkflowEvent


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
