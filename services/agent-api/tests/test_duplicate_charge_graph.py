from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import BaseModel, JsonValue

from resolveops.api.runs import Principal, _execute_shell, _start_independent_execution
from resolveops.graph.duplicate_charge import (
    CLASSIFY_CASE,
    COLLECT_INITIAL_EVIDENCE,
    ENFORCE_POLICY,
    NORMALIZE_INPUT,
    SELECT_INVESTIGATION_RECIPE,
    VALIDATE_DUPLICATE_CHARGE,
    VERIFY_EVIDENCE,
    build_duplicate_charge_graph,
)
from resolveops.graph.state import DuplicateChargeState
from resolveops.models.contracts import (
    ArtifactKind,
    ReadToolName,
    RiskLevel,
    RunArtifact,
    RunStatus,
    TicketInput,
    ToolResult,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowOutcome,
)
from resolveops.policies.duplicate_charge import (
    enforce_duplicate_charge_policy,
    validate_duplicate_charge,
    verify_evidence,
)
from resolveops.repositories.runs import DatabaseRunRepository, ExecutionLease, RunCase
from resolveops.storage.artifacts import InMemoryObjectStorage, StoredObject
from resolveops.tools.contracts import (
    CustomerRecord,
    GetPaymentAttemptsInput,
    GetPolicyInput,
    GetSubscriptionInput,
    InvoicePage,
    InvoiceRecord,
    ListInvoicesInput,
    LookupCustomerInput,
    PaymentAttemptPage,
    PaymentAttemptRecord,
    PolicyRecord,
    SubscriptionRecord,
)
from resolveops.tools.read_only import ReadOnlyToolset

NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
RUN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ORGANIZATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ACCOUNT_ID = UUID("a2bf6866-a47b-5920-8ada-49d78c5d39f1")
SUBSCRIPTION_ID = UUID("09615c4c-25ac-5d56-a6d1-6bbc4573e5e0")
INVOICE_ID = UUID("5837ede5-6c6f-59e8-8cad-52fb7a66d25a")
PAYMENT_ONE_ID = UUID("92c4c18e-51d4-5c66-9120-e26056a90e4d")
PAYMENT_TWO_ID = UUID("c0043fb1-5506-5f18-94d0-cd3b874bec8e")
POLICY_ID = UUID("62141d26-a9f1-5fb1-89bc-f3bd25cbf4a8")


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[ReadToolName] = []

    def lookup_customer(self, request: LookupCustomerInput) -> CustomerRecord:
        self.calls.append(ReadToolName.LOOKUP_CUSTOMER)
        return CustomerRecord(
            account_id=ACCOUNT_ID,
            customer_reference=request.customer_reference,
            name="AtlasFlow Test Organization",
            region="us-west",
            status="active",
            created_at=NOW - timedelta(days=700),
        )

    def get_subscription(self, request: GetSubscriptionInput) -> SubscriptionRecord:
        self.calls.append(ReadToolName.GET_SUBSCRIPTION)
        return SubscriptionRecord(
            subscription_id=SUBSCRIPTION_ID,
            account_id=request.account_id,
            plan="starter",
            status="active",
            amount_cents=4_900,
            currency="USD",
            current_period_start=date(2026, 7, 1),
            current_period_end=date(2026, 8, 1),
            plan_limit_units=1_000,
            usage_units=900,
            previous_plan="free",
            upgraded_at=NOW - timedelta(days=1),
            canceled_at=None,
        )

    def list_invoices(self, request: ListInvoicesInput) -> InvoicePage:
        self.calls.append(ReadToolName.LIST_INVOICES)
        return InvoicePage(
            items=[
                InvoiceRecord(
                    invoice_id=INVOICE_ID,
                    account_id=request.account_id,
                    subscription_id=SUBSCRIPTION_ID,
                    period_start=date(2026, 7, 1),
                    period_end=date(2026, 8, 1),
                    amount_cents=4_900,
                    currency="USD",
                    status="paid",
                    issued_at=NOW - timedelta(days=1),
                )
            ]
        )

    def get_payment_attempts(self, request: GetPaymentAttemptsInput) -> PaymentAttemptPage:
        self.calls.append(ReadToolName.GET_PAYMENT_ATTEMPTS)
        return PaymentAttemptPage(
            items=[
                PaymentAttemptRecord(
                    payment_attempt_id=payment_id,
                    account_id=request.account_id,
                    invoice_id=request.invoice_id,
                    amount_cents=4_900,
                    currency="USD",
                    status="succeeded",
                    processor_reference=f"synthetic_{index}",
                    attempted_at=NOW - timedelta(hours=index),
                )
                for index, payment_id in enumerate((PAYMENT_ONE_ID, PAYMENT_TWO_ID), start=1)
            ]
        )

    def get_policy(self, request: GetPolicyInput) -> PolicyRecord:
        self.calls.append(ReadToolName.GET_POLICY)
        return PolicyRecord(
            policy_id=POLICY_ID,
            policy_key=request.policy_key,
            version=request.version,
            action_type="apply_account_credit",
            maximum_amount_cents=10_000,
            approval_required=True,
            effective_at=NOW - timedelta(days=180),
            body="Synthetic duplicate-charge credit policy.",
        )


