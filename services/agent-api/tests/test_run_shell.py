from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

import resolveops.api.runs as run_routes
from resolveops.api.app import create_app
from resolveops.api.runs import (
    Principal,
    _execute_shell,
    _replay_execution,
    _start_independent_execution,
    require_principal,
)
from resolveops.models.contracts import WorkflowEventType
from resolveops.repositories.runs import DatabaseRunRepository, LostExecutionLeaseError
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

TEST_NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
TEST_ACCOUNT_ID = UUID("a2bf6866-a47b-5920-8ada-49d78c5d39f1")
TEST_SUBSCRIPTION_ID = UUID("09615c4c-25ac-5d56-a6d1-6bbc4573e5e0")
TEST_INVOICE_ID = UUID("5837ede5-6c6f-59e8-8cad-52fb7a66d25a")
TEST_PAYMENT_ID = UUID("92c4c18e-51d4-5c66-9120-e26056a90e4d")
TEST_POLICY_ID = UUID("62141d26-a9f1-5fb1-89bc-f3bd25cbf4a8")


class DatabaseTestBackend:
    """Deterministic synthetic transport used by database-backed route tests."""

    def lookup_customer(self, request: LookupCustomerInput) -> CustomerRecord:
        return CustomerRecord(
            account_id=TEST_ACCOUNT_ID,
            customer_reference=request.customer_reference,
            name="AtlasFlow Test Organization",
            region="us-west",
            status="active",
            created_at=TEST_NOW,
        )

    def get_subscription(self, request: GetSubscriptionInput) -> SubscriptionRecord:
        return SubscriptionRecord(
            subscription_id=TEST_SUBSCRIPTION_ID,
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
            upgraded_at=TEST_NOW,
        )

    def list_invoices(self, request: ListInvoicesInput) -> InvoicePage:
        return InvoicePage(
            items=[
                InvoiceRecord(
                    invoice_id=TEST_INVOICE_ID,
                    account_id=request.account_id,
                    subscription_id=TEST_SUBSCRIPTION_ID,
                    period_start=date(2026, 7, 1),
                    period_end=date(2026, 8, 1),
                    amount_cents=4_900,
                    currency="USD",
                    status="paid",
                    issued_at=TEST_NOW,
                )
            ]
        )

    def get_payment_attempts(self, request: GetPaymentAttemptsInput) -> PaymentAttemptPage:
        return PaymentAttemptPage(
            items=[
                PaymentAttemptRecord(
                    payment_attempt_id=TEST_PAYMENT_ID,
                    account_id=request.account_id,
                    invoice_id=request.invoice_id,
                    amount_cents=4_900,
                    currency="USD",
                    status="succeeded",
                    processor_reference="synthetic_test_payment",
                    attempted_at=TEST_NOW,
                )
            ]
        )

    def get_policy(self, request: GetPolicyInput) -> PolicyRecord:
        return PolicyRecord(
            policy_id=TEST_POLICY_ID,
            policy_key=request.policy_key,
            version=request.version,
            action_type="apply_account_credit",
            maximum_amount_cents=10_000,
            approval_required=True,
            effective_at=TEST_NOW,
            body="Synthetic test policy.",
        )


@dataclass(frozen=True)
class SeededCase:
    principal: Principal
    case_id: UUID


def _test_database_url() -> str:
    value = os.getenv("DATABASE_URL_TEST")
    if not value:
        pytest.skip("DATABASE_URL_TEST is required for PostgreSQL run persistence tests")
    database_name = value.rsplit("/", 1)[-1].split("?", 1)[0]
    if not database_name.endswith("_test"):
        raise RuntimeError("DATABASE_URL_TEST must target a database ending in '_test'")
    return value


@pytest.fixture(scope="session")
def database_url() -> str:
    value = _test_database_url()
    service_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["DATABASE_URL_DIRECT"] = value
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=service_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return value


@pytest.fixture
def seeded_case(database_url: str) -> SeededCase:
    organization_id = uuid4()
    user_id = uuid4()
    case_id = uuid4()
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO app.users (id, workos_user_id, display_name) VALUES (%s, %s, %s)",
            (user_id, f"workos_{user_id}", "Synthetic Operator"),
        )
        cursor.execute(
            "INSERT INTO app.organizations (id, name, slug, mode) VALUES (%s, %s, %s, 'demo')",
            (organization_id, "Synthetic Organization", f"org-{organization_id}"),
        )
        cursor.execute(
            """
            INSERT INTO app.organization_memberships (organization_id, user_id, role)
            VALUES (%s, %s, 'operator')
            """,
            (organization_id, user_id),
        )
        cursor.execute(
            """
            INSERT INTO app.support_cases (
                id, organization_id, dataset_case_id, subject, body,
                customer_reference, status, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, 'open', %s)
            """,
            (
                case_id,
                organization_id,
                f"case_{case_id}",
                "Synthetic duplicate charge",
                "Two synthetic charges appear for the same period.",
                "org_atlas_001",
                user_id,
            ),
        )
    return SeededCase(
        principal=Principal(
            organization_id=organization_id,
            user_id=user_id,
            roles=frozenset({"operator"}),
        ),
        case_id=case_id,
    )


