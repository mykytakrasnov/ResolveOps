"""First production workflow slice: deterministic duplicate-charge evidence collection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, JsonValue

from resolveops.graph.state import DuplicateChargeState
from resolveops.models.contracts import (
    CaseCategory,
    CaseClassification,
    EvidenceItem,
    InvestigationPlan,
    ReadToolName,
    RiskIndicator,
    SourceSystem,
    TicketInput,
    ToolResult,
    Urgency,
    WorkflowEvent,
    WorkflowEventType,
)
from resolveops.repositories.runs import ExecutionLease
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
from resolveops.tools.read_only import ReadOnlyToolset, ToolAttemptObserver

NORMALIZE_INPUT = "normalize_input"
CLASSIFY_CASE = "classify_case"
SELECT_INVESTIGATION_RECIPE = "select_investigation_recipe"
COLLECT_INITIAL_EVIDENCE = "collect_initial_evidence"
DUPLICATE_CHARGE_RECIPE = "duplicate_charge_v1"
BILLING_POLICY_KEY = "billing_duplicate_credit"
BILLING_POLICY_VERSION = "3.0"
MAX_INVOICES_FOR_PAYMENT_LOOKUP = 6


class WorkflowPersistence(Protocol):
    def append_event(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        event_type: WorkflowEventType,
        status: str,
        public_payload: dict[str, JsonValue],
        node_name: str | None = None,
    ) -> WorkflowEvent: ...

    def start_tool_attempt(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        tool_call_id: str,
        tool_name: ReadToolName,
        attempt: int,
        request_summary: dict[str, str | int],
    ) -> None: ...

    def finish_tool_attempt(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        tool_call_id: str,
        tool_name: ReadToolName,
        result: ToolResult[BaseModel],
        response_summary: dict[str, str | int | list[str]],
    ) -> None: ...


class _PersistingObserver(ToolAttemptObserver):
    def __init__(
        self,
        *,
        persistence: WorkflowPersistence,
        lease: ExecutionLease,
        organization_id: UUID,
        events: list[WorkflowEvent],
    ) -> None:
        self._persistence = persistence
        self._lease = lease
        self._organization_id = organization_id
        self._events = events

    def _execution_tool_call_id(self, tool_call_id: str) -> str:
        return f"execution-{self._lease.attempt}:{tool_call_id}"

    def started(
        self,
        *,
        tool_call_id: str,
        tool_name: ReadToolName,
        attempt: int,
        request_summary: dict[str, str | int],
    ) -> None:
        persisted_tool_call_id = self._execution_tool_call_id(tool_call_id)
        self._persistence.start_tool_attempt(
            lease=self._lease,
            organization_id=self._organization_id,
            tool_call_id=persisted_tool_call_id,
            tool_name=tool_name,
            attempt=attempt,
            request_summary=request_summary,
        )
        self._events.append(
            self._persistence.append_event(
                lease=self._lease,
                organization_id=self._organization_id,
                event_type=WorkflowEventType.TOOL_STARTED,
                node_name=COLLECT_INITIAL_EVIDENCE,
                status="running",
                public_payload={"tool": tool_name.value, "attempt": attempt},
            )
        )

    def finished(
        self,
        *,
        tool_call_id: str,
        tool_name: ReadToolName,
        result: ToolResult[BaseModel],
        response_summary: dict[str, str | int | list[str]],
        will_retry: bool,
    ) -> None:
        persisted_tool_call_id = self._execution_tool_call_id(tool_call_id)
        self._persistence.finish_tool_attempt(
            lease=self._lease,
            organization_id=self._organization_id,
            tool_call_id=persisted_tool_call_id,
            tool_name=tool_name,
            result=result,
            response_summary=response_summary,
        )
        if result.ok:
            event_type = WorkflowEventType.TOOL_COMPLETED
            status = "completed"
            payload: dict[str, JsonValue] = {
                "tool": tool_name.value,
                "attempt": result.attempt,
                "source_ids": cast(JsonValue, result.source_ids),
                "summary": f"{len(result.source_ids)} source object(s) retrieved.",
            }
        else:
            event_type = WorkflowEventType.TOOL_FAILED
            status = "retrying" if will_retry else "failed"
            payload = {
                "tool": tool_name.value,
                "attempt": result.attempt,
                "error_code": result.error_code or "unknown",
                "retrying": will_retry,
            }
        self._events.append(
            self._persistence.append_event(
                lease=self._lease,
                organization_id=self._organization_id,
                event_type=event_type,
                node_name=COLLECT_INITIAL_EVIDENCE,
                status=status,
                public_payload=payload,
            )
        )


def build_duplicate_charge_graph(
    *,
    tools: ReadOnlyToolset,
    persistence: WorkflowPersistence,
    lease: ExecutionLease,
    organization_id: UUID,
) -> CompiledStateGraph[DuplicateChargeState, None, Any, Any]:
    """Build the explicit four-node graph for the curated duplicate-charge recipe."""

    graph = StateGraph(DuplicateChargeState)
    graph.add_node(
        NORMALIZE_INPUT,
        cast(
            Any,
            _with_node_events(
                NORMALIZE_INPUT,
                persistence,
                lease,
                organization_id,
                _normalize_input,
            ),
        ),
    )
    graph.add_node(
        CLASSIFY_CASE,
        cast(
            Any,
            _with_node_events(
                CLASSIFY_CASE,
                persistence,
                lease,
                organization_id,
                _classify_case,
            ),
        ),
    )
    graph.add_node(
        SELECT_INVESTIGATION_RECIPE,
        cast(
            Any,
            _with_node_events(
                SELECT_INVESTIGATION_RECIPE,
                persistence,
                lease,
                organization_id,
                _select_investigation_recipe,
            ),
        ),
    )
    graph.add_node(
        COLLECT_INITIAL_EVIDENCE,
        cast(
            Any,
            _collect_node(
                tools=tools,
                persistence=persistence,
                lease=lease,
                organization_id=organization_id,
            ),
        ),
    )
    graph.add_edge(START, NORMALIZE_INPUT)
    graph.add_edge(NORMALIZE_INPUT, CLASSIFY_CASE)
    graph.add_edge(CLASSIFY_CASE, SELECT_INVESTIGATION_RECIPE)
    graph.add_edge(SELECT_INVESTIGATION_RECIPE, COLLECT_INITIAL_EVIDENCE)
    graph.add_edge(COLLECT_INITIAL_EVIDENCE, END)
    return graph.compile()


def execute_duplicate_charge_graph(
    *,
    tools: ReadOnlyToolset,
    persistence: WorkflowPersistence,
    lease: ExecutionLease,
    organization_id: UUID,
    ticket: TicketInput,
    case_created_at: datetime,
) -> Iterator[WorkflowEvent]:
    graph = build_duplicate_charge_graph(
        tools=tools,
        persistence=persistence,
        lease=lease,
        organization_id=organization_id,
    )
    initial: DuplicateChargeState = {
        "run_id": lease.run_id,
        "organization_id": organization_id,
        "ticket": ticket,
        "case_created_at": case_created_at.isoformat(),
        "evidence": [],
        "tool_errors": [],
        "emitted_events": [],
    }
    for update in graph.stream(initial, stream_mode="updates"):
        for node_update in update.values():
            if not isinstance(node_update, dict):
                continue
            for event in node_update.get("emitted_events", []):
                if isinstance(event, WorkflowEvent):
                    yield event


def _with_node_events(
    node_name: str,
    persistence: WorkflowPersistence,
    lease: ExecutionLease,
    organization_id: UUID,
    operation: Callable[[DuplicateChargeState], DuplicateChargeState],
) -> Callable[[DuplicateChargeState], DuplicateChargeState]:
    def node(state: DuplicateChargeState) -> DuplicateChargeState:
        started = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_STARTED,
            node_name=node_name,
            status="running",
            public_payload={"summary": f"{node_name} started."},
        )
        update = operation(state)
        completed = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_COMPLETED,
            node_name=node_name,
            status="completed",
            public_payload={"summary": f"{node_name} completed."},
        )
        update["emitted_events"] = [started, completed]
        return update

    return node


def _normalize_input(state: DuplicateChargeState) -> DuplicateChargeState:
    ticket = TicketInput.model_validate(state["ticket"])
    normalized = ticket.model_copy(update={"customer_reference": ticket.customer_reference.lower()})
    return {"ticket": normalized}


def _classify_case(state: DuplicateChargeState) -> DuplicateChargeState:
    ticket = state["ticket"]
    content = f"{ticket.subject} {ticket.body}".lower()
    duplicate_terms = ("charged twice", "duplicate charge", "two completed charges")
    category = (
        CaseCategory.DUPLICATE_CHARGE
        if any(term in content for term in duplicate_terms)
        else CaseCategory.UNKNOWN
    )
    classification = CaseClassification(
        category=category,
        urgency=Urgency.NORMAL,
        confidence=1.0 if category is CaseCategory.DUPLICATE_CHARGE else 0.0,
        suspected_account_reference=ticket.customer_reference,
        requested_outcome="Investigate the reported billing charges.",
        risk_indicators=[RiskIndicator.UNSUPPORTED_ACTION]
        if category is CaseCategory.UNKNOWN
        else [],
    )
    return {"classification": classification}


def _select_investigation_recipe(state: DuplicateChargeState) -> DuplicateChargeState:
    classification = state["classification"]
    if classification.category is not CaseCategory.DUPLICATE_CHARGE:
        raise ValueError("only duplicate-charge cases are supported by this graph slice")
    return {
        "investigation_plan": InvestigationPlan(
            recipe_id=DUPLICATE_CHARGE_RECIPE,
            category=CaseCategory.DUPLICATE_CHARGE,
            required_tools=[
                ReadToolName.LOOKUP_CUSTOMER,
                ReadToolName.GET_SUBSCRIPTION,
                ReadToolName.LIST_INVOICES,
                ReadToolName.GET_PAYMENT_ATTEMPTS,
                ReadToolName.GET_POLICY,
            ],
            max_additional_rounds=0,
        )
    }


def _collect_node(
    *,
    tools: ReadOnlyToolset,
    persistence: WorkflowPersistence,
    lease: ExecutionLease,
    organization_id: UUID,
) -> Callable[[DuplicateChargeState], DuplicateChargeState]:
    def node(state: DuplicateChargeState) -> DuplicateChargeState:
        events = [
            persistence.append_event(
                lease=lease,
                organization_id=organization_id,
                event_type=WorkflowEventType.NODE_STARTED,
                node_name=COLLECT_INITIAL_EVIDENCE,
                status="running",
                public_payload={"summary": "collect_initial_evidence started."},
            )
        ]
        observer = _PersistingObserver(
            persistence=persistence,
            lease=lease,
            organization_id=organization_id,
            events=events,
        )
        evidence, errors, account_id, invoice_ids = _collect_evidence(state, tools, observer)
        for item in evidence:
            events.append(
                persistence.append_event(
                    lease=lease,
                    organization_id=organization_id,
                    event_type=WorkflowEventType.EVIDENCE_ADDED,
                    node_name=COLLECT_INITIAL_EVIDENCE,
                    status="completed",
                    public_payload={
                        "evidence_id": item.evidence_id,
                        "source_system": item.source_system.value,
                        "object_type": item.source_object_type,
                        "object_id": item.source_object_id,
                        "fact": item.fact,
                    },
                )
            )
        events.append(
            persistence.append_event(
                lease=lease,
                organization_id=organization_id,
                event_type=WorkflowEventType.NODE_COMPLETED,
                node_name=COLLECT_INITIAL_EVIDENCE,
                status="completed" if not errors else "completed_with_errors",
                public_payload={
                    "summary": f"{len(evidence)} evidence item(s) collected.",
                    "error_count": len(errors),
                },
            )
        )
        return {
            "account_id": account_id,
            "invoice_ids": invoice_ids,
            "evidence": evidence,
            "tool_errors": errors,
            "emitted_events": events,
        }

    return node


def _collect_evidence(
    state: DuplicateChargeState,
    tools: ReadOnlyToolset,
    observer: ToolAttemptObserver,
) -> tuple[list[EvidenceItem], list[str], str, list[str]]:
    evidence: list[EvidenceItem] = []
    errors: list[str] = []
    ticket = state["ticket"]
    customer_result = tools.lookup_customer(
        LookupCustomerInput(customer_reference=ticket.customer_reference),
        expected_customer_reference=ticket.customer_reference,
        observer=observer,
    )
    customer = _successful_data(customer_result, ReadToolName.LOOKUP_CUSTOMER, errors)
    if customer is None:
        return evidence, errors, "", []
    if customer.customer_reference != ticket.customer_reference:
        raise ValueError("customer lookup returned an object outside the active case")
    account_id = str(customer.account_id)
    evidence.append(_customer_evidence(customer, customer_result))

    subscription_result = tools.get_subscription(
        GetSubscriptionInput(account_id=customer.account_id),
        owned_account_id=account_id,
        observer=observer,
    )
    subscription = _successful_data(subscription_result, ReadToolName.GET_SUBSCRIPTION, errors)
    if subscription is not None:
        if str(subscription.account_id) != account_id:
            raise ValueError("subscription returned an object outside the active account")
        evidence.append(_subscription_evidence(subscription, subscription_result))

    case_created_at = datetime.fromisoformat(state["case_created_at"])
    invoices_result = tools.list_invoices(
        ListInvoicesInput(
            account_id=customer.account_id,
            from_date=(case_created_at - timedelta(days=62)).date(),
            to_date=case_created_at.date(),
        ),
        owned_account_id=account_id,
        observer=observer,
    )
    invoices = _successful_data(invoices_result, ReadToolName.LIST_INVOICES, errors)
    invoice_items = invoices.items if invoices is not None else []
    for invoice in invoice_items:
        if str(invoice.account_id) != account_id:
            raise ValueError("invoice list returned an object outside the active account")
        evidence.append(_invoice_evidence(invoice, invoices_result))
    invoice_ids = [str(invoice.invoice_id) for invoice in invoice_items]

    allowed_invoice_ids = frozenset(invoice_ids)
    for invoice in invoice_items[:MAX_INVOICES_FOR_PAYMENT_LOOKUP]:
        attempts_result = tools.get_payment_attempts(
            GetPaymentAttemptsInput(
                account_id=customer.account_id,
                invoice_id=invoice.invoice_id,
            ),
            owned_account_id=account_id,
            allowed_invoice_ids=allowed_invoice_ids,
            observer=observer,
        )
        attempts = _successful_data(attempts_result, ReadToolName.GET_PAYMENT_ATTEMPTS, errors)
        for attempt in attempts.items if attempts is not None else []:
            if str(attempt.account_id) != account_id or attempt.invoice_id != invoice.invoice_id:
                raise ValueError("payment attempt returned an object outside invoice ownership")
            evidence.append(_payment_evidence(attempt, attempts_result))

    policy_result = tools.get_policy(
        GetPolicyInput(policy_key=BILLING_POLICY_KEY, version=BILLING_POLICY_VERSION),
        observer=observer,
    )
    policy = _successful_data(policy_result, ReadToolName.GET_POLICY, errors)
    if policy is not None:
        if policy.policy_key != BILLING_POLICY_KEY or policy.version != BILLING_POLICY_VERSION:
            raise ValueError("policy tool returned a different immutable version")
        evidence.append(_policy_evidence(policy, policy_result))
    return evidence, errors, account_id, invoice_ids


def _successful_data[T: BaseModel](
    result: ToolResult[T], tool_name: ReadToolName, errors: list[str]
) -> T | None:
    if result.ok and result.data is not None:
        return result.data
    errors.append(f"{tool_name.value}:{result.error_code or 'unknown'}")
    return None


def _integrity_hash(record: BaseModel) -> str:
    payload = json.dumps(
        record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _customer_evidence(item: CustomerRecord, result: ToolResult[CustomerRecord]) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"crm:{item.account_id}",
        source_system=SourceSystem.CRM,
        source_object_type="customer_account",
        source_object_id=str(item.account_id),
        observed_at=result.observed_at,
        fact=f"Customer account {item.customer_reference} is {item.status} in {item.region}.",
        structured_fields={
            "customer_reference": item.customer_reference,
            "status": item.status,
            "region": item.region,
        },
        integrity_hash=_integrity_hash(item),
    )


def _subscription_evidence(
    item: SubscriptionRecord, result: ToolResult[SubscriptionRecord]
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"subscription:{item.subscription_id}",
        source_system=SourceSystem.BILLING,
        source_object_type="subscription",
        source_object_id=str(item.subscription_id),
        observed_at=result.observed_at,
        fact=f"Subscription {item.subscription_id} is {item.status} on the {item.plan} plan.",
        structured_fields={
            "account_id": str(item.account_id),
            "status": item.status,
            "plan": item.plan,
            "amount_cents": item.amount_cents,
            "currency": item.currency,
            "period_start": item.current_period_start.isoformat(),
            "period_end": item.current_period_end.isoformat(),
            "previous_plan": item.previous_plan,
            "upgraded_at": item.upgraded_at.isoformat() if item.upgraded_at else None,
            "canceled_at": item.canceled_at.isoformat() if item.canceled_at else None,
            "plan_limit_units": item.plan_limit_units,
            "usage_units": item.usage_units,
        },
        integrity_hash=_integrity_hash(item),
    )


def _invoice_evidence(item: InvoiceRecord, result: ToolResult[InvoicePage]) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"invoice:{item.invoice_id}",
        source_system=SourceSystem.BILLING,
        source_object_type="invoice",
        source_object_id=str(item.invoice_id),
        observed_at=result.observed_at,
        fact=(
            f"Invoice {item.invoice_id} is {item.status} for "
            f"{item.amount_cents} {item.currency} cents."
        ),
        structured_fields={
            "account_id": str(item.account_id),
            "subscription_id": str(item.subscription_id),
            "amount_cents": item.amount_cents,
            "currency": item.currency,
            "status": item.status,
            "period_start": item.period_start.isoformat(),
            "period_end": item.period_end.isoformat(),
        },
        integrity_hash=_integrity_hash(item),
    )


def _payment_evidence(
    item: PaymentAttemptRecord, result: ToolResult[PaymentAttemptPage]
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"payment_attempt:{item.payment_attempt_id}",
        source_system=SourceSystem.BILLING,
        source_object_type="payment_attempt",
        source_object_id=str(item.payment_attempt_id),
        observed_at=result.observed_at,
        fact=(
            f"Payment attempt {item.payment_attempt_id} is {item.status} for "
            f"{item.amount_cents} {item.currency} cents."
        ),
        structured_fields={
            "account_id": str(item.account_id),
            "invoice_id": str(item.invoice_id),
            "amount_cents": item.amount_cents,
            "currency": item.currency,
            "status": item.status,
            "attempted_at": item.attempted_at.isoformat(),
        },
        integrity_hash=_integrity_hash(item),
    )


def _policy_evidence(item: PolicyRecord, result: ToolResult[PolicyRecord]) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"policy:{item.policy_key}:v{item.version}",
        source_system=SourceSystem.POLICY,
        source_object_type="policy",
        source_object_id=str(item.policy_id),
        observed_at=result.observed_at,
        fact=(
            f"Policy {item.policy_key} version {item.version} requires approval: "
            f"{item.approval_required}."
        ),
        structured_fields={
            "policy_key": item.policy_key,
            "version": item.version,
            "action_type": item.action_type,
            "maximum_amount_cents": cast(JsonValue, item.maximum_amount_cents),
            "approval_required": item.approval_required,
        },
        integrity_hash=_integrity_hash(item),
    )
