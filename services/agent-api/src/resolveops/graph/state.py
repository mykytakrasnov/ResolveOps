"""Serializable state for the bounded duplicate-charge evidence graph."""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict
from uuid import UUID

from resolveops.models.contracts import (
    CaseClassification,
    EvidenceItem,
    InvestigationPlan,
    TicketInput,
    WorkflowEvent,
)


class DuplicateChargeState(TypedDict, total=False):
    run_id: UUID
    organization_id: UUID
    ticket: TicketInput
    case_created_at: str
    classification: CaseClassification
    investigation_plan: InvestigationPlan
    account_id: str
    invoice_ids: list[str]
    evidence: Annotated[list[EvidenceItem], add]
    tool_errors: Annotated[list[str], add]
    emitted_events: Annotated[list[WorkflowEvent], add]