class MissingPolicyBackend(FakeBackend):
    def get_policy(self, request: GetPolicyInput) -> PolicyRecord:
        self.calls.append(ReadToolName.GET_POLICY)
        raise ValueError(f"missing synthetic policy {request.policy_key}")


class UnavailableCustomerBackend(FakeBackend):
    def lookup_customer(self, request: LookupCustomerInput) -> CustomerRecord:
        self.calls.append(ReadToolName.LOOKUP_CUSTOMER)
        raise ValueError(f"missing synthetic customer {request.customer_reference}")


class OnePaymentBackend(FakeBackend):
    def get_payment_attempts(self, request: GetPaymentAttemptsInput) -> PaymentAttemptPage:
        self.calls.append(ReadToolName.GET_PAYMENT_ATTEMPTS)
        return PaymentAttemptPage(
            items=[
                PaymentAttemptRecord(
                    payment_attempt_id=PAYMENT_ONE_ID,
                    account_id=request.account_id,
                    invoice_id=request.invoice_id,
                    amount_cents=4_900,
                    currency="USD",
                    status="succeeded",
                    processor_reference="synthetic_1",
                    attempted_at=NOW - timedelta(hours=1),
                )
            ]
        )


class AboveLimitBackend(FakeBackend):
    def list_invoices(self, request: ListInvoicesInput) -> InvoicePage:
        self.calls.append(ReadToolName.LIST_INVOICES)
        return InvoicePage(
            items=[
                InvoiceRecord(
                    invoice_id=INVOICE_ID,
                    account_id=request.account_id,
                    subscription_id=SUBSCRIPTION_ID,
                    period_start=date(2026, 7, 1),
                    period_end=date(2026, 8, 1),
                    amount_cents=15_000,
                    currency="USD",
                    status="paid",
                    issued_at=NOW - timedelta(days=1),
                )
            ]
        )

    def get_payment_attempts(self, request: GetPaymentAttemptsInput) -> PaymentAttemptPage:
        self.calls.append(ReadToolName.GET_PAYMENT_ATTEMPTS)
        return PaymentAttemptPage(
            items=[
                PaymentAttemptRecord(
                    payment_attempt_id=payment_id,
                    account_id=request.account_id,
                    invoice_id=request.invoice_id,
                    amount_cents=15_000,
                    currency="USD",
                    status="succeeded",
                    processor_reference=f"synthetic_{index}",
                    attempted_at=NOW - timedelta(hours=index),
                )
                for index, payment_id in enumerate((PAYMENT_ONE_ID, PAYMENT_TWO_ID), start=1)
            ]
        )