def _client(database_url: str, principal: Principal) -> TestClient:
    application = create_app(
        DatabaseRunRepository(database_url),
        ReadOnlyToolset(DatabaseTestBackend(), now=lambda: TEST_NOW),
    )
    application.dependency_overrides[require_principal] = lambda: principal
    return TestClient(application)


def _create_run(
    client: TestClient,
    case_id: UUID,
    key: UUID | None = None,
) -> tuple[dict[str, Any], str]:
    response = client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": str(key or uuid4())},
        json={"case_id": str(case_id)},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body, response.headers["Idempotent-Replay"]


def test_execute_fails_closed_when_synthetic_evidence_tools_are_unavailable() -> None:
    principal = Principal(
        organization_id=uuid4(),
        user_id=uuid4(),
        roles=frozenset({"operator"}),
    )
    application = create_app(
        DatabaseRunRepository("postgresql+psycopg://resolveops:resolveops@127.0.0.1:1/unreachable")
    )
    application.dependency_overrides[require_principal] = lambda: principal

    with TestClient(application) as client:
        response = client.post(
            f"/api/v1/runs/{uuid4()}/execute",
            headers={"Idempotency-Key": str(uuid4())},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "synthetic evidence tools are unavailable"


def test_run_creation_is_idempotent_and_uses_run_id_as_thread_id(
    database_url: str,
    seeded_case: SeededCase,
) -> None:
    key = uuid4()
    with _client(database_url, seeded_case.principal) as client:
        first, first_replay = _create_run(client, seeded_case.case_id, key)
        replay, replay_replay = _create_run(client, seeded_case.case_id, key)
        run_detail = client.get(f"/api/v1/runs/{first['run_id']}").json()
        conflict = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": str(key)},
            json={"case_id": str(uuid4())},
        )

    assert first_replay == "false"
    assert replay_replay == "true"
    assert first["run_id"] == replay["run_id"]
    assert run_detail["thread_id"] == first["run_id"]
    assert conflict.status_code == 409


