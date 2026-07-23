from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from resolveops.models.contracts import (
    ApprovalDecision,
    ApprovalDecisionType,
    EvidenceBundle,
    EvidenceItem,
    SourceSystem,
    TicketInput,
    ToolResult,
)
from resolveops.models.run_api import ApprovalDecisionRequest


def test_ticket_input_rejects_undeclared_fields() -> None:
    with pytest.raises(ValidationError):
        TicketInput.model_validate(
            {
                "subject": "  Duplicate charge  ",
                "body": "We see two completed charges.",
                "customer_reference": "org_atlas_014",
                "attachments": [],
                "arbitrary_url": "https://example.com/not-allowed",
            }
        )

    ticket = TicketInput.model_validate(
        {
            "subject": "  Duplicate charge  ",
            "body": "We see two completed charges.",
            "customer_reference": "org_atlas_014",
        }
    )

    assert ticket.subject == "Duplicate charge"
    assert ticket.attachments == []


def test_evidence_bundle_rejects_duplicate_evidence_ids() -> None:
    evidence = EvidenceItem(
        evidence_id="invoice_inv_442",
        source_system=SourceSystem.BILLING,
        source_object_type="invoice",
        source_object_id="inv_442",
        observed_at=datetime(2026, 7, 22, tzinfo=UTC),
        fact="Invoice inv_442 settled for 4,900 cents.",
    )

    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        EvidenceBundle(items=[evidence, evidence], completeness_score=1)


def test_rejected_approval_requires_a_review_comment() -> None:
    with pytest.raises(ValidationError, match="comment is required"):
        ApprovalDecision(
            proposal_id=UUID("6cbf2c34-1bea-4e90-9dc8-5f2b15a0ec61"),
            proposal_hash="a" * 64,
            decision=ApprovalDecisionType.REJECT,
            decided_by=UUID("cb6126fb-d633-4b19-a13e-e2cefe5431d5"),
            decided_at=datetime(2026, 7, 22, tzinfo=UTC),
        )

    with pytest.raises(ValidationError, match="comment is required"):
        ApprovalDecisionRequest(
            proposal_id=UUID("6cbf2c34-1bea-4e90-9dc8-5f2b15a0ec61"),
            proposal_hash="a" * 64,
            decision=ApprovalDecisionType.REJECT,
            comment="   ",
        )


def test_failed_tool_result_requires_safe_error_details() -> None:
    with pytest.raises(ValidationError, match="error code and message"):
        ToolResult[dict[str, str]](
            ok=False,
            source_system=SourceSystem.CRM,
            observed_at=datetime(2026, 7, 22, tzinfo=UTC),
            latency_ms=25,
            attempt=1,
        )