class FakePersistence:
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []
        self.attempts: list[dict[str, Any]] = []
        self.artifacts: list[RunArtifact] = []
        self.storage = InMemoryObjectStorage()

    def append_event(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        event_type: WorkflowEventType,
        status: str,
        public_payload: dict[str, JsonValue],
        node_name: str | None = None,
        final_status: RunStatus | None = None,
        final_error_code: str | None = None,
    ) -> WorkflowEvent:
        del final_status, final_error_code
        assert organization_id == ORGANIZATION_ID
        sequence = len(self.events) + 1
        hash_input = json.dumps(public_payload, sort_keys=True, separators=(",", ":"))
        event = WorkflowEvent(
            event_id=sequence,
            run_id=lease.run_id,
            sequence=sequence,
            event_type=event_type,
            node_name=node_name,
            status=status,
            public_payload=public_payload,
            payload_hash=hashlib.sha256(hash_input.encode()).hexdigest(),
            created_at=NOW,
        )
        self.events.append(event)
        return event

    def start_tool_attempt(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        tool_call_id: str,
        tool_name: ReadToolName,
        attempt: int,
        request_summary: dict[str, str | int],
    ) -> None:
        self.attempts.append(
            {
                "run_id": lease.run_id,
                "organization_id": organization_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "attempt": attempt,
                "request_summary": request_summary,
                "status": "running",
            }
        )

    def finish_tool_attempt(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        tool_call_id: str,
        tool_name: ReadToolName,
        result: ToolResult[BaseModel],
        response_summary: dict[str, str | int | list[str]],
    ) -> None:
        attempt = next(
            item
            for item in reversed(self.attempts)
            if item["tool_call_id"] == tool_call_id and item["attempt"] == result.attempt
        )
        attempt["status"] = "completed" if result.ok else "failed"
        attempt["response_summary"] = response_summary

    def get_run_case(self, **kwargs: object) -> RunCase:
        del kwargs
        return RunCase(
            ticket=TicketInput(
                subject="Charged twice after plan upgrade",
                body="We upgraded yesterday and see two completed charges for the same period.",
                customer_reference="org_atlas_001",
            ),
            created_at=NOW,
        )

    def record_artifact(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        kind: ArtifactKind,
        stored_object: StoredObject,
    ) -> RunArtifact:
        assert organization_id == ORGANIZATION_ID
        artifact = RunArtifact(
            artifact_id=UUID(int=len(self.artifacts) + 1),
            run_id=lease.run_id,
            kind=kind,
            object_key=stored_object.object_key,
            mime_type=stored_object.mime_type,
            sha256=stored_object.sha256,
            size_bytes=stored_object.size_bytes,
            created_at=NOW,
        )
        self.artifacts.append(artifact)
        return artifact

    def create_approval_gate_records(self, **kwargs: object) -> Any:
        del kwargs
        raise AssertionError("the non-checkpointed unit graph must not persist approval records")

    def execute_approved_action(self, **kwargs: object) -> Any:
        del kwargs
        raise AssertionError("the non-checkpointed unit graph must not execute approved actions")


class FailingObjectStorage:
    def put_object(self, **kwargs: object) -> StoredObject:
        del kwargs
        raise RuntimeError("synthetic artifact write failure")


def invoke_graph(backend: FakeBackend) -> tuple[DuplicateChargeState, FakePersistence]:
    persistence = FakePersistence()
    lease = ExecutionLease(
        run_id=RUN_ID,
        token=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        attempt=1,
        expires_at=NOW + timedelta(minutes=1),
    )
    graph = build_duplicate_charge_graph(
        tools=ReadOnlyToolset(backend, now=lambda: NOW),
        persistence=persistence,
        object_storage=persistence.storage,
        lease=lease,
        organization_id=ORGANIZATION_ID,
    )
    initial: DuplicateChargeState = {
        "run_id": RUN_ID,
        "organization_id": ORGANIZATION_ID,
        "ticket": TicketInput(
            subject="Charged twice after plan upgrade",
            body="We upgraded yesterday and see two completed charges for the same period.",
            customer_reference="org_atlas_001",
        ),
        "case_created_at": NOW.isoformat(),
        "evidence": [],
        "tool_errors": [],
        "emitted_events": [],
    }
    return cast(DuplicateChargeState, graph.invoke(initial)), persistence