def test_execute_persists_monotonic_events_before_sse_and_supports_reconnect(
    database_url: str,
    seeded_case: SeededCase,
) -> None:
    with _client(database_url, seeded_case.principal) as client:
        created, _ = _create_run(client, seeded_case.case_id)
        run_id = created["run_id"]
        execute_key = uuid4()
        response = client.post(
            f"/api/v1/runs/{run_id}/execute",
            headers={
                "Accept": "text/event-stream",
                "Idempotency-Key": str(execute_key),
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        event_ids = [line for line in response.text.splitlines() if line.startswith("id: ")]
        assert event_ids == [f"id: {sequence}" for sequence in range(1, 36)]

        run_response = client.get(f"/api/v1/runs/{run_id}")
        assert run_response.status_code == 200
        assert run_response.json()["status"] == "completed"
        assert run_response.json()["execution_attempt"] == 1

        reconnect = client.get(f"/api/v1/runs/{run_id}/events?after_sequence=2")
        assert reconnect.status_code == 200
        assert [event["sequence"] for event in reconnect.json()["events"]] == list(range(3, 36))
        assert reconnect.json()["last_sequence"] == 35

        replay = client.post(
            f"/api/v1/runs/{run_id}/execute",
            headers={"Idempotency-Key": str(execute_key)},
        )
        assert replay.status_code == 200
        assert replay.headers["Idempotent-Replay"] == "true"
        assert [line for line in replay.text.splitlines() if line.startswith("id: ")] == event_ids

    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tool_call_id, tool_name, status, request_summary, response_summary
            FROM app.tool_executions
            WHERE run_id = %s
            """,
            (run_id,),
        )
        tool_attempts = cursor.fetchall()

    assert len(tool_attempts) == 5
    assert {attempt[1] for attempt in tool_attempts} == {
        "lookup_customer",
        "get_subscription",
        "list_invoices",
        "get_payment_attempts",
        "get_policy",
    }
    assert all(str(attempt[0]).startswith("execution-1:") for attempt in tool_attempts)
    assert all(attempt[2] == "completed" for attempt in tool_attempts)
    assert all("body" not in attempt[3] and "body" not in attempt[4] for attempt in tool_attempts)


def test_active_execution_lease_rejects_a_concurrent_executor(
    database_url: str,
    seeded_case: SeededCase,
) -> None:
    repository = DatabaseRunRepository(database_url)
    with _client(database_url, seeded_case.principal) as client:
        created, _ = _create_run(client, seeded_case.case_id)
        run_id = UUID(created["run_id"])
        repository.acquire_execution_lease(
            run_id=run_id,
            organization_id=seeded_case.principal.organization_id,
            actor_user_id=seeded_case.principal.user_id,
            lease_seconds=60,
        )
        response = client.post(
            f"/api/v1/runs/{run_id}/execute",
            headers={"Idempotency-Key": str(uuid4())},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "run already has an active execution lease"


def test_stale_execution_lease_is_recovered_with_a_new_fencing_token(
    database_url: str,
    seeded_case: SeededCase,
) -> None:
    repository = DatabaseRunRepository(database_url)
    with _client(database_url, seeded_case.principal) as client:
        created, _ = _create_run(client, seeded_case.case_id)
        run_id = UUID(created["run_id"])
        stale_lease = repository.acquire_execution_lease(
            run_id=run_id,
            organization_id=seeded_case.principal.organization_id,
            actor_user_id=seeded_case.principal.user_id,
            lease_seconds=60,
        )
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE app.workflow_runs
                SET execution_lease_until = CURRENT_TIMESTAMP - INTERVAL '1 second'
                WHERE id = %s
                """,
                (run_id,),
            )

        response = client.post(
            f"/api/v1/runs/{run_id}/execute",
            headers={"Idempotency-Key": str(uuid4())},
        )
        run_response = client.get(f"/api/v1/runs/{run_id}")

    assert response.status_code == 200, response.text
    assert run_response.json()["execution_attempt"] == 2
    with pytest.raises(LostExecutionLeaseError, match="lease is no longer owned"):
        repository.append_event(
            lease=stale_lease,
            organization_id=seeded_case.principal.organization_id,
            event_type=WorkflowEventType.NODE_STARTED,
            status="running",
            public_payload={},
        )


def test_idempotent_execute_retry_recovers_its_stale_lease(
    database_url: str,
    seeded_case: SeededCase,
) -> None:
    repository = DatabaseRunRepository(database_url)
    created = repository.create_run(
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        case_id=seeded_case.case_id,
        idempotency_key=uuid4(),
    )
    execute_key = uuid4()
    first = repository.start_execution(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        idempotency_key=execute_key,
        lease_seconds=60,
    )
    assert first.lease is not None
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE app.workflow_runs
            SET execution_lease_until = CURRENT_TIMESTAMP - INTERVAL '1 second'
            WHERE id = %s
            """,
            (created.run.run_id,),
        )

    recovered = repository.start_execution(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        idempotency_key=execute_key,
        lease_seconds=60,
    )

    assert recovered.idempotent_replay is True
    assert recovered.lease is not None
    assert recovered.lease.attempt == 2
    list(
        _execute_shell(
            repository=repository,
            principal=seeded_case.principal,
            lease=recovered.lease,
        )
    )


def test_database_serializes_concurrent_event_sequence_allocation(
    database_url: str,
    seeded_case: SeededCase,
) -> None:
    repository = DatabaseRunRepository(database_url)
    created = repository.create_run(
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        case_id=seeded_case.case_id,
        idempotency_key=uuid4(),
    )
    lease = repository.acquire_execution_lease(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        lease_seconds=60,
    )

    def append_event(index: int) -> None:
        repository.append_event(
            lease=lease,
            organization_id=seeded_case.principal.organization_id,
            event_type=WorkflowEventType.NODE_STARTED,
            node_name="sequence_test",
            status="running",
            public_payload={"index": index, "observed_at": datetime.now(UTC).isoformat()},
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append_event, range(8)))

    events = repository.list_events(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        after_sequence=0,
    )
    assert [event.sequence for event in events] == list(range(1, 9))


def test_each_shell_event_is_committed_before_it_can_be_yielded(
    database_url: str,
    seeded_case: SeededCase,
) -> None:
    repository = DatabaseRunRepository(database_url)
    created = repository.create_run(
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        case_id=seeded_case.case_id,
        idempotency_key=uuid4(),
    )
    start = repository.start_execution(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        idempotency_key=uuid4(),
        lease_seconds=60,
    )
    assert start.lease is not None
    stream = _execute_shell(
        repository=repository,
        principal=seeded_case.principal,
        lease=start.lease,
    )

    for expected_sequence in range(1, 5):
        yielded = next(stream)
        persisted = repository.list_events(
            run_id=created.run.run_id,
            organization_id=seeded_case.principal.organization_id,
            actor_user_id=seeded_case.principal.user_id,
            after_sequence=expected_sequence - 1,
        )
        assert yielded.sequence == expected_sequence
        assert persisted[0].sequence == expected_sequence


def test_stream_disconnect_does_not_cancel_independent_run_state(
    database_url: str,
    seeded_case: SeededCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DatabaseRunRepository(database_url)
    created = repository.create_run(
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        case_id=seeded_case.case_id,
        idempotency_key=uuid4(),
    )
    start = repository.start_execution(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        idempotency_key=uuid4(),
        lease_seconds=60,
    )
    assert start.lease is not None
    real_append_event = repository.append_event
    allow_completion = Event()
    append_count = 0

    def pause_after_first_event(**kwargs: Any) -> Any:
        nonlocal append_count
        append_count += 1
        if append_count > 1:
            assert allow_completion.wait(timeout=5)
        return real_append_event(**kwargs)

    monkeypatch.setattr(repository, "append_event", pause_after_first_event)
    independent = _start_independent_execution(
        repository=repository,
        principal=seeded_case.principal,
        lease=start.lease,
    )
    assert next(independent.events).startswith("id: 1\n")
    assert independent.thread.is_alive()
    allow_completion.set()
    independent.events.close()
    assert not independent.thread.is_alive()

    deadline = monotonic() + 5
    while monotonic() < deadline:
        run = repository.get_run(
            run_id=created.run.run_id,
            organization_id=seeded_case.principal.organization_id,
            actor_user_id=seeded_case.principal.user_id,
        )
        if run.status.value == "completed":
            break
        sleep(0.01)
    assert run.status.value == "completed"


def test_background_worker_persists_a_safe_terminal_failure(
    database_url: str,
    seeded_case: SeededCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DatabaseRunRepository(database_url)
    created = repository.create_run(
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        case_id=seeded_case.case_id,
        idempotency_key=uuid4(),
    )
    start = repository.start_execution(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        idempotency_key=uuid4(),
        lease_seconds=60,
    )
    assert start.lease is not None
    real_append_event = repository.append_event
    fail_next = True

    def fail_once(**kwargs: Any) -> Any:
        nonlocal fail_next
        if fail_next:
            fail_next = False
            raise RuntimeError("synthetic worker failure")
        return real_append_event(**kwargs)

    monkeypatch.setattr(repository, "append_event", fail_once)
    independent = _start_independent_execution(
        repository=repository,
        principal=seeded_case.principal,
        lease=start.lease,
    )
    frames = list(independent.events)

    run = repository.get_run(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
    )
    events = repository.list_events(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        after_sequence=0,
    )
    assert run.status.value == "failed"
    assert run.last_error is not None
    assert run.last_error.code == "run_shell_failed"
    assert run.last_error.recoverable is True
    assert events[-1].event_type.value == "run.failed"
    assert "run_shell_failed" in frames[-1]


def test_active_idempotent_replay_closes_at_the_bounded_wait_window(
    database_url: str,
    seeded_case: SeededCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DatabaseRunRepository(database_url)
    created = repository.create_run(
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        case_id=seeded_case.case_id,
        idempotency_key=uuid4(),
    )
    start = repository.start_execution(
        run_id=created.run.run_id,
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        idempotency_key=uuid4(),
        lease_seconds=60,
    )
    assert start.lease is not None
    monkeypatch.setattr(run_routes, "REPLAY_WAIT_SECONDS", 0.03)
    monkeypatch.setattr(run_routes, "REPLAY_POLL_SECONDS", 0.005)

    started_at = monotonic()
    frames = list(
        _replay_execution(
            repository=repository,
            principal=seeded_case.principal,
            run_id=created.run.run_id,
        )
    )

    assert frames == []
    assert monotonic() - started_at < 1


def test_same_organization_operator_cannot_read_or_execute_another_users_run(
    database_url: str,
    seeded_case: SeededCase,
) -> None:
    repository = DatabaseRunRepository(database_url)
    created = repository.create_run(
        organization_id=seeded_case.principal.organization_id,
        actor_user_id=seeded_case.principal.user_id,
        case_id=seeded_case.case_id,
        idempotency_key=uuid4(),
    )
    other_operator = Principal(
        organization_id=seeded_case.principal.organization_id,
        user_id=uuid4(),
        roles=frozenset({"operator"}),
    )
    with _client(database_url, other_operator) as client:
        read_response = client.get(f"/api/v1/runs/{created.run.run_id}")
        execute_response = client.post(
            f"/api/v1/runs/{created.run.run_id}/execute",
            headers={"Idempotency-Key": str(uuid4())},
        )

    assert read_response.status_code == 404
    assert execute_response.status_code == 404
