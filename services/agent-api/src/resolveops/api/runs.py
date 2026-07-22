"""FastAPI routes for durable run creation, execution, and reconnect reads."""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from queue import SimpleQueue
from threading import Thread
from time import monotonic, sleep
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from resolveops.models.contracts import RunStatus, WorkflowEvent, WorkflowEventType, WorkflowRun
from resolveops.models.run_api import CreateRunRequest, CreateRunResponse, WorkflowEventPage
from resolveops.repositories.runs import (
    CaseNotFoundError,
    DatabaseRunRepository,
    ExecutionLease,
    ExecutionLeaseConflictError,
    IdempotencyConflictError,
    LostExecutionLeaseError,
    RunNotExecutableError,
    RunNotFoundError,
)

LEASE_SECONDS = 60
REPLAY_WAIT_SECONDS = LEASE_SECONDS + 1
REPLAY_POLL_SECONDS = 1.0
RUN_SHELL_NODE = "initialize_run_shell"


@dataclass(frozen=True)
class Principal:
    organization_id: UUID
    user_id: UUID
    roles: frozenset[str]

    @property
    def can_operate(self) -> bool:
        return not self.roles.isdisjoint({"operator", "reviewer", "admin"})

    @property
    def can_access_all_runs(self) -> bool:
        return "admin" in self.roles


@dataclass(frozen=True)
class IndependentExecution:
    events: Generator[str, None, None]
    thread: Thread


def require_principal(request: Request) -> Principal:
    """Read the authenticated principal established by the authorization middleware."""

    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication is required",
        )
    if not principal.can_operate:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator access is required",
        )
    return principal


def require_repository(request: Request) -> DatabaseRunRepository:
    repository = getattr(request.app.state, "run_repository", None)
    if not isinstance(repository, DatabaseRunRepository):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="run persistence is unavailable",
        )
    return repository