def test_duplicate_charge_graph_collects_required_typed_evidence_and_public_events() -> None:
    backend = FakeBackend()
    persistence = FakePersistence()
    lease = ExecutionLease(
        run_id=RUN_ID,
        token=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        attempt=1,
        expires_at=NOW + timedelta(minutes=1),
    )
    graph = build_duplicate_charge_graph(
        tools=ReadOnlyToolset(backend, now=lambda: NOW),
        persistence=persistence,
        object_storage=persistence.storage,
        lease=lease,
        organization_id=ORGANIZATION_ID,
    )

    assert {
        NORMALIZE_INPUT,
        CLASSIFY_CASE,
        SELECT_INVESTIGATION_RECIPE,
        COLLECT_INITIAL_EVIDENCE,
        VERIFY_EVIDENCE,
        VALIDATE_DUPLICATE_CHARGE,
        ENFORCE_POLICY,
    }.issubset(graph.nodes)

    initial: DuplicateChargeState = {
        "run_id": RUN_ID,
        "organization_id": ORGANIZATION_ID,
        "ticket": TicketInput(
            subject="Charged twice after plan upgrade",
            body="We upgraded yesterday and see two completed charges for the same period.",
            customer_reference="org_atlas_001",
        ),
        "case_created_at": NOW.isoformat(),
        "evidence": [],
        "tool_errors": [],
        "emitted_events": [],
    }
    result = graph.invoke(initial)

    assert result["classification"].category.value == "duplicate_charge"
    assert result["investigation_plan"].required_tools == list(ReadToolName)[:4] + [
        ReadToolName.GET_POLICY
    ]
    assert backend.calls == result["investigation_plan"].required_tools
    assert {item.evidence_id for item in result["evidence"]} == {
        f"case_report:{RUN_ID}",
        f"crm:{ACCOUNT_ID}",
        f"subscription:{SUBSCRIPTION_ID}",
        f"invoice:{INVOICE_ID}",
        f"payment_attempt:{PAYMENT_ONE_ID}",
        f"payment_attempt:{PAYMENT_TWO_ID}",
        "policy:billing_duplicate_credit:v3.0",
    }
    assert all(item.integrity_hash is not None for item in result["evidence"])
    assert result["evidence_verification"].verified is True
    assert result["duplicate_charge_validation"].confirmed is True
    assert result["duplicate_charge_validation"].allowed_credit_cents == 4_900
    assert result["policy_decision"].outcome is WorkflowOutcome.APPROVAL_REQUIRED
    assert result["policy_decision"].risk_level is RiskLevel.R2
    assert result["policy_decision"].canonical_parameters == {
        "account_id": str(ACCOUNT_ID),
        "amount_cents": 4_900,
        "currency": "USD",
    }
    assert all(attempt["status"] == "completed" for attempt in persistence.attempts)
    assert all(
        str(attempt["tool_call_id"]).startswith("execution-1:") for attempt in persistence.attempts
    )
    assert all(
        "body" not in attempt.get("response_summary", {}) for attempt in persistence.attempts
    )
    event_types = [event.event_type for event in persistence.events]
    assert event_types.count(WorkflowEventType.TOOL_STARTED) == 5
    assert event_types.count(WorkflowEventType.TOOL_COMPLETED) == 5
    assert event_types.count(WorkflowEventType.EVIDENCE_ADDED) == 7
    assert event_types.count(WorkflowEventType.EVIDENCE_VERIFIED) == 1
    assert event_types.count(WorkflowEventType.POLICY_EVALUATED) == 1
    public_payloads = [event.public_payload for event in persistence.events]
    assert all("body" not in payload for payload in public_payloads)
    assert all("rationale" not in payload for payload in public_payloads)
    assert all("parameters" not in payload for payload in public_payloads)
    assert (
        event_types.index(WorkflowEventType.TOOL_STARTED)
        < event_types.index(WorkflowEventType.TOOL_COMPLETED)
        < event_types.index(WorkflowEventType.EVIDENCE_ADDED)
    )


