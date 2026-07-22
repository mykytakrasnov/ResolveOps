"""First production workflow slice: deterministic duplicate-charge evidence collection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, JsonValue

from resolveops.db.checkpoints import open_async_postgres_saver
from resolveops.graph.state import DuplicateChargeState
from resolveops.models.contracts import (
    ArtifactKind,
    CaseCategory,
    CaseClassification,
    EvidenceClaim,
    EvidenceItem,
    FinalResponse,
    InvestigationPlan,
    PolicyDecision,
    ReadToolName,
    RiskIndicator,
    RunArtifact,
    SourceSystem,
    TicketInput,
    ToolResult,
    Urgency,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowOutcome,
)
from resolveops.policies.duplicate_charge import (
    enforce_duplicate_charge_policy,
    validate_duplicate_charge,
    verify_evidence,
)
from resolveops.repositories.runs import ApprovalGateRecords, ExecutionLease
from resolveops.storage.artifacts import ObjectStorage, StoredObject
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
VERIFY_EVIDENCE = "verify_evidence"
VALIDATE_DUPLICATE_CHARGE = "validate_duplicate_charge"
ENFORCE_POLICY = "enforce_policy"
ESCALATE_CASE = "escalate_case"
DRAFT_RESPONSE = "draft_response"
FINALIZE_RUN = "finalize_run"
APPROVAL_GATE = "approval_gate"
REQUEST_APPROVAL = APPROVAL_GATE
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

    def record_artifact(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        kind: ArtifactKind,
        stored_object: StoredObject,
    ) -> RunArtifact: ...

    def create_approval_gate_records(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        decision: PolicyDecision,
    ) -> ApprovalGateRecords: ...


@dataclass(frozen=True)
class CheckpointedGraphExecution:
    events: list[WorkflowEvent]
    workflow_outcome: WorkflowOutcome
    outcome_reason_code: str
    approval_records: ApprovalGateRecords | None = None


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
    object_storage: ObjectStorage,
    lease: ExecutionLease,
    organization_id: UUID,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    enable_approval_interrupt: bool = False,
) -> CompiledStateGraph[DuplicateChargeState, None, Any, Any]:
    """Build the bounded duplicate-charge investigation and policy graph."""

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
    graph.add_node(
        VERIFY_EVIDENCE,
        cast(
            Any,
            _verification_node(
                persistence=persistence,
                lease=lease,
                organization_id=organization_id,
            ),
        ),
    )
    graph.add_node(
        VALIDATE_DUPLICATE_CHARGE,
        cast(
            Any,
            _with_node_events(
                VALIDATE_DUPLICATE_CHARGE,
                persistence,
                lease,
                organization_id,
                _validate_duplicate_charge_evidence,
            ),
        ),
    )
    graph.add_node(
        ENFORCE_POLICY,
        cast(
            Any,
            _policy_node(
                persistence=persistence,
                lease=lease,
                organization_id=organization_id,
            ),
        ),
    )
    for node_name, operation in ((ESCALATE_CASE, _escalate_case),):
        graph.add_node(
            node_name,
            cast(
                Any,
                _with_node_events(
                    node_name,
                    persistence,
                    lease,
                    organization_id,
                    operation,
                ),
            ),
        )
    graph.add_node(
        APPROVAL_GATE,
        cast(
            Any,
            _approval_gate_node()
            if enable_approval_interrupt
            else _with_node_events(
                APPROVAL_GATE,
                persistence,
                lease,
                organization_id,
                _request_approval,
            ),
        ),
    )
    graph.add_node(
        DRAFT_RESPONSE,
        cast(
            Any,
            _draft_response_node(
                persistence=persistence,
                lease=lease,
                organization_id=organization_id,
            ),
        ),
    )
    graph.add_node(
        FINALIZE_RUN,
        cast(
            Any,
            _finalize_node(
                persistence=persistence,
                object_storage=object_storage,
                lease=lease,
                organization_id=organization_id,
            ),
        ),
    )
    graph.add_edge(START, NORMALIZE_INPUT)
    graph.add_edge(NORMALIZE_INPUT, CLASSIFY_CASE)
    graph.add_edge(CLASSIFY_CASE, SELECT_INVESTIGATION_RECIPE)
    graph.add_edge(SELECT_INVESTIGATION_RECIPE, COLLECT_INITIAL_EVIDENCE)
    graph.add_edge(COLLECT_INITIAL_EVIDENCE, VERIFY_EVIDENCE)
    graph.add_edge(VERIFY_EVIDENCE, VALIDATE_DUPLICATE_CHARGE)
    graph.add_edge(VALIDATE_DUPLICATE_CHARGE, ENFORCE_POLICY)
    graph.add_conditional_edges(
        ENFORCE_POLICY,
        _route_policy_outcome,
        {
            WorkflowOutcome.ESCALATE.value: ESCALATE_CASE,
            WorkflowOutcome.NO_ACTION.value: DRAFT_RESPONSE,
            WorkflowOutcome.APPROVAL_REQUIRED.value: APPROVAL_GATE,
        },
    )
    graph.add_edge(ESCALATE_CASE, DRAFT_RESPONSE)
    graph.add_edge(DRAFT_RESPONSE, FINALIZE_RUN)
    graph.add_edge(FINALIZE_RUN, END)
    graph.add_edge(APPROVAL_GATE, END)
    return graph.compile(checkpointer=checkpointer)


def execute_duplicate_charge_graph(
    *,
    tools: ReadOnlyToolset,
    persistence: WorkflowPersistence,
    object_storage: ObjectStorage,
    lease: ExecutionLease,
    organization_id: UUID,
    ticket: TicketInput,
    case_created_at: datetime,
) -> Generator[WorkflowEvent, None, tuple[WorkflowOutcome, str]]:
    graph = build_duplicate_charge_graph(
        tools=tools,
        persistence=persistence,
        object_storage=object_storage,
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
    workflow_outcome: WorkflowOutcome | None = None
    outcome_reason_code: str | None = None
    for update in graph.stream(initial, stream_mode="updates"):
        for node_update in update.values():
            if not isinstance(node_update, dict):
                continue
            raw_outcome = node_update.get("workflow_outcome")
            if isinstance(raw_outcome, WorkflowOutcome):
                workflow_outcome = raw_outcome
            policy_decision = node_update.get("policy_decision")
            if isinstance(policy_decision, PolicyDecision):
                outcome_reason_code = policy_decision.reason_code
            for event in node_update.get("emitted_events", []):
                if isinstance(event, WorkflowEvent):
                    yield event
    if workflow_outcome is None or outcome_reason_code is None:
        raise RuntimeError("duplicate-charge graph completed without a policy outcome")
    return workflow_outcome, outcome_reason_code


async def execute_checkpointed_duplicate_charge_graph(
    *,
    tools: ReadOnlyToolset,
    persistence: WorkflowPersistence,
    object_storage: ObjectStorage,
    lease: ExecutionLease,
    organization_id: UUID,
    ticket: TicketInput,
    case_created_at: datetime,
    checkpoint_dsn: str,
) -> CheckpointedGraphExecution:
    """Execute with durable PostgreSQL state and verify an interrupt before returning."""

    initial: DuplicateChargeState = {
        "run_id": lease.run_id,
        "organization_id": organization_id,
        "ticket": ticket,
        "case_created_at": case_created_at.isoformat(),
        "evidence": [],
        "tool_errors": [],
        "emitted_events": [],
    }
    config: dict[str, Any] = {"configurable": {"thread_id": str(lease.run_id)}}
    events: list[WorkflowEvent] = []
    decision: PolicyDecision | None = None
    workflow_outcome: WorkflowOutcome | None = None

    async with open_async_postgres_saver(checkpoint_dsn) as checkpointer:
        graph = build_duplicate_charge_graph(
            tools=tools,
            persistence=persistence,
            object_storage=object_storage,
            lease=lease,
            organization_id=organization_id,
            checkpointer=checkpointer,
            enable_approval_interrupt=True,
        )
        async for update in graph.astream(
            initial,
            config=cast(Any, config),
            stream_mode="updates",
        ):
            for node_update in update.values():
                if not isinstance(node_update, dict):
                    continue
                raw_decision = node_update.get("policy_decision")
                if isinstance(raw_decision, PolicyDecision):
                    decision = raw_decision
                raw_outcome = node_update.get("workflow_outcome")
                if isinstance(raw_outcome, WorkflowOutcome):
                    workflow_outcome = raw_outcome
                events.extend(
                    event
                    for event in node_update.get("emitted_events", [])
                    if isinstance(event, WorkflowEvent)
                )

        if decision is None:
            raise RuntimeError("checkpointed graph stopped without a policy decision")
        if workflow_outcome is None:
            workflow_outcome = decision.outcome

        approval_records = None
        if workflow_outcome is WorkflowOutcome.APPROVAL_REQUIRED:
            checkpoint = await checkpointer.aget_tuple(cast(Any, config))
            snapshot = await graph.aget_state(cast(Any, config))
            if checkpoint is None or not any(task.interrupts for task in snapshot.tasks):
                raise RuntimeError("approval graph stopped without a durable interrupt checkpoint")
            # The node already inserted these rows before calling interrupt(). Re-read
            # through the replay-safe seam after checkpoint durability is proven.
            approval_records = persistence.create_approval_gate_records(
                lease=lease,
                organization_id=organization_id,
                decision=decision,
            )

    return CheckpointedGraphExecution(
        events=events,
        workflow_outcome=workflow_outcome,
        outcome_reason_code=decision.reason_code,
        approval_records=approval_records,
    )


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
        case_report = _case_report_evidence(state)
        observer = _PersistingObserver(
            persistence=persistence,
            lease=lease,
            organization_id=organization_id,
            events=events,
        )
        collected, errors, account_id, invoice_ids = _collect_evidence(state, tools, observer)
        evidence = [case_report, *collected]
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


def _verification_node(
    *,
    persistence: WorkflowPersistence,
    lease: ExecutionLease,
    organization_id: UUID,
) -> Callable[[DuplicateChargeState], DuplicateChargeState]:
    def node(state: DuplicateChargeState) -> DuplicateChargeState:
        started = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_STARTED,
            node_name=VERIFY_EVIDENCE,
            status="running",
            public_payload={"summary": "Evidence verification started."},
        )
        evidence = state.get("evidence", [])
        result = verify_evidence(
            evidence=evidence,
            cited_evidence_ids=[item.evidence_id for item in evidence],
            claims=[
                EvidenceClaim(fact=item.fact, cited_evidence_ids=[item.evidence_id])
                for item in evidence
            ],
        )
        verified = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.EVIDENCE_VERIFIED,
            node_name=VERIFY_EVIDENCE,
            status="verified" if result.verified else "rejected",
            public_payload={
                "summary": "Evidence passed deterministic verification."
                if result.verified
                else "Evidence requires escalation after deterministic verification.",
                "verified": result.verified,
                "completeness_score": result.completeness_score,
                "missing_evidence_types": cast(JsonValue, result.missing_evidence_types),
                "hallucinated_evidence_id_count": len(result.hallucinated_evidence_ids),
                "unsupported_claim_count": result.unsupported_claim_count,
                "contradiction_count": len(result.contradictions),
            },
        )
        completed = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_COMPLETED,
            node_name=VERIFY_EVIDENCE,
            status="completed" if result.verified else "completed_with_errors",
            public_payload={"summary": "Evidence verification completed."},
        )
        return {"evidence_verification": result, "emitted_events": [started, verified, completed]}

    return node


def _validate_duplicate_charge_evidence(state: DuplicateChargeState) -> DuplicateChargeState:
    return {"duplicate_charge_validation": validate_duplicate_charge(state.get("evidence", []))}


def _policy_node(
    *,
    persistence: WorkflowPersistence,
    lease: ExecutionLease,
    organization_id: UUID,
) -> Callable[[DuplicateChargeState], DuplicateChargeState]:
    def node(state: DuplicateChargeState) -> DuplicateChargeState:
        started = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_STARTED,
            node_name=ENFORCE_POLICY,
            status="running",
            public_payload={"summary": "Billing-credit policy evaluation started."},
        )
        decision = enforce_duplicate_charge_policy(
            evidence=state.get("evidence", []),
            verification=state["evidence_verification"],
            validation=state["duplicate_charge_validation"],
        )
        evaluated = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.POLICY_EVALUATED,
            node_name=ENFORCE_POLICY,
            status="completed",
            public_payload=_public_policy_summary(decision),
        )
        completed = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_COMPLETED,
            node_name=ENFORCE_POLICY,
            status="completed",
            public_payload={"summary": "Billing-credit policy evaluation completed."},
        )
        return {"policy_decision": decision, "emitted_events": [started, evaluated, completed]}

    return node


def _public_policy_summary(decision: PolicyDecision) -> dict[str, JsonValue]:
    amount = decision.canonical_parameters.get("amount_cents")
    payload: dict[str, JsonValue] = {
        "summary": "Deterministic billing-credit policy evaluated.",
        "outcome": decision.outcome.value,
        "risk_level": decision.risk_level.value,
        "reason_code": decision.reason_code,
        "approval_required": decision.approval_required,
    }
    if isinstance(amount, int):
        payload["allowed_credit_cents"] = amount
    if decision.policy_key is not None:
        payload["policy_key"] = decision.policy_key
    if decision.policy_version is not None:
        payload["policy_version"] = decision.policy_version
    return payload


def _route_policy_outcome(state: DuplicateChargeState) -> str:
    return state["policy_decision"].outcome.value


def _escalate_case(state: DuplicateChargeState) -> DuplicateChargeState:
    del state
    return {"workflow_outcome": WorkflowOutcome.ESCALATE}


def _request_approval(state: DuplicateChargeState) -> DuplicateChargeState:
    del state
    return {"workflow_outcome": WorkflowOutcome.APPROVAL_REQUIRED}


def _approval_gate_node() -> Callable[[DuplicateChargeState], DuplicateChargeState]:
    def node(state: DuplicateChargeState) -> DuplicateChargeState:
        decision = state["policy_decision"]
        interrupt(
            {
                "action_type": decision.action_type.value if decision.action_type else None,
                "target_reference": decision.target_reference,
                "canonical_parameters": cast(JsonValue, decision.canonical_parameters),
                "risk_level": decision.risk_level.value,
                "policy_key": decision.policy_key,
                "policy_version": decision.policy_version,
                "reason_code": decision.reason_code,
            }
        )
        # Resume and decision handling belong to a later issue. Even a caller that
        # manually supplies a resume value cannot cross into action execution here.
        raise RuntimeError("approval decision resume is not implemented")

    return node


def _draft_response_node(
    *,
    persistence: WorkflowPersistence,
    lease: ExecutionLease,
    organization_id: UUID,
) -> Callable[[DuplicateChargeState], DuplicateChargeState]:
    def node(state: DuplicateChargeState) -> DuplicateChargeState:
        outcome = state.get("workflow_outcome", state["policy_decision"].outcome)
        started = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_STARTED,
            node_name=DRAFT_RESPONSE,
            status="running",
            public_payload={"summary": "Deterministic response drafting started."},
        )
        final_response = _deterministic_fallback_draft(state, outcome=outcome)
        fallback = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.MODEL_FALLBACK,
            node_name=DRAFT_RESPONSE,
            status="completed",
            public_payload={
                "summary": "A deterministic evidence-cited response template was used.",
                "fallback": "deterministic_template",
            },
        )
        completed = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_COMPLETED,
            node_name=DRAFT_RESPONSE,
            status="completed",
            public_payload={
                "summary": "Customer response and internal note drafted.",
                "citation_count": len(final_response.cited_evidence_ids),
                "uncertainty_disclosed": final_response.uncertainty_disclosure is not None,
            },
        )
        return {
            "final_response": final_response,
            "workflow_outcome": outcome,
            "emitted_events": [started, fallback, completed],
        }

    return node


def _deterministic_fallback_draft(
    state: DuplicateChargeState,
    *,
    outcome: WorkflowOutcome,
) -> FinalResponse:
    decision = state["policy_decision"]
    verification = state["evidence_verification"]
    citations = verification.validated_evidence_ids
    if not citations:
        raise RuntimeError("cannot draft a final response without a validated evidence citation")
    citation_text = ", ".join(f"[{evidence_id}]" for evidence_id in citations)
    if outcome is WorkflowOutcome.NO_ACTION:
        body = (
            "We reviewed the available synthetic AtlasFlow billing records and found only one "
            "successful payment for the billing period, so we did not make an account change. "
            f"Evidence reviewed: {citation_text}."
        )
        internal_note = (
            "Deterministic duplicate-charge validation did not confirm a duplicate payment. "
            f"Reason code: {decision.reason_code}. Evidence: {citation_text}."
        )
        uncertainty = (
            "This conclusion is limited to the synthetic records available during this review; "
            "new or changed billing records should trigger another investigation."
        )
    elif outcome is WorkflowOutcome.ESCALATE:
        missing = ", ".join(verification.missing_evidence_types) or "none identified"
        body = (
            "We could not verify the reported duplicate charge with enough synthetic evidence. "
            "We have routed the case for specialist review and made no account change. "
            f"Evidence available: {citation_text}."
        )
        internal_note = (
            f"Escalated after deterministic review. Reason code: {decision.reason_code}. "
            f"Missing evidence types: {missing}. Evidence: {citation_text}."
        )
        uncertainty = (
            "The available synthetic records are incomplete or do not support a safe automated "
            "conclusion; a specialist must verify the missing information."
        )
    else:  # pragma: no cover - graph routing excludes approval-required drafting
        raise RuntimeError("approval-required outcomes cannot be drafted or finalized")
    return FinalResponse(
        subject="Update on your AtlasFlow billing investigation",
        body=body,
        internal_case_note=internal_note,
        cited_evidence_ids=citations,
        uncertainty_disclosure=uncertainty,
    )


def _finalize_node(
    *,
    persistence: WorkflowPersistence,
    object_storage: ObjectStorage,
    lease: ExecutionLease,
    organization_id: UUID,
) -> Callable[[DuplicateChargeState], DuplicateChargeState]:
    def node(state: DuplicateChargeState) -> DuplicateChargeState:
        outcome = state["workflow_outcome"]
        if outcome is WorkflowOutcome.APPROVAL_REQUIRED:
            raise RuntimeError("approval-required outcomes cannot be finalized")
        started = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_STARTED,
            node_name=FINALIZE_RUN,
            status="running",
            public_payload={"summary": "Run report finalization started."},
        )
        report = _structured_report(state)
        json_content = (
            json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        ).encode()
        markdown_content = _markdown_report(state).encode()
        stored_objects = (
            (
                ArtifactKind.JSON_REPORT,
                object_storage.put_object(
                    object_key=f"runs/{lease.run_id}/report.json",
                    content=json_content,
                    mime_type="application/json",
                ),
            ),
            (
                ArtifactKind.MARKDOWN_BRIEF,
                object_storage.put_object(
                    object_key=f"runs/{lease.run_id}/report.md",
                    content=markdown_content,
                    mime_type="text/markdown; charset=utf-8",
                ),
            ),
        )
        artifacts = [
            persistence.record_artifact(
                lease=lease,
                organization_id=organization_id,
                kind=kind,
                stored_object=stored,
            )
            for kind, stored in stored_objects
        ]
        completed = persistence.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.NODE_COMPLETED,
            node_name=FINALIZE_RUN,
            status="completed",
            public_payload={
                "summary": "Structured JSON and Markdown reports were finalized.",
                "artifact_kinds": cast(JsonValue, [artifact.kind.value for artifact in artifacts]),
            },
        )
        return {"finalized_artifacts": artifacts, "emitted_events": [started, completed]}

    return node


def _structured_report(state: DuplicateChargeState) -> dict[str, JsonValue]:
    return {
        "schema_version": "1.0",
        "run_id": str(state["run_id"]),
        "workflow_outcome": state["workflow_outcome"].value,
        "reason_code": state["policy_decision"].reason_code,
        "final_response": cast(JsonValue, state["final_response"].model_dump(mode="json")),
        "evidence": cast(
            JsonValue,
            [
                {
                    "evidence_id": item.evidence_id,
                    "source_system": item.source_system.value,
                    "source_object_type": item.source_object_type,
                    "source_object_id": item.source_object_id,
                    "fact": item.fact,
                    "integrity_hash": item.integrity_hash,
                }
                for item in state.get("evidence", [])
            ],
        ),
        "evidence_verification": cast(
            JsonValue, state["evidence_verification"].model_dump(mode="json")
        ),
    }


def _markdown_report(state: DuplicateChargeState) -> str:
    response = state["final_response"]
    evidence_by_id = {item.evidence_id: item for item in state.get("evidence", [])}
    citations = "\n".join(
        f"- `{evidence_id}` — {evidence_by_id[evidence_id].fact}"
        for evidence_id in response.cited_evidence_ids
    )
    return (
        "# AtlasFlow billing investigation\n\n"
        f"Outcome: `{state['workflow_outcome'].value}`  \n"
        f"Reason code: `{state['policy_decision'].reason_code}`\n\n"
        "## Customer response\n\n"
        f"**{response.subject}**\n\n{response.body}\n\n"
        "## Internal note\n\n"
        f"{response.internal_case_note}\n\n"
        "## Uncertainty disclosure\n\n"
        f"{response.uncertainty_disclosure}\n\n"
        "## Evidence citations\n\n"
        f"{citations}\n"
    )


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


def _case_report_evidence(state: DuplicateChargeState) -> EvidenceItem:
    ticket = state["ticket"]
    run_id = state["run_id"]
    return EvidenceItem(
        evidence_id=f"case_report:{run_id}",
        source_system=SourceSystem.CASE_HISTORY,
        source_object_type="support_case_report",
        source_object_id=str(run_id),
        observed_at=datetime.fromisoformat(state["case_created_at"]),
        fact=(
            "The synthetic support case reported a possible duplicate charge for "
            f"customer reference {ticket.customer_reference}."
        ),
        structured_fields={
            "customer_reference": ticket.customer_reference,
            "reported_category": CaseCategory.DUPLICATE_CHARGE.value,
        },
        integrity_hash=_integrity_hash(ticket),
    )


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
