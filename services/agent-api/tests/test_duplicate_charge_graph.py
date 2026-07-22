from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, JsonValue

from resolveops.api.runs import Principal, _execute_shell
from resolveops.graph.duplicate_charge import (
    CLASSIFY_CASE,
    COLLECT_INITIAL_EVIDENCE,
    NORMALIZE_INPUT,
    SELECT_INVESTIGATION_RECIPE,
    build_duplicate_charge_graph,
)
from resolveops.graph.state import DuplicateChargeState
from resolveops.models.contracts import (
    ReadToolName,
    RunStatus,
    TicketInput,
    ToolResult,
    WorkflowEvent,
    WorkflowEventType,
)
from resolveops.repositories.runs import DatabaseRunRepository, ExecutionLease, RunCase
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


class FakePersistence:
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []
        self.attempts: list[dict[str, Any]] = []

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
        lease=lease,
        organization_id=ORGANIZATION_ID,
    )

    assert {
        NORMALIZE_INPUT,
        CLASSIFY_CASE,
        SELECT_INVESTIGATION_RECIPE,
        COLLECT_INITIAL_EVIDENCE,
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
        f"crm:{ACCOUNT_ID}",
        f"subscription:{SUBSCRIPTION_ID}",
        f"invoice:{INVOICE_ID}",
        f"payment_attempt:{PAYMENT_ONE_ID}",
        f"payment_attempt:{PAYMENT_TWO_ID}",
        "policy:billing_duplicate_credit:v3.0",
    }
    assert all(item.integrity_hash is not None for item in result["evidence"])
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
    assert event_types.count(WorkflowEventType.EVIDENCE_ADDED) == 6
    assert (
        event_types.index(WorkflowEventType.TOOL_STARTED)
        < event_types.index(WorkflowEventType.TOOL_COMPLETED)
        < event_types.index(WorkflowEventType.EVIDENCE_ADDED)
    )


def test_run_shell_publishes_only_events_already_recorded_by_graph_persistence() -> None:
    backend = FakeBackend()
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