def test_run_shell_publishes_only_events_already_recorded_by_graph_persistence() -> None:
    backend = OnePaymentBackend()
    persistence = FakePersistence()
    lease = ExecutionLease(
        run_id=RUN_ID,
        token=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        attempt=1,
        expires_at=NOW + timedelta(minutes=1),
    )

    yielded = list(
        _execute_shell(
            repository=cast(DatabaseRunRepository, persistence),
            principal=Principal(
                organization_id=ORGANIZATION_ID,
                user_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                roles=frozenset({"operator"}),
            ),
            lease=lease,
            read_tools=ReadOnlyToolset(backend, now=lambda: NOW),
            object_storage=persistence.storage,
        )
    )

    assert yielded == persistence.events
    assert yielded[0].event_type is WorkflowEventType.RUN_STARTED
    assert yielded[-1].event_type is WorkflowEventType.RUN_COMPLETED
    assert {event.node_name for event in yielded} >= {
        NORMALIZE_INPUT,
        CLASSIFY_CASE,
        SELECT_INVESTIGATION_RECIPE,
        COLLECT_INITIAL_EVIDENCE,
    }


def test_missing_policy_evidence_routes_to_escalation() -> None:
    result, persistence = invoke_graph(MissingPolicyBackend())

    assert result["evidence_verification"].verified is False
    assert result["evidence_verification"].missing_evidence_types == ["policy"]
    assert result["policy_decision"].outcome is WorkflowOutcome.ESCALATE
    assert result["policy_decision"].risk_level is RiskLevel.R1
    assert result["policy_decision"].canonical_parameters == {}
    assert result["workflow_outcome"] is WorkflowOutcome.ESCALATE
    verified_event = next(
        event
        for event in persistence.events
        if event.event_type is WorkflowEventType.EVIDENCE_VERIFIED
    )
    assert verified_event.public_payload["verified"] is False
    assert result["final_response"].uncertainty_disclosure is not None
    assert "specialist" in result["final_response"].body.lower()
    assert {artifact.kind for artifact in persistence.artifacts} == {
        ArtifactKind.JSON_REPORT,
        ArtifactKind.MARKDOWN_BRIEF,
    }
    assert any(event.event_type is WorkflowEventType.MODEL_FALLBACK for event in persistence.events)


def test_total_tool_outage_escalates_with_a_cited_case_report() -> None:
    result, persistence = invoke_graph(UnavailableCustomerBackend())

    assert result["workflow_outcome"] is WorkflowOutcome.ESCALATE
    assert result["evidence_verification"].verified is False
    assert result["final_response"].cited_evidence_ids == [f"case_report:{RUN_ID}"]
    assert "specialist" in result["final_response"].body.lower()
    assert {artifact.kind for artifact in persistence.artifacts} == {
        ArtifactKind.JSON_REPORT,
        ArtifactKind.MARKDOWN_BRIEF,
    }


def test_complete_non_duplicate_evidence_routes_to_no_action() -> None:
    result, persistence = invoke_graph(OnePaymentBackend())

    assert result["evidence_verification"].verified is True
    assert result["duplicate_charge_validation"].confirmed is False
    assert result["policy_decision"].outcome is WorkflowOutcome.NO_ACTION
    assert result["workflow_outcome"] is WorkflowOutcome.NO_ACTION
    assert "did not make an account change" in result["final_response"].body
    assert result["final_response"].internal_case_note
    assert result["final_response"].uncertainty_disclosure is not None
    assert set(result["final_response"].cited_evidence_ids).issubset(
        {item.evidence_id for item in result["evidence"]}
    )
    json_object = persistence.storage.objects[f"runs/{RUN_ID}/report.json"]
    report = json.loads(json_object.content)
    assert report["workflow_outcome"] == "no_action"
    assert report["final_response"]["cited_evidence_ids"]
    markdown = persistence.storage.objects[f"runs/{RUN_ID}/report.md"].content.decode()
    assert "## Customer response" in markdown
    assert "## Evidence citations" in markdown
    assert {artifact.kind for artifact in persistence.artifacts} == {
        ArtifactKind.JSON_REPORT,
        ArtifactKind.MARKDOWN_BRIEF,
    }
    for artifact in persistence.artifacts:
        stored = persistence.storage.objects[artifact.object_key]
        assert artifact.sha256 == stored.sha256
        assert artifact.size_bytes == stored.size_bytes


