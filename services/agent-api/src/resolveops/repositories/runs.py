"""PostgreSQL persistence for run identity, leases, and append-only events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from resolveops.models.contracts import (
    RunError,
    RunStatus,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowRun,
)

GRAPH_VERSION = "1.0.0"
PROMPT_BUNDLE_VERSION = "1.0.0"
DATASET_VERSION = "v1"
IDEMPOTENCY_TTL_HOURS = 24
EVENT_PAGE_SIZE = 500
DB_CONNECT_TIMEOUT_SECONDS = 5
DB_STATEMENT_TIMEOUT_MILLISECONDS = 10_000
DB_LOCK_TIMEOUT_MILLISECONDS = 5_000


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
                        current_node = NULL,
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


def _run_from_row(row: dict[str, Any]) -> WorkflowRun:
    last_error = None
    if row["last_error_code"] is not None:
        last_error = RunError(
            code=row["last_error_code"],
            message="The run ended with a safe workflow error.",
            recoverable=row["last_error_code"] == "run_shell_failed",
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
