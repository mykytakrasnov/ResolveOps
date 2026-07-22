"""Deterministic evidence verification and policy for duplicate charges."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from uuid import UUID

from pydantic import AwareDatetime, Field, ValidationError

from resolveops.models.contracts import (
    ActionType,
    ContractModel,
    DuplicateChargeValidation,
    EvidenceClaim,
    EvidenceItem,
    EvidenceVerification,
    PolicyDecision,
    ResolutionProposal,
    RiskLevel,
    WorkflowOutcome,
)

REQUIRED_EVIDENCE_TYPES = frozenset(
    {"customer_account", "subscription", "invoice", "payment_attempt", "policy"}
)
GLOBAL_CREDIT_LIMIT_CENTS = 10_000
EXPECTED_POLICY_KEY = "billing_duplicate_credit"
EXPECTED_POLICY_VERSION = "3.0"
R3_ESCALATION_REASONS = frozenset(
    {
        "unsupported_evidence_citation",
        "forbidden_action",
        "unsupported_target",
        "unsupported_action",
        "policy_does_not_allow_credit",
        "invalid_calculation",
        "credit_above_limit",
    }
)


class _InvoiceFacts(ContractModel):
    account_id: UUID
    subscription_id: UUID
    amount_cents: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: str
    period_start: date
    period_end: date


class _PaymentFacts(ContractModel):
    account_id: UUID
    invoice_id: UUID
    amount_cents: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: str
    attempted_at: AwareDatetime


class _PolicyFacts(ContractModel):
    policy_key: str
    version: str
    action_type: str
    maximum_amount_cents: int | None = Field(default=None, gt=0)
    approval_required: bool


def verify_evidence(
    *,
    evidence: Sequence[EvidenceItem],
    cited_evidence_ids: Sequence[str],
    claims: Sequence[object] = (),
) -> EvidenceVerification:
    """Verify completeness, internal consistency, citations, and exact factual claims."""

    items = [EvidenceItem.model_validate(item) for item in evidence]
    evidence_ids = [item.evidence_id for item in items]
    evidence_by_id = {item.evidence_id: item for item in items}
    contradictions: set[str] = set()
    if len(evidence_ids) != len(evidence_by_id):
        contradictions.add("duplicate_evidence_id")

    present_types = {item.source_object_type for item in items}
    missing_types = sorted(REQUIRED_EVIDENCE_TYPES - present_types)
    completeness = (len(REQUIRED_EVIDENCE_TYPES) - len(missing_types)) / len(
        REQUIRED_EVIDENCE_TYPES
    )
    hallucinated_ids = sorted(set(cited_evidence_ids) - set(evidence_by_id))
    validated_ids = sorted(set(cited_evidence_ids) & set(evidence_by_id))

    unsupported_claim_count = 0
    for raw_claim in claims:
        try:
            claim = EvidenceClaim.model_validate(raw_claim)
        except ValidationError:
            unsupported_claim_count += 1
            continue
        cited_items = [
            evidence_by_id[evidence_id]
            for evidence_id in claim.cited_evidence_ids
            if evidence_id in evidence_by_id
        ]
        if len(cited_items) != len(claim.cited_evidence_ids) or not any(
            claim.fact == item.fact for item in cited_items
        ):
            unsupported_claim_count += 1

    contradictions.update(_find_contradictions(items))
    verified = (
        completeness == 1
        and not hallucinated_ids
        and unsupported_claim_count == 0
        and not contradictions
    )
    return EvidenceVerification(
        verified=verified,
        completeness_score=completeness,
        validated_evidence_ids=validated_ids,
        missing_evidence_types=missing_types,
        hallucinated_evidence_ids=hallucinated_ids,
        unsupported_claim_count=unsupported_claim_count,
        contradictions=sorted(contradictions),
    )


def validate_duplicate_charge(evidence: Sequence[EvidenceItem]) -> DuplicateChargeValidation:
    """Calculate whether one billing-period charge was paid more than once."""

    invoices, invoice_errors = _invoice_facts(evidence)
    payments, payment_errors = _payment_facts(evidence)
    if invoice_errors or payment_errors:
        return DuplicateChargeValidation(confirmed=False, reason_code="contradictory_evidence")

    invoice_by_id = {facts.source_object_id: facts for facts in invoices}
    successful_by_billing_period: dict[
        tuple[UUID, UUID, date, date, int, str], list[_PaymentEvidence]
    ] = defaultdict(list)
    for payment in payments:
        if payment.facts.status != "succeeded":
            continue
        invoice = invoice_by_id.get(str(payment.facts.invoice_id))
        if (
            invoice is None
            or invoice.facts.status != "paid"
            or not _payment_matches_invoice(payment.facts, invoice.facts)
        ):
            return DuplicateChargeValidation(
                confirmed=False,
                reason_code="contradictory_evidence",
            )
        key = (
            invoice.facts.account_id,
            invoice.facts.subscription_id,
            invoice.facts.period_start,
            invoice.facts.period_end,
            invoice.facts.amount_cents,
            invoice.facts.currency,
        )
        successful_by_billing_period[key].append(payment)

    duplicate_groups = [
        (key, group) for key, group in successful_by_billing_period.items() if len(group) >= 2
    ]
    if not duplicate_groups:
        return DuplicateChargeValidation(confirmed=False, reason_code="duplicate_not_confirmed")
    if len(duplicate_groups) != 1:
        return DuplicateChargeValidation(confirmed=False, reason_code="ambiguous_duplicate_groups")

    key, duplicate_payments = duplicate_groups[0]
    account_id, _subscription_id, _period_start, _period_end, amount_cents, currency = key
    invoice_evidence_ids = sorted(
        {
            invoice.evidence_id
            for invoice in invoices
            if invoice.facts.account_id == account_id
            and invoice.facts.subscription_id == key[1]
            and invoice.facts.period_start == key[2]
            and invoice.facts.period_end == key[3]
            and invoice.facts.amount_cents == amount_cents
            and invoice.facts.currency == currency
        }
    )
    return DuplicateChargeValidation(
        confirmed=True,
        reason_code="duplicate_charge_confirmed",
        account_id=str(account_id),
        allowed_credit_cents=(len(duplicate_payments) - 1) * amount_cents,
        currency=currency,
        invoice_evidence_ids=invoice_evidence_ids,
        payment_evidence_ids=sorted(payment.evidence_id for payment in duplicate_payments),
    )


def enforce_duplicate_charge_policy(
    *,
    evidence: Sequence[EvidenceItem],
    verification: EvidenceVerification,
    validation: DuplicateChargeValidation,
    untrusted_proposal: object | None = None,
) -> PolicyDecision:
    """Return a sanitized outcome; model-supplied parameters never cross this seam."""

    proposal, proposal_error = _validate_proposal(untrusted_proposal)
    if proposal_error:
        return _escalation("invalid_proposal")
    if proposal is not None:
        collected_evidence_ids = {item.evidence_id for item in evidence}
        proposal_evidence_ids = set(proposal.cited_evidence_ids)
        if proposal.action_proposal is not None:
            proposal_evidence_ids.update(proposal.action_proposal.cited_evidence_ids)
        if not proposal_evidence_ids.issubset(collected_evidence_ids):
            return _escalation("unsupported_evidence_citation")
    if proposal is not None and proposal.action_proposal is not None:
        action = proposal.action_proposal
        if action.action_type is not ActionType.APPLY_ACCOUNT_CREDIT:
            return _escalation("forbidden_action")
        if validation.account_id is None or action.target_reference != validation.account_id:
            return _escalation("unsupported_target")

    if not verification.verified:
        return _escalation("evidence_not_verified")
    if not validation.confirmed:
        if proposal is not None and proposal.action_proposal is not None:
            return _escalation("unsupported_action")
        return PolicyDecision(
            outcome=WorkflowOutcome.NO_ACTION,
            risk_level=RiskLevel.R0,
            reason_code=validation.reason_code,
        )

    policy = _single_policy(evidence)
    if policy is None:
        return _escalation("invalid_policy_evidence")
    policy_facts = policy.facts
    if (
        policy_facts.policy_key != EXPECTED_POLICY_KEY
        or policy_facts.version != EXPECTED_POLICY_VERSION
        or policy_facts.action_type != ActionType.APPLY_ACCOUNT_CREDIT.value
        or policy_facts.maximum_amount_cents is None
        or not policy_facts.approval_required
    ):
        return _escalation("policy_does_not_allow_credit", policy=policy_facts)

    amount_cents = validation.allowed_credit_cents
    if amount_cents is None or validation.account_id is None or validation.currency is None:
        return _escalation("invalid_calculation", policy=policy_facts)
    effective_limit = min(policy_facts.maximum_amount_cents, GLOBAL_CREDIT_LIMIT_CENTS)
    if amount_cents > effective_limit:
        return _escalation("credit_above_limit", policy=policy_facts)

    return PolicyDecision(
        outcome=WorkflowOutcome.APPROVAL_REQUIRED,
        risk_level=RiskLevel.R2,
        reason_code="credit_requires_approval",
        action_type=ActionType.APPLY_ACCOUNT_CREDIT,
        target_reference=validation.account_id,
        canonical_parameters={
            "account_id": validation.account_id,
            "amount_cents": amount_cents,
            "currency": validation.currency,
        },
        policy_key=policy_facts.policy_key,
        policy_version=policy_facts.version,
        approval_required=True,
    )


class _InvoiceEvidence(ContractModel):
    evidence_id: str
    source_object_id: str
    facts: _InvoiceFacts


class _PaymentEvidence(ContractModel):
    evidence_id: str
    facts: _PaymentFacts


class _PolicyEvidence(ContractModel):
    facts: _PolicyFacts


def _invoice_facts(
    evidence: Sequence[EvidenceItem],
) -> tuple[list[_InvoiceEvidence], list[str]]:
    parsed: list[_InvoiceEvidence] = []
    errors: list[str] = []
    for item in evidence:
        if item.source_object_type != "invoice":
            continue
        try:
            parsed.append(
                _InvoiceEvidence(
                    evidence_id=item.evidence_id,
                    source_object_id=item.source_object_id,
                    facts=_InvoiceFacts.model_validate(item.structured_fields),
                )
            )
        except ValidationError:
            errors.append("malformed_invoice_fields")
    return parsed, errors


def _payment_facts(
    evidence: Sequence[EvidenceItem],
) -> tuple[list[_PaymentEvidence], list[str]]:
    parsed: list[_PaymentEvidence] = []
    errors: list[str] = []
    for item in evidence:
        if item.source_object_type != "payment_attempt":
            continue
        try:
            parsed.append(
                _PaymentEvidence(
                    evidence_id=item.evidence_id,
                    facts=_PaymentFacts.model_validate(item.structured_fields),
                )
            )
        except ValidationError:
            errors.append("malformed_payment_fields")
    return parsed, errors


def _policy_facts(evidence: Sequence[EvidenceItem]) -> tuple[list[_PolicyEvidence], list[str]]:
    parsed: list[_PolicyEvidence] = []
    errors: list[str] = []
    for item in evidence:
        if item.source_object_type != "policy":
            continue
        try:
            parsed.append(
                _PolicyEvidence(facts=_PolicyFacts.model_validate(item.structured_fields))
            )
        except ValidationError:
            errors.append("malformed_policy_fields")
    return parsed, errors


def _find_contradictions(evidence: Sequence[EvidenceItem]) -> Iterable[str]:
    invoices, invoice_errors = _invoice_facts(evidence)
    payments, payment_errors = _payment_facts(evidence)
    policies, policy_errors = _policy_facts(evidence)
    yield from invoice_errors
    yield from payment_errors
    yield from policy_errors
    if len(policies) != 1:
        yield "ambiguous_policy_evidence"

    invoice_by_id = {invoice.source_object_id: invoice for invoice in invoices}
    for payment in payments:
        invoice = invoice_by_id.get(str(payment.facts.invoice_id))
        if invoice is None:
            yield "payment_without_invoice"
        elif not _payment_matches_invoice(payment.facts, invoice.facts):
            yield "payment_invoice_mismatch"
        elif payment.facts.status == "succeeded" and invoice.facts.status != "paid":
            yield "payment_invoice_status_conflict"


def _payment_matches_invoice(payment: _PaymentFacts, invoice: _InvoiceFacts) -> bool:
    return (
        payment.account_id == invoice.account_id
        and payment.amount_cents == invoice.amount_cents
        and payment.currency == invoice.currency
    )


def _single_policy(evidence: Sequence[EvidenceItem]) -> _PolicyEvidence | None:
    policies, errors = _policy_facts(evidence)
    return policies[0] if len(policies) == 1 and not errors else None


def _validate_proposal(raw: object | None) -> tuple[ResolutionProposal | None, bool]:
    if raw is None:
        return None, False
    try:
        return ResolutionProposal.model_validate(raw), False
    except ValidationError:
        return None, True


def _escalation(reason_code: str, *, policy: _PolicyFacts | None = None) -> PolicyDecision:
    return PolicyDecision(
        outcome=WorkflowOutcome.ESCALATE,
        risk_level=(RiskLevel.R3 if reason_code in R3_ESCALATION_REASONS else RiskLevel.R1),
        reason_code=reason_code,
        policy_key=policy.policy_key if policy else None,
        policy_version=policy.version if policy else None,
    )