def test_hallucinated_evidence_id_and_unsupported_fact_are_rejected() -> None:
    result, _ = invoke_graph(FakeBackend())
    evidence = result["evidence"]

    verification = verify_evidence(
        evidence=evidence,
        cited_evidence_ids=[evidence[0].evidence_id, "invoice:hallucinated"],
        claims=[
            {
                "fact": "A cash refund has already been issued.",
                "cited_evidence_ids": [evidence[0].evidence_id],
            }
        ],
    )

    assert verification.verified is False
    assert verification.hallucinated_evidence_ids == ["invoice:hallucinated"]
    assert verification.unsupported_claim_count == 1


def test_above_limit_credit_is_blocked_and_escalated() -> None:
    result, _ = invoke_graph(AboveLimitBackend())

    assert result["duplicate_charge_validation"].allowed_credit_cents == 15_000
    assert result["policy_decision"].outcome is WorkflowOutcome.ESCALATE
    assert result["policy_decision"].risk_level is RiskLevel.R3
    assert result["policy_decision"].reason_code == "credit_above_limit"
    assert result["policy_decision"].canonical_parameters == {}


def test_forbidden_action_proposal_is_schema_validated_and_parameters_are_removed() -> None:
    result, _ = invoke_graph(FakeBackend())
    forbidden_proposal: object = {
        "resolution_code": "duplicate_charge_confirmed",
        "explanation": "The evidence supports one duplicate payment.",
        "cited_evidence_ids": result["evidence_verification"].validated_evidence_ids,
        "recommended_next_step": "Close the case without reviewer approval.",
        "action_proposal": {
            "action_type": "change_case_status",
            "target_reference": str(ACCOUNT_ID),
            "parameters": {
                "status": "resolved",
                "amount_cents": 999_999,
                "arbitrary_url": "https://example.com/unsafe",
            },
            "rationale": "Bypass the billing-credit control.",
            "cited_evidence_ids": result["evidence_verification"].validated_evidence_ids,
        },
    }

    decision = enforce_duplicate_charge_policy(
        evidence=result["evidence"],
        verification=result["evidence_verification"],
        validation=result["duplicate_charge_validation"],
        untrusted_proposal=forbidden_proposal,
    )

    assert decision.outcome is WorkflowOutcome.ESCALATE
    assert decision.reason_code == "forbidden_action"
    assert decision.canonical_parameters == {}


def test_allowed_action_uses_code_calculated_parameters_only() -> None:
    result, _ = invoke_graph(FakeBackend())
    untrusted_proposal: object = {
        "resolution_code": "duplicate_charge_confirmed",
        "explanation": "The evidence supports one duplicate payment.",
        "cited_evidence_ids": result["evidence_verification"].validated_evidence_ids,
        "recommended_next_step": "Apply an account credit after review.",
        "action_proposal": {
            "action_type": "apply_account_credit",
            "target_reference": str(ACCOUNT_ID),
            "parameters": {
                "amount_cents": 999_999,
                "currency": "BTC",
                "arbitrary_url": "https://example.com/unsafe",
            },
            "rationale": "Credit the duplicate charge.",
            "cited_evidence_ids": result["evidence_verification"].validated_evidence_ids,
        },
    }

    decision = enforce_duplicate_charge_policy(
        evidence=result["evidence"],
        verification=result["evidence_verification"],
        validation=result["duplicate_charge_validation"],
        untrusted_proposal=untrusted_proposal,
    )

    assert decision.outcome is WorkflowOutcome.APPROVAL_REQUIRED
    assert decision.canonical_parameters == {
        "account_id": str(ACCOUNT_ID),
        "amount_cents": 4_900,
        "currency": "USD",
    }


def test_policy_rejects_a_proposal_with_a_hallucinated_citation() -> None:
    result, _ = invoke_graph(FakeBackend())
    untrusted_proposal: object = {
        "resolution_code": "duplicate_charge_confirmed",
        "explanation": "The evidence supports one duplicate payment.",
        "cited_evidence_ids": ["payment_attempt:hallucinated"],
        "recommended_next_step": "Apply an account credit after review.",
        "action_proposal": {
            "action_type": "apply_account_credit",
            "target_reference": str(ACCOUNT_ID),
            "parameters": {"amount_cents": 4_900},
            "rationale": "Credit the duplicate charge.",
            "cited_evidence_ids": result["duplicate_charge_validation"].payment_evidence_ids,
        },
    }

    decision = enforce_duplicate_charge_policy(
        evidence=result["evidence"],
        verification=result["evidence_verification"],
        validation=result["duplicate_charge_validation"],
        untrusted_proposal=untrusted_proposal,
    )

    assert decision.outcome is WorkflowOutcome.ESCALATE
    assert decision.reason_code == "unsupported_evidence_citation"
    assert decision.canonical_parameters == {}


