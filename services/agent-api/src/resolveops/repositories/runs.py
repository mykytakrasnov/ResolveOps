"""PostgreSQL persistence for run identity, leases, and append-only events."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, JsonValue

from resolveops.models.contracts import (
    ActionProposal,
    ApprovalDecision,
    ApprovalDecisionType,
    ApprovalRequest,
    ArtifactKind,
    PolicyDecision,
    ProposalStatus,
    ReadToolName,
    RunArtifact,
    RunError,
    RunStatus,
    TicketInput,
    ToolResult,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowRun,
)
from resolveops.models.run_api import ApprovalEvidence, ApprovalQueueItem
from resolveops.storage.artifacts import StoredObject

GRAPH_VERSION = "1.0.0"
PROMPT_BUNDLE_VERSION = "1.0.0"
DATASET_VERSION = "v1"
IDEMPOTENCY_TTL_HOURS = 24
EVENT_PAGE_SIZE = 500
DB_CONNECT_TIMEOUT_SECONDS = 5
DB_STATEMENT_TIMEOUT_MILLISECONDS = 10_000
DB_LOCK_TIMEOUT_MILLISECONDS = 5_000
RECOVERABLE_RUN_ERROR_CODES = frozenset({"run_shell_failed"})


class RunRepositoryError(Exception):
    """Base class for errors safe to translate at the API boundary."""


class RunNotFoundError(RunRepositoryError):
    pass


class CaseNotFoundError(RunRepositoryError):
    pass


class IdempotencyConflictError(RunRepositoryError):
    pass


class ExecutionLeaseConflictError(RunRepositoryError):
    pass


class RunNotExecutableError(RunRepositoryError):
    pass


class LostExecutionLeaseError(RunRepositoryError):
    pass


class ProposalReplayConflictError(RunRepositoryError):
    pass


class StaleProposalError(RunRepositoryError):
    pass


class ApprovalDecisionConflictError(RunRepositoryError):
    pass


@dataclass(frozen=True)
class CreatedRun:
    run: WorkflowRun
    idempotent_replay: bool


@dataclass(frozen=True)
class ExecutionLease:
    run_id: UUID
    token: UUID
    attempt: int
    expires_at: datetime


@dataclass(frozen=True)
class ExecutionStart:
    lease: ExecutionLease | None
    idempotent_replay: bool


@dataclass(frozen=True)
class RunCase:
    ticket: TicketInput
    created_at: datetime


@dataclass(frozen=True)
class ApprovalGateRecords:
    proposal: ActionProposal
    approval_request: ApprovalRequest
    cited_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecordedApprovalDecision:
    approval_request: ApprovalRequest
    idempotent_replay: bool
    lease: ExecutionLease | None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _normalize_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


class DatabaseRunRepository:
    """Runs each state transition in a short PostgreSQL transaction."""

    def __init__(self, dsn: str) -> None:
        self._dsn = _normalize_dsn(dsn)

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(
            self._dsn,
            row_factory=dict_row,
            connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
            options=(
                f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MILLISECONDS} "
                f"-c lock_timeout={DB_LOCK_TIMEOUT_MILLISECONDS}"
            ),
        )

    def create_run(
        self,
        *,
        organization_id: UUID,
        actor_user_id: UUID,
        case_id: UUID,
        idempotency_key: UUID,
    ) -> CreatedRun:
        scope = f"run-create:{organization_id}:{actor_user_id}"
        request_hash = _sha256({"case_id": str(case_id)})

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{scope}:{idempotency_key}",),
            )
            cursor.execute(
                """
                SELECT request_hash, response_body
                FROM app.idempotency_records
                WHERE scope = %s AND key = %s AND expires_at > CURRENT_TIMESTAMP
                """,
                (scope, str(idempotency_key)),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflictError(
                        "the idempotency key was already used for a different request"
                    )
                if existing["response_body"] is None:
                    raise RunRepositoryError("idempotency response is incomplete")
                return CreatedRun(
                    run=WorkflowRun.model_validate(existing["response_body"]["run"]),
                    idempotent_replay=True,
                )

            cursor.execute(
                """
                DELETE FROM app.idempotency_records
                WHERE scope = %s AND key = %s AND expires_at <= CURRENT_TIMESTAMP
                """,
                (scope, str(idempotency_key)),
            )

            cursor.execute(
                """
                SELECT id
                FROM app.support_cases
                WHERE id = %s AND organization_id = %s
                """,
                (case_id, organization_id),
            )
            if cursor.fetchone() is None:
                raise CaseNotFoundError("case not found")

            run_id = uuid4()
            cursor.execute(
                """
                INSERT INTO app.workflow_runs (
                    id, organization_id, case_id, thread_id, initiated_by, status,
                    graph_version, prompt_bundle_version, dataset_version
                )
                VALUES (%s, %s, %s, %s, %s, 'created', %s, %s, %s)
                RETURNING *
                """,
                (
                    run_id,
                    organization_id,
                    case_id,
                    str(run_id),
                    actor_user_id,
                    GRAPH_VERSION,
                    PROMPT_BUNDLE_VERSION,
                    DATASET_VERSION,
                ),
            )
            row = cursor.fetchone()
            if row is None:  # pragma: no cover - PostgreSQL RETURNING contract
                raise RunRepositoryError("run creation returned no row")
            run = _run_from_row(row)
            response_body = {"run": run.model_dump(mode="json")}
            cursor.execute(
                """
                INSERT INTO app.idempotency_records (
                    scope, key, request_hash, response_status, response_body, expires_at
                ) VALUES (
                    %s, %s, %s, 201, %s,
                    CURRENT_TIMESTAMP + (%s * INTERVAL '1 hour')
                )
                """,
                (
                    scope,
                    str(idempotency_key),
                    request_hash,
                    Jsonb(response_body),
                    IDEMPOTENCY_TTL_HOURS,
                ),
            )
            return CreatedRun(run=run, idempotent_replay=False)

    def get_run(
        self,
        *,
        run_id: UUID,
        organization_id: UUID,
        actor_user_id: UUID,
        allow_all: bool = False,
    ) -> WorkflowRun:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM app.workflow_runs
                WHERE id = %s AND organization_id = %s
                  AND (initiated_by = %s OR %s)
                """,
                (run_id, organization_id, actor_user_id, allow_all),
            )
            row = cursor.fetchone()
            if row is None:
                raise RunNotFoundError("run not found")
            return _run_from_row(row)

    def list_events(
        self,
        *,
        run_id: UUID,
        organization_id: UUID,
        actor_user_id: UUID,
        after_sequence: int,
        allow_all: bool = False,
    ) -> list[WorkflowEvent]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM app.workflow_runs
                WHERE id = %s AND organization_id = %s
                  AND (initiated_by = %s OR %s)
                """,
                (run_id, organization_id, actor_user_id, allow_all),
            )
            if cursor.fetchone() is None:
                raise RunNotFoundError("run not found")
            cursor.execute(
                """
                SELECT id, run_id, sequence, event_type, node_name, status,
                       public_payload, payload_hash, created_at
                FROM audit.workflow_events
                WHERE run_id = %s AND sequence > %s
                ORDER BY sequence
                LIMIT %s
                """,
                (run_id, after_sequence, EVENT_PAGE_SIZE),
            )
            return [_event_from_row(row) for row in cursor.fetchall()]

    def list_pending_approvals(self, *, organization_id: UUID) -> list[ApprovalQueueItem]:
        """List same-organization review work without applying run ownership rules."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT proposal.*, request.id AS request_id, request.requested_by,
                       request.requested_at, request.decided_by, request.decision,
                       request.comment, request.decided_at,
                       run.case_id, support.subject AS case_subject
                FROM app.approval_requests AS request
                JOIN app.action_proposals AS proposal ON proposal.id = request.proposal_id
                JOIN app.workflow_runs AS run ON run.id = proposal.run_id
                JOIN app.support_cases AS support ON support.id = run.case_id
                WHERE run.organization_id = %s
                  AND run.status = 'waiting_for_approval'
                  AND request.decision IS NULL
                  AND proposal.status = 'pending_approval'
                ORDER BY request.requested_at, request.id
                """,
                (organization_id,),
            )
            return [self._approval_queue_item(cursor, row) for row in cursor.fetchall()]

    def get_approval(
        self,
        *,
        run_id: UUID,
        organization_id: UUID,
    ) -> ApprovalQueueItem:
        """Read reviewer details for any run in the reviewer's organization."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT proposal.*, request.id AS request_id, request.requested_by,
                       request.requested_at, request.decided_by, request.decision,
                       request.comment, request.decided_at,
                       run.case_id, support.subject AS case_subject
                FROM app.approval_requests AS request
                JOIN app.action_proposals AS proposal ON proposal.id = request.proposal_id
                JOIN app.workflow_runs AS run ON run.id = proposal.run_id
                JOIN app.support_cases AS support ON support.id = run.case_id
                WHERE run.id = %s AND run.organization_id = %s
                """,
                (run_id, organization_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise RunNotFoundError("approval request not found")
            return self._approval_queue_item(cursor, row)

    def _approval_queue_item(
        self,
        cursor: psycopg.Cursor[dict[str, Any]],
        row: dict[str, Any],
    ) -> ApprovalQueueItem:
        proposal = _proposal_from_row(row)
        approval = _approval_request_from_row(row, proposal=proposal)
        cursor.execute(
            """
            SELECT public_payload
            FROM audit.workflow_events
            WHERE run_id = %s AND event_type = 'evidence.added'
            ORDER BY sequence
            """,
            (proposal.run_id,),
        )
        all_evidence = []
        for event_row in cursor.fetchall():
            payload = event_row["public_payload"]
            try:
                all_evidence.append(
                    ApprovalEvidence(
                        evidence_id=payload["evidence_id"],
                        source_system=payload["source_system"],
                        object_type=payload["object_type"],
                        object_id=payload["object_id"],
                        fact=payload["fact"],
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        cursor.execute(
            """
            SELECT public_payload->'cited_evidence_ids' AS cited_evidence_ids
            FROM audit.workflow_events
            WHERE run_id = %s AND event_type = 'approval.requested'
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (proposal.run_id,),
        )
        citation_row = cursor.fetchone()
        cited_ids = (
            set(citation_row["cited_evidence_ids"])
            if citation_row and isinstance(citation_row["cited_evidence_ids"], list)
            else set()
        )
        evidence = [item for item in all_evidence if not cited_ids or item.evidence_id in cited_ids]
        return ApprovalQueueItem(
            run_id=proposal.run_id,
            case_id=row["case_id"],
            case_subject=row["case_subject"],
            approval=approval,
            cited_evidence=evidence,
        )

    def record_approval_decision(
        self,
        *,
        run_id: UUID,
        organization_id: UUID,
        reviewer_user_id: UUID,
        proposal_id: UUID,
        proposal_hash: str,
        decision: ApprovalDecisionType,
        comment: str | None,
        idempotency_key: UUID,
        lease_seconds: int,
    ) -> RecordedApprovalDecision:
        """Persist an exact reviewer decision once, bound to an immutable proposal."""

        normalized_comment = comment.strip() if comment and comment.strip() else None
        if decision is ApprovalDecisionType.REJECT and normalized_comment is None:
            raise ApprovalDecisionConflictError("comment is required when rejecting a proposal")
        scope = f"run-decision:{organization_id}:{reviewer_user_id}"
        request_hash = _sha256(
            {
                "run_id": str(run_id),
                "proposal_id": str(proposal_id),
                "proposal_hash": proposal_hash,
                "decision": decision.value,
                "comment": normalized_comment,
            }
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{scope}:{idempotency_key}",),
            )
            cursor.execute(
                """
                SELECT request_hash, response_body
                FROM app.idempotency_records
                WHERE scope = %s AND key = %s AND expires_at > CURRENT_TIMESTAMP
                """,
                (scope, str(idempotency_key)),
            )
            replay = cursor.fetchone()
            if replay is not None and replay["request_hash"] != request_hash:
                raise IdempotencyConflictError(
                    "the idempotency key was already used for a different request"
                )

            cursor.execute(
                """
                SELECT proposal.*, request.id AS request_id, request.requested_by,
                       request.requested_at, request.decided_by, request.decision,
                       request.comment, request.decided_at,
                       run.status AS run_status,
                       run.current_node AS run_current_node,
                       run.execution_lease_until AS run_execution_lease_until
                FROM app.approval_requests AS request
                JOIN app.action_proposals AS proposal ON proposal.id = request.proposal_id
                JOIN app.workflow_runs AS run ON run.id = proposal.run_id
                WHERE run.id = %s AND run.organization_id = %s
                FOR UPDATE OF request, proposal, run
                """,
                (run_id, organization_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise RunNotFoundError("approval request not found")
            if row["id"] != proposal_id or row["proposal_hash"] != proposal_hash:
                raise StaleProposalError("proposal is stale or does not match the active request")

            existing_decision = row["decision"]
            if existing_decision is not None:
                if (
                    existing_decision != decision.value
                    or row["comment"] != normalized_comment
                    or row["decided_by"] != reviewer_user_id
                ):
                    raise ApprovalDecisionConflictError(
                        "the proposal already has a different reviewer decision"
                    )
                approval = _approval_request_from_row(row, proposal=_proposal_from_row(row))
                if row["run_status"] == RunStatus.RUNNING.value:
                    lease_token = uuid4()
                    cursor.execute(
                        """
                        UPDATE app.workflow_runs
                        SET execution_lease_token = %s,
                            execution_lease_until =
                                CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                            execution_attempt = execution_attempt + 1,
                            version = version + 1
                        WHERE id = %s AND organization_id = %s
                          AND status = 'running'
                          AND (
                            execution_lease_until IS NULL
                            OR execution_lease_until <= CURRENT_TIMESTAMP
                          )
                        RETURNING execution_attempt, execution_lease_until
                        """,
                        (lease_token, lease_seconds, run_id, organization_id),
                    )
                    recovered = cursor.fetchone()
                    if recovered is not None:
                        return RecordedApprovalDecision(
                            approval_request=approval,
                            idempotent_replay=True,
                            lease=ExecutionLease(
                                run_id=run_id,
                                token=lease_token,
                                attempt=recovered["execution_attempt"],
                                expires_at=recovered["execution_lease_until"],
                            ),
                        )
                    if row["run_execution_lease_until"] is None:
                        raise ApprovalDecisionConflictError(
                            "decided run could not reacquire its resume lease"
                        )
                elif not (
                    row["run_status"] == RunStatus.WAITING_FOR_APPROVAL.value
                    and row["run_current_node"] == "execute_approved_action"
                    and decision is ApprovalDecisionType.APPROVE
                ) and not (
                    row["run_status"] == RunStatus.ESCALATED.value
                    and decision is ApprovalDecisionType.REJECT
                ):
                    raise ApprovalDecisionConflictError(
                        "decided run is not at a valid resume or terminal state"
                    )
                return RecordedApprovalDecision(
                    approval_request=approval, idempotent_replay=True, lease=None
                )

            if row["status"] != ProposalStatus.PENDING_APPROVAL.value:
                raise StaleProposalError("proposal is no longer pending approval")
            cursor.execute(
                """
                UPDATE app.approval_requests
                SET decision = %s, comment = %s, decided_by = %s, decided_at = CURRENT_TIMESTAMP
                WHERE id = %s AND decision IS NULL
                RETURNING *
                """,
                (decision.value, normalized_comment, reviewer_user_id, row["request_id"]),
            )
            request_row = cursor.fetchone()
            if request_row is None:
                raise ApprovalDecisionConflictError("approval decision changed concurrently")
            cursor.execute(
                "UPDATE app.action_proposals SET status = %s WHERE id = %s",
                (
                    ProposalStatus.APPROVED.value
                    if decision is ApprovalDecisionType.APPROVE
                    else ProposalStatus.REJECTED.value,
                    proposal_id,
                ),
            )
            approval = _approval_request_from_row(
                request_row,
                proposal=_proposal_from_row(
                    {
                        **row,
                        "status": ProposalStatus.APPROVED.value
                        if decision is ApprovalDecisionType.APPROVE
                        else ProposalStatus.REJECTED.value,
                    }
                ),
            )
            lease_token = uuid4()
            cursor.execute(
                """
                UPDATE app.workflow_runs
                SET execution_lease_token = %s,
                    execution_lease_until = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    execution_attempt = execution_attempt + 1,
                    status = 'running',
                    current_node = 'approval_gate',
                    version = version + 1
                WHERE id = %s AND organization_id = %s
                  AND status = 'waiting_for_approval'
                  AND execution_lease_until IS NULL
                RETURNING execution_attempt, execution_lease_until
                """,
                (lease_token, lease_seconds, run_id, organization_id),
            )
            lease_row = cursor.fetchone()
            if lease_row is None:
                raise ApprovalDecisionConflictError("run cannot resume from its current state")
            lease = ExecutionLease(
                run_id=run_id,
                token=lease_token,
                attempt=lease_row["execution_attempt"],
                expires_at=lease_row["execution_lease_until"],
            )
            cursor.execute(
                """
                INSERT INTO app.idempotency_records (
                    scope, key, request_hash, response_status, response_body, expires_at
                ) VALUES (
                    %s, %s, %s, 200, %s,
                    CURRENT_TIMESTAMP + (%s * INTERVAL '1 hour')
                )
                """,
                (
                    scope,
                    str(idempotency_key),
                    request_hash,
                    Jsonb({"run_id": str(run_id), "proposal_id": str(proposal_id)}),
                    IDEMPOTENCY_TTL_HOURS,
                ),
            )
            return RecordedApprovalDecision(
                approval_request=approval,
                idempotent_replay=False,
                lease=lease,
            )

    def get_run_case(
        self,
        *,
        run_id: UUID,
        organization_id: UUID,
        actor_user_id: UUID,
        allow_all: bool = False,
    ) -> RunCase:
        """Load only the public case input owned by the active run organization."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT support.subject, support.body, support.customer_reference,
                       support.attachment_keys, support.created_at
                FROM app.workflow_runs AS run
                JOIN app.support_cases AS support
                  ON support.id = run.case_id
                 AND support.organization_id = run.organization_id
                WHERE run.id = %s AND run.organization_id = %s
                  AND (run.initiated_by = %s OR %s)
                """,
                (run_id, organization_id, actor_user_id, allow_all),
            )
            row = cursor.fetchone()
            if row is None:
                raise RunNotFoundError("run not found")
            return RunCase(
                ticket=TicketInput(
                    subject=row["subject"],
                    body=row["body"],
                    customer_reference=row["customer_reference"],
                    attachments=row["attachment_keys"],
                ),
                created_at=row["created_at"],
            )

    def acquire_execution_lease(
        self,
        *,
        run_id: UUID,
        organization_id: UUID,
        actor_user_id: UUID,
        lease_seconds: int,
        allow_all: bool = False,
    ) -> ExecutionLease:
        token = uuid4()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE app.workflow_runs
                SET execution_lease_token = %s,
                    execution_lease_until = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    execution_attempt = execution_attempt + 1,
                    status = 'running',
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    version = version + 1
                WHERE id = %s
                  AND organization_id = %s
                  AND (initiated_by = %s OR %s)
                  AND status IN ('created', 'running')
                  AND (execution_lease_until IS NULL OR execution_lease_until <= CURRENT_TIMESTAMP)
                RETURNING execution_attempt, execution_lease_until
                """,
                (
                    token,
                    lease_seconds,
                    run_id,
                    organization_id,
                    actor_user_id,
                    allow_all,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return ExecutionLease(
                    run_id=run_id,
                    token=token,
                    attempt=row["execution_attempt"],
                    expires_at=row["execution_lease_until"],
                )

            cursor.execute(
                """
                SELECT status, execution_lease_until
                FROM app.workflow_runs
                WHERE id = %s AND organization_id = %s
                  AND (initiated_by = %s OR %s)
                """,
                (run_id, organization_id, actor_user_id, allow_all),
            )
            state = cursor.fetchone()
            if state is None:
                raise RunNotFoundError("run not found")
            if state["execution_lease_until"] is not None:
                raise ExecutionLeaseConflictError("run already has an active execution lease")
            raise RunNotExecutableError(f"run cannot execute from status {state['status']}")

    def start_execution(
        self,
        *,
        run_id: UUID,
        organization_id: UUID,
        actor_user_id: UUID,
        idempotency_key: UUID,
        lease_seconds: int,
        allow_all: bool = False,
    ) -> ExecutionStart:
        """Atomically claim execute idempotency and a fenced execution lease."""

        scope = f"run-execute:{organization_id}:{actor_user_id}"
        request_hash = _sha256({"run_id": str(run_id)})
        token = uuid4()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{scope}:{idempotency_key}",),
            )
            cursor.execute(
                """
                SELECT request_hash, response_body
                FROM app.idempotency_records
                WHERE scope = %s AND key = %s AND expires_at > CURRENT_TIMESTAMP
                """,
                (scope, str(idempotency_key)),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflictError(
                        "the idempotency key was already used for a different request"
                    )
                response_body = existing["response_body"]
                if response_body is None or response_body.get("run_id") != str(run_id):
                    raise RunRepositoryError("idempotency response is incomplete")
                cursor.execute(
                    """
                    UPDATE app.workflow_runs
                    SET execution_lease_token = %s,
                        execution_lease_until = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                        execution_attempt = execution_attempt + 1,
                        status = 'running',
                        started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                        version = version + 1
                    WHERE id = %s AND organization_id = %s
                      AND (initiated_by = %s OR %s)
                      AND status IN ('created', 'running')
                      AND (execution_lease_until IS NULL
                           OR execution_lease_until <= CURRENT_TIMESTAMP)
                    RETURNING execution_attempt, execution_lease_until
                    """,
                    (
                        token,
                        lease_seconds,
                        run_id,
                        organization_id,
                        actor_user_id,
                        allow_all,
                    ),
                )
                recovered = cursor.fetchone()
                if recovered is not None:
                    return ExecutionStart(
                        lease=ExecutionLease(
                            run_id=run_id,
                            token=token,
                            attempt=recovered["execution_attempt"],
                            expires_at=recovered["execution_lease_until"],
                        ),
                        idempotent_replay=True,
                    )
                cursor.execute(
                    """
                    SELECT 1 FROM app.workflow_runs
                    WHERE id = %s AND organization_id = %s
                      AND (initiated_by = %s OR %s)
                    """,
                    (run_id, organization_id, actor_user_id, allow_all),
                )
                if cursor.fetchone() is None:
                    raise RunNotFoundError("run not found")
                return ExecutionStart(lease=None, idempotent_replay=True)

            cursor.execute(
                """
                DELETE FROM app.idempotency_records
                WHERE scope = %s AND key = %s AND expires_at <= CURRENT_TIMESTAMP
                """,
                (scope, str(idempotency_key)),
            )
            cursor.execute(
                """
                UPDATE app.workflow_runs
                SET execution_lease_token = %s,
                    execution_lease_until = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    execution_attempt = execution_attempt + 1,
                    status = 'running',
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    version = version + 1
                WHERE id = %s
                  AND organization_id = %s
                  AND (initiated_by = %s OR %s)
                  AND status IN ('created', 'running')
                  AND (execution_lease_until IS NULL OR execution_lease_until <= CURRENT_TIMESTAMP)
                RETURNING execution_attempt, execution_lease_until
                """,
                (
                    token,
                    lease_seconds,
                    run_id,
                    organization_id,
                    actor_user_id,
                    allow_all,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    SELECT status, execution_lease_until
                    FROM app.workflow_runs
                    WHERE id = %s AND organization_id = %s
                      AND (initiated_by = %s OR %s)
                    """,
                    (run_id, organization_id, actor_user_id, allow_all),
                )
                state = cursor.fetchone()
                if state is None:
                    raise RunNotFoundError("run not found")
                if state["execution_lease_until"] is not None:
                    raise ExecutionLeaseConflictError("run already has an active execution lease")
                raise RunNotExecutableError(f"run cannot execute from status {state['status']}")

            lease = ExecutionLease(
                run_id=run_id,
                token=token,
                attempt=row["execution_attempt"],
                expires_at=row["execution_lease_until"],
            )
            cursor.execute(
                """
                INSERT INTO app.idempotency_records (
                    scope, key, request_hash, response_status, response_body, expires_at
                ) VALUES (
                    %s, %s, %s, 200, %s,
                    CURRENT_TIMESTAMP + (%s * INTERVAL '1 hour')
                )
                """,
                (
                    scope,
                    str(idempotency_key),
                    request_hash,
                    Jsonb({"run_id": str(run_id), "execution_attempt": lease.attempt}),
                    IDEMPOTENCY_TTL_HOURS,
                ),
            )
            return ExecutionStart(lease=lease, idempotent_replay=False)

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
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT execution_lease_token,
                       execution_lease_until > CURRENT_TIMESTAMP AS lease_active
                FROM app.workflow_runs
                WHERE id = %s AND organization_id = %s
                FOR UPDATE
                """,
                (lease.run_id, organization_id),
            )
            state = cursor.fetchone()
            if (
                state is None
                or state["execution_lease_token"] != lease.token
                or not state["lease_active"]
            ):
                raise LostExecutionLeaseError("execution lease is no longer owned by this executor")

            cursor.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM audit.workflow_events
                WHERE run_id = %s
                """,
                (lease.run_id,),
            )
            sequence_row = cursor.fetchone()
            if sequence_row is None:  # pragma: no cover - aggregate always returns one row
                raise RunRepositoryError("event sequence query returned no row")
            sequence = sequence_row["next_sequence"]
            hash_input = {
                "run_id": str(lease.run_id),
                "sequence": sequence,
                "event_type": event_type.value,
                "node_name": node_name,
                "status": status,
                "public_payload": public_payload,
            }
            cursor.execute(
                """
                INSERT INTO audit.workflow_events (
                    run_id, sequence, event_type, node_name, status,
                    public_payload, payload_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, run_id, sequence, event_type, node_name, status,
                          public_payload, payload_hash, created_at
                """,
                (
                    lease.run_id,
                    sequence,
                    event_type.value,
                    node_name,
                    status,
                    Jsonb(public_payload),
                    _sha256(hash_input),
                ),
            )
            event_row = cursor.fetchone()
            if event_row is None:  # pragma: no cover - PostgreSQL RETURNING contract
                raise RunRepositoryError("event append returned no row")

            if final_status is not None:
                cursor.execute(
                    """
                    UPDATE app.workflow_runs
                    SET status = %s,
                        current_node = CASE WHEN %s = 'waiting_for_approval'
                                            THEN %s ELSE NULL END,
                        completed_at = CASE WHEN %s IN ('completed', 'escalated', 'failed')
                                            THEN CURRENT_TIMESTAMP ELSE completed_at END,
                        execution_lease_token = NULL,
                        execution_lease_until = NULL,
                        last_error_code = %s,
                        version = version + 1
                    WHERE id = %s AND execution_lease_token = %s
                    """,
                    (
                        final_status.value,
                        final_status.value,
                        node_name,
                        final_status.value,
                        final_error_code,
                        lease.run_id,
                        lease.token,
                    ),
                )
                if (
                    cursor.rowcount != 1
                ):  # pragma: no cover - row remains locked in this transaction
                    raise LostExecutionLeaseError("execution lease changed during completion")
            elif node_name is not None:
                cursor.execute(
                    """
                    UPDATE app.workflow_runs
                    SET current_node = %s, version = version + 1
                    WHERE id = %s AND execution_lease_token = %s
                    """,
                    (node_name, lease.run_id, lease.token),
                )
            return _event_from_row(event_row)

    def release_execution_lease_for_retry(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
    ) -> None:
        """Release only the caller's fenced lease after a failed resume attempt."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE app.workflow_runs
                SET execution_lease_token = NULL,
                    execution_lease_until = NULL,
                    version = version + 1
                WHERE id = %s
                  AND organization_id = %s
                  AND execution_lease_token = %s
                  AND status = 'running'
                """,
                (lease.run_id, organization_id, lease.token),
            )

    def record_artifact(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        kind: ArtifactKind,
        stored_object: StoredObject,
    ) -> RunArtifact:
        """Upsert stable run artifact metadata while the executor still owns the lease."""

        expected_metadata = {
            ArtifactKind.JSON_REPORT: (
                f"runs/{lease.run_id}/report.json",
                "application/json",
            ),
            ArtifactKind.MARKDOWN_BRIEF: (
                f"runs/{lease.run_id}/report.md",
                "text/markdown; charset=utf-8",
            ),
        }.get(kind)
        if expected_metadata != (stored_object.object_key, stored_object.mime_type):
            raise RunRepositoryError("artifact metadata does not match the active run and kind")

        with self._connect() as connection, connection.cursor() as cursor:
            self._lock_owned_lease(cursor, lease=lease, organization_id=organization_id)
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"run-artifact:{lease.run_id}:{kind.value}",),
            )
            cursor.execute(
                """
                SELECT id
                FROM app.run_artifacts
                WHERE run_id = %s AND kind = %s
                FOR UPDATE
                """,
                (lease.run_id, kind.value),
            )
            existing = cursor.fetchone()
            if existing is None:
                artifact_id = uuid4()
                cursor.execute(
                    """
                    INSERT INTO app.run_artifacts (
                        id, run_id, kind, object_key, mime_type, sha256, size_bytes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, run_id, kind, object_key, mime_type, sha256,
                              size_bytes, created_at
                    """,
                    (
                        artifact_id,
                        lease.run_id,
                        kind.value,
                        stored_object.object_key,
                        stored_object.mime_type,
                        stored_object.sha256,
                        stored_object.size_bytes,
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE app.run_artifacts
                    SET object_key = %s,
                        mime_type = %s,
                        sha256 = %s,
                        size_bytes = %s
                    WHERE id = %s
                    RETURNING id, run_id, kind, object_key, mime_type, sha256,
                              size_bytes, created_at
                    """,
                    (
                        stored_object.object_key,
                        stored_object.mime_type,
                        stored_object.sha256,
                        stored_object.size_bytes,
                        existing["id"],
                    ),
                )
            row = cursor.fetchone()
            if row is None:  # pragma: no cover - INSERT/UPDATE RETURNING contract
                raise RunRepositoryError("artifact persistence returned no row")
            return _artifact_from_row(row)

    def create_approval_gate_records(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        decision: PolicyDecision,
        cited_evidence_ids: Sequence[str] = (),
    ) -> ApprovalGateRecords:
        """Persist one immutable policy-derived proposal and undecided request."""

        if (
            not decision.approval_required
            or decision.action_type is None
            or decision.target_reference is None
            or not decision.canonical_parameters
            or decision.policy_key is None
            or decision.policy_version is None
        ):
            raise RunRepositoryError("approval gate requires a complete policy decision")

        canonical_parameters = json.loads(_canonical_json(decision.canonical_parameters))
        proposal_payload = {
            "action_type": decision.action_type.value,
            "target_reference": decision.target_reference,
            "canonical_parameters": canonical_parameters,
            "risk_level": decision.risk_level.value,
            "policy_key": decision.policy_key,
            "policy_version": decision.policy_version,
        }
        proposal_hash = _sha256(proposal_payload)
        proposal_id = uuid5(lease.run_id, "resolveops:action-proposal:v1")
        request_id = uuid5(lease.run_id, "resolveops:approval-request:v1")
        idempotency_key = f"resolveops:{lease.run_id}:{decision.action_type.value}:v1"

        with self._connect() as connection, connection.cursor() as cursor:
            self._lock_owned_lease(cursor, lease=lease, organization_id=organization_id)
            cursor.execute(
                "SELECT initiated_by FROM app.workflow_runs WHERE id = %s FOR UPDATE",
                (lease.run_id,),
            )
            run_row = cursor.fetchone()
            if run_row is None:  # pragma: no cover - lease lock already proved the row exists
                raise RunRepositoryError("run disappeared while creating approval request")
            requested_by = run_row["initiated_by"]
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"approval-gate:{lease.run_id}",),
            )
            cursor.execute(
                "SELECT * FROM app.action_proposals WHERE run_id = %s FOR UPDATE",
                (lease.run_id,),
            )
            proposal_row = cursor.fetchone()
            if proposal_row is None:
                cursor.execute(
                    """
                    INSERT INTO app.action_proposals (
                        id, run_id, action_type, target_reference, canonical_parameters,
                        proposal_hash, risk_level, policy_key, policy_version, status,
                        idempotency_key
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending_approval', %s)
                    RETURNING *
                    """,
                    (
                        proposal_id,
                        lease.run_id,
                        decision.action_type.value,
                        decision.target_reference,
                        Jsonb(canonical_parameters),
                        proposal_hash,
                        decision.risk_level.value,
                        decision.policy_key,
                        decision.policy_version,
                        idempotency_key,
                    ),
                )
                proposal_row = cursor.fetchone()
            if proposal_row is None:  # pragma: no cover - INSERT RETURNING contract
                raise RunRepositoryError("proposal persistence returned no row")

            expected = {
                "id": proposal_id,
                "action_type": decision.action_type.value,
                "target_reference": decision.target_reference,
                "canonical_parameters": canonical_parameters,
                "proposal_hash": proposal_hash,
                "risk_level": decision.risk_level.value,
                "policy_key": decision.policy_key,
                "policy_version": decision.policy_version,
                "status": ProposalStatus.PENDING_APPROVAL.value,
                "idempotency_key": idempotency_key,
            }
            if any(proposal_row[key] != value for key, value in expected.items()):
                raise ProposalReplayConflictError(
                    "approval proposal replay differs from the persisted proposal"
                )

            cursor.execute(
                "SELECT * FROM app.approval_requests WHERE proposal_id = %s FOR UPDATE",
                (proposal_id,),
            )
            request_row = cursor.fetchone()
            if request_row is None:
                cursor.execute(
                    """
                    INSERT INTO app.approval_requests (id, proposal_id, requested_by)
                    VALUES (%s, %s, %s)
                    RETURNING *
                    """,
                    (request_id, proposal_id, requested_by),
                )
                request_row = cursor.fetchone()
            if request_row is None:  # pragma: no cover - INSERT RETURNING contract
                raise RunRepositoryError("approval request persistence returned no row")
            if (
                request_row["id"] != request_id
                or request_row["requested_by"] != requested_by
                or request_row["decision"] is not None
                or request_row["decided_by"] is not None
                or request_row["comment"] is not None
                or request_row["decided_at"] is not None
            ):
                raise ProposalReplayConflictError(
                    "approval request replay differs from the undecided request"
                )

            proposal = _proposal_from_row(proposal_row)
            return ApprovalGateRecords(
                proposal=proposal,
                approval_request=_approval_request_from_row(request_row, proposal=proposal),
                cited_evidence_ids=tuple(sorted(set(cited_evidence_ids))),
            )

    def mark_waiting_for_approval(
        self,
        *,
        lease: ExecutionLease,
        organization_id: UUID,
        records: ApprovalGateRecords,
    ) -> WorkflowEvent:
        """Publish the approval request only after its graph checkpoint is durable."""

        if (
            records.proposal.run_id != lease.run_id
            or records.approval_request.proposal.proposal_id != records.proposal.proposal_id
            or records.approval_request.decision is not None
        ):
            raise RunRepositoryError("approval records do not belong to the active run")
        with self._connect() as connection, connection.cursor() as cursor:
            self._lock_owned_lease(cursor, lease=lease, organization_id=organization_id)
            cursor.execute(
                """
                SELECT 1
                FROM app.action_proposals AS proposal
                JOIN app.approval_requests AS request
                  ON request.proposal_id = proposal.id
                WHERE proposal.id = %s
                  AND proposal.run_id = %s
                  AND proposal.proposal_hash = %s
                  AND request.id = %s
                  AND request.decision IS NULL
                  AND request.decided_by IS NULL
                  AND request.comment IS NULL
                  AND request.decided_at IS NULL
                """,
                (
                    records.proposal.proposal_id,
                    lease.run_id,
                    records.proposal.proposal_hash,
                    records.approval_request.request_id,
                ),
            )
            if cursor.fetchone() is None:
                raise RunRepositoryError("persisted approval records do not match the active run")

        return self.append_event(
            lease=lease,
            organization_id=organization_id,
            event_type=WorkflowEventType.APPROVAL_REQUESTED,
            node_name="approval_gate",
            status="waiting_for_approval",
            public_payload={
                "proposal_id": str(records.proposal.proposal_id),
                "approval_request_id": str(records.approval_request.request_id),
                "proposal_hash": records.proposal.proposal_hash,
                "action_type": records.proposal.action_type.value,
                "risk_level": records.proposal.risk_level.value,
                "policy_key": records.proposal.policy_key,
                "policy_version": records.proposal.policy_version,
                "cited_evidence_ids": list(records.cited_evidence_ids),
            },
            final_status=RunStatus.WAITING_FOR_APPROVAL,
        )

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
        """Persist a compact attempt record before invoking a read-only transport."""

        with self._connect() as connection, connection.cursor() as cursor:
            self._lock_owned_lease(cursor, lease=lease, organization_id=organization_id)
            cursor.execute(
                """
                INSERT INTO app.tool_executions (
                    id, run_id, tool_call_id, tool_name, request_summary,
                    attempt, status, started_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'running', CURRENT_TIMESTAMP)
                """,
                (
                    uuid4(),
                    lease.run_id,
                    tool_call_id,
                    tool_name.value,
                    Jsonb(request_summary),
                    attempt,
                ),
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
        """Complete exactly one persisted tool attempt with no raw response logging."""

        with self._connect() as connection, connection.cursor() as cursor:
            self._lock_owned_lease(cursor, lease=lease, organization_id=organization_id)
            cursor.execute(
                """
                UPDATE app.tool_executions
                SET response_summary = %s,
                    status = %s,
                    error_code = %s,
                    latency_ms = %s,
                    completed_at = CURRENT_TIMESTAMP
                WHERE run_id = %s AND tool_call_id = %s
                  AND tool_name = %s AND attempt = %s AND status = 'running'
                """,
                (
                    Jsonb(response_summary),
                    "completed" if result.ok else "failed",
                    result.error_code,
                    result.latency_ms,
                    lease.run_id,
                    tool_call_id,
                    tool_name.value,
                    result.attempt,
                ),
            )
            if cursor.rowcount != 1:
                raise RunRepositoryError("tool attempt was not available for completion")

    @staticmethod
    def _lock_owned_lease(
        cursor: psycopg.Cursor[dict[str, Any]],
        *,
        lease: ExecutionLease,
        organization_id: UUID,
    ) -> None:
        cursor.execute(
            """
            SELECT execution_lease_token,
                   execution_lease_until > CURRENT_TIMESTAMP AS lease_active
            FROM app.workflow_runs
            WHERE id = %s AND organization_id = %s
            FOR UPDATE
            """,
            (lease.run_id, organization_id),
        )
        state = cursor.fetchone()
        if (
            state is None
            or state["execution_lease_token"] != lease.token
            or not state["lease_active"]
        ):
            raise LostExecutionLeaseError("execution lease is no longer owned by this executor")


def _run_from_row(row: dict[str, Any]) -> WorkflowRun:
    last_error = None
    if row["last_error_code"] is not None:
        last_error = RunError(
            code=row["last_error_code"],
            message="The run ended with a safe workflow error.",
            recoverable=row["last_error_code"] in RECOVERABLE_RUN_ERROR_CODES,
        )
    cost = row["cost_usd"]
    return WorkflowRun(
        run_id=row["id"],
        organization_id=row["organization_id"],
        case_id=row["case_id"],
        thread_id=row["thread_id"],
        initiated_by=row["initiated_by"],
        status=row["status"],
        current_node=row["current_node"],
        graph_version=row["graph_version"],
        prompt_bundle_version=row["prompt_bundle_version"],
        dataset_version=row["dataset_version"],
        resolved_model=row["resolved_model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=float(cost if isinstance(cost, Decimal) else cost or 0),
        execution_attempt=row["execution_attempt"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        last_error=last_error,
        created_at=row["created_at"],
    )


def _event_from_row(row: dict[str, Any]) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=row["id"],
        run_id=row["run_id"],
        sequence=row["sequence"],
        event_type=row["event_type"],
        node_name=row["node_name"],
        status=row["status"],
        public_payload=row["public_payload"],
        payload_hash=row["payload_hash"],
        created_at=row["created_at"],
    )


def _artifact_from_row(row: dict[str, Any]) -> RunArtifact:
    return RunArtifact(
        artifact_id=row["id"],
        run_id=row["run_id"],
        kind=row["kind"],
        object_key=row["object_key"],
        mime_type=row["mime_type"],
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        created_at=row["created_at"],
    )


def _proposal_from_row(row: dict[str, Any]) -> ActionProposal:
    return ActionProposal(
        proposal_id=row["id"],
        run_id=row["run_id"],
        action_type=row["action_type"],
        target_reference=row["target_reference"],
        canonical_parameters=row["canonical_parameters"],
        proposal_hash=row["proposal_hash"],
        risk_level=row["risk_level"],
        policy_key=row["policy_key"],
        policy_version=row["policy_version"],
        status=row["status"],
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
    )


def _approval_request_from_row(
    row: dict[str, Any],
    *,
    proposal: ActionProposal,
) -> ApprovalRequest:
    decision = None
    if row["decision"] is not None:
        decision = ApprovalDecision(
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal.proposal_hash,
            decision=row["decision"],
            comment=row["comment"],
            decided_by=row["decided_by"],
            decided_at=row["decided_at"],
        )
    return ApprovalRequest(
        request_id=row.get("request_id", row["id"]),
        proposal=proposal,
        requested_by=row["requested_by"],
        requested_at=row["requested_at"],
        decision=decision,
    )