PrincipalDependency = Annotated[Principal, Depends(require_principal)]
RepositoryDependency = Annotated[DatabaseRunRepository, Depends(require_repository)]

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.post("", response_model=CreateRunResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    request: CreateRunRequest,
    principal: PrincipalDependency,
    repository: RepositoryDependency,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> CreateRunResponse:
    try:
        created = repository.create_run(
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            case_id=request.case_id,
            idempotency_key=idempotency_key,
        )
    except CaseNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except IdempotencyConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    response.headers["Idempotent-Replay"] = str(created.idempotent_replay).lower()
    response.headers["Cache-Control"] = "no-store"
    return CreateRunResponse(
        run_id=created.run.run_id,
        status=created.run.status,
        graph_version=created.run.graph_version,
        created_at=created.run.created_at,
    )


@router.get("/{run_id}", response_model=WorkflowRun)
def get_run(
    run_id: UUID,
    principal: PrincipalDependency,
    repository: RepositoryDependency,
    response: Response,
) -> WorkflowRun:
    try:
        run = repository.get_run(
            run_id=run_id,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            allow_all=principal.can_access_all_runs,
        )
    except RunNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    response.headers["Cache-Control"] = "no-store"
    return run


@router.get("/{run_id}/events", response_model=WorkflowEventPage)
def get_events(
    run_id: UUID,
    principal: PrincipalDependency,
    repository: RepositoryDependency,
    response: Response,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
) -> WorkflowEventPage:
    try:
        events = repository.list_events(
            run_id=run_id,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            after_sequence=after_sequence,
            allow_all=principal.can_access_all_runs,
        )
    except RunNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    response.headers["Cache-Control"] = "no-store"
    return WorkflowEventPage(
        events=events,
        after_sequence=after_sequence,
        last_sequence=events[-1].sequence if events else after_sequence,
    )


@router.post("/{run_id}/execute")
def execute_run(
    run_id: UUID,
    principal: PrincipalDependency,
    repository: RepositoryDependency,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> StreamingResponse:
    """Persist the bounded shell timeline, then expose those durable rows as SSE."""

    try:
        execution = repository.start_execution(
            run_id=run_id,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            idempotency_key=idempotency_key,
            lease_seconds=LEASE_SECONDS,
            allow_all=principal.can_access_all_runs,
        )
        if execution.lease is None:
            events = _replay_execution(repository=repository, principal=principal, run_id=run_id)
            background = None
        else:
            independent = _start_independent_execution(
                repository=repository,
                principal=principal,
                lease=execution.lease,
            )
            events = independent.events
            # Joining after the response body keeps the Lambda invocation alive even if
            # the client disconnects while the persistence worker is still finishing.
            background = BackgroundTask(independent.thread.join)
    except RunNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (
        ExecutionLeaseConflictError,
        IdempotencyConflictError,
        LostExecutionLeaseError,
        RunNotExecutableError,
    ) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return StreamingResponse(
        events,
        background=background,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Idempotent-Replay": str(execution.idempotent_replay).lower(),
            "X-Accel-Buffering": "no",
        },
    )


def _execute_shell(
    *,
    repository: DatabaseRunRepository,
    principal: Principal,
    lease: ExecutionLease,
) -> Iterator[WorkflowEvent]:
    yield repository.append_event(
        lease=lease,
        organization_id=principal.organization_id,
        event_type=WorkflowEventType.RUN_STARTED,
        status="running",
        public_payload={"execution_attempt": lease.attempt},
    )
    yield repository.append_event(
        lease=lease,
        organization_id=principal.organization_id,
        event_type=WorkflowEventType.NODE_STARTED,
        node_name=RUN_SHELL_NODE,
        status="running",
        public_payload={"summary": "Run shell initialization started."},
    )
    yield repository.append_event(
        lease=lease,
        organization_id=principal.organization_id,
        event_type=WorkflowEventType.NODE_COMPLETED,
        node_name=RUN_SHELL_NODE,
        status="completed",
        public_payload={"summary": "Run shell initialization completed."},
    )
    yield repository.append_event(
        lease=lease,
        organization_id=principal.organization_id,
        event_type=WorkflowEventType.RUN_COMPLETED,
        status="completed",
        public_payload={"report_status": "not_generated"},
        final_status=RunStatus.COMPLETED,
    )


_STREAM_DONE = object()


def _start_independent_execution(
    *,
    repository: DatabaseRunRepository,
    principal: Principal,
    lease: ExecutionLease,
) -> IndependentExecution:
    """Run persistence independently so a disconnected stream cannot cancel the run."""

    messages: SimpleQueue[WorkflowEvent | Exception | object] = SimpleQueue()

    def worker() -> None:
        try:
            for event in _execute_shell(
                repository=repository,
                principal=principal,
                lease=lease,
            ):
                messages.put(event)
        except Exception as error:  # noqa: BLE001 - persist a safe terminal failure
            try:
                failed_event = repository.append_event(
                    lease=lease,
                    organization_id=principal.organization_id,
                    event_type=WorkflowEventType.RUN_FAILED,
                    status="failed",
                    public_payload={
                        "error_code": "run_shell_failed",
                        "recoverable": True,
                    },
                    final_status=RunStatus.FAILED,
                    final_error_code="run_shell_failed",
                )
                messages.put(failed_event)
            except Exception:  # noqa: BLE001 - original failure remains the safe boundary
                messages.put(error)
        finally:
            messages.put(_STREAM_DONE)

    thread = Thread(target=worker, name=f"resolveops-run-{lease.run_id}")
    thread.start()

    def stream() -> Generator[str, None, None]:
        try:
            while True:
                message = messages.get()
                if message is _STREAM_DONE:
                    return
                if isinstance(message, Exception):
                    raise message
                if isinstance(message, WorkflowEvent):
                    yield _format_sse(message)
        finally:
            thread.join()

    return IndependentExecution(events=stream(), thread=thread)


def _replay_execution(
    *,
    repository: DatabaseRunRepository,
    principal: Principal,
    run_id: UUID,
) -> Iterator[str]:
    after_sequence = 0
    terminal_statuses = {RunStatus.COMPLETED, RunStatus.ESCALATED, RunStatus.FAILED}
    deadline = monotonic() + REPLAY_WAIT_SECONDS
    while monotonic() < deadline:
        events = repository.list_events(
            run_id=run_id,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            after_sequence=after_sequence,
            allow_all=principal.can_access_all_runs,
        )
        for event in events:
            after_sequence = event.sequence
            yield _format_sse(event)
        run = repository.get_run(
            run_id=run_id,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            allow_all=principal.can_access_all_runs,
        )
        if run.status in terminal_statuses and not events:
            return
        sleep(REPLAY_POLL_SECONDS)


def _format_sse(event: WorkflowEvent) -> str:
    data = {
        "run_id": str(event.run_id),
        "sequence": event.sequence,
        "status": event.status,
        "node_name": event.node_name,
        **event.public_payload,
    }
    return (
        f"id: {event.sequence}\n"
        f"event: {event.event_type.value}\n"
        f"data: {json.dumps(data, sort_keys=True, separators=(',', ':'))}\n\n"
    )