def test_contradictory_payment_evidence_is_flagged() -> None:
    result, _ = invoke_graph(FakeBackend())
    evidence = list(result["evidence"])
    payment_index = next(
        index for index, item in enumerate(evidence) if item.source_object_type == "payment_attempt"
    )
    payment = evidence[payment_index]
    evidence[payment_index] = payment.model_copy(
        update={"structured_fields": {**payment.structured_fields, "amount_cents": 9_900}}
    )

    verification = verify_evidence(
        evidence=evidence,
        cited_evidence_ids=[item.evidence_id for item in evidence],
    )
    validation = validate_duplicate_charge(evidence)

    assert verification.verified is False
    assert "payment_invoice_mismatch" in verification.contradictions
    assert validation.confirmed is False
    assert validation.reason_code == "contradictory_evidence"


def test_run_shell_persists_escalation_as_the_terminal_outcome() -> None:
    persistence = FakePersistence()
    lease = ExecutionLease(
        run_id=RUN_ID,
        token=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        attempt=1,
        expires_at=NOW + timedelta(minutes=1),
    )

    yielded = list(
        _execute_shell(
            repository=cast(DatabaseRunRepository, persistence),
            principal=Principal(
                organization_id=ORGANIZATION_ID,
                user_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                roles=frozenset({"operator"}),
            ),
            lease=lease,
            read_tools=ReadOnlyToolset(MissingPolicyBackend(), now=lambda: NOW),
            object_storage=persistence.storage,
        )
    )

    assert yielded[-1].event_type is WorkflowEventType.RUN_ESCALATED
    assert WorkflowEventType.RUN_COMPLETED not in {event.event_type for event in yielded}
    assert yielded[-1].sequence == len(yielded)
    assert yielded[-1].payload_hash


def test_approval_required_is_not_finalized_as_successful() -> None:
    persistence = FakePersistence()
    lease = ExecutionLease(
        run_id=RUN_ID,
        token=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        attempt=1,
        expires_at=NOW + timedelta(minutes=1),
    )

    with pytest.raises(RuntimeError, match="needs durable checkpoint persistence"):
        list(
            _execute_shell(
                repository=cast(DatabaseRunRepository, persistence),
                principal=Principal(
                    organization_id=ORGANIZATION_ID,
                    user_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                    roles=frozenset({"operator"}),
                ),
                lease=lease,
                read_tools=ReadOnlyToolset(FakeBackend(), now=lambda: NOW),
                object_storage=persistence.storage,
            )
        )

    assert WorkflowEventType.RUN_COMPLETED not in {event.event_type for event in persistence.events}
    assert persistence.artifacts == []


def test_report_write_failure_emits_a_recoverable_run_failed_event() -> None:
    persistence = FakePersistence()
    lease = ExecutionLease(
        run_id=RUN_ID,
        token=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        attempt=1,
        expires_at=NOW + timedelta(minutes=1),
    )

    independent = _start_independent_execution(
        repository=cast(DatabaseRunRepository, persistence),
        principal=Principal(
            organization_id=ORGANIZATION_ID,
            user_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            roles=frozenset({"operator"}),
        ),
        lease=lease,
        read_tools=ReadOnlyToolset(OnePaymentBackend(), now=lambda: NOW),
        object_storage=FailingObjectStorage(),
    )
    frames = list(independent.events)

    assert persistence.events[-1].event_type is WorkflowEventType.RUN_FAILED
    assert persistence.events[-1].public_payload == {
        "error_code": "run_shell_failed",
        "recoverable": True,
    }
    assert "run.failed" in frames[-1]
