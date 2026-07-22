from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from resolveops.models.contracts import ReadToolName
from resolveops.tools.contracts import CustomerRecord, LookupCustomerInput
from resolveops.tools.read_only import OwnershipError, ReadOnlyToolset

NOW = datetime(2026, 7, 22, tzinfo=UTC)


class RecordingObserver:
    def __init__(self) -> None:
        self.started_attempts: list[int] = []
        self.finished_attempts: list[tuple[int, bool, bool]] = []

    def started(self, **kwargs: object) -> None:
        self.started_attempts.append(int(str(kwargs["attempt"])))

    def finished(self, **kwargs: object) -> None:
        result = kwargs["result"]
        assert hasattr(result, "attempt") and hasattr(result, "ok")
        self.finished_attempts.append((result.attempt, result.ok, bool(kwargs["will_retry"])))


class TransientBackend:
    def __init__(self) -> None:
        self.calls = 0

    def lookup_customer(self, request: LookupCustomerInput) -> CustomerRecord:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("raw upstream detail must be redacted")
        return CustomerRecord(
            account_id=UUID("a2bf6866-a47b-5920-8ada-49d78c5d39f1"),
            customer_reference=request.customer_reference,
            name="Synthetic Company",
            region="us-west",
            status="active",
            created_at=NOW,
        )

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unexpected backend call: {name}")


class MissingCustomerBackend(TransientBackend):
    def lookup_customer(self, request: LookupCustomerInput) -> CustomerRecord:
        del request
        self.calls += 1
        response = httpx.Response(404, request=httpx.Request("GET", "https://example.com"))
        raise httpx.HTTPStatusError("not found", request=response.request, response=response)


def test_idempotent_customer_read_retries_transient_failure_with_safe_summary() -> None:
    backend = TransientBackend()
    observer = RecordingObserver()
    tools = ReadOnlyToolset(backend, max_attempts=3, now=lambda: NOW)  # type: ignore[arg-type]

    result = tools.lookup_customer(
        LookupCustomerInput(customer_reference="org_atlas_001"),
        expected_customer_reference="org_atlas_001",
        observer=observer,
    )

    assert result.ok is True
    assert result.attempt == 2
    assert backend.calls == 2
    assert observer.started_attempts == [1, 2]
    assert observer.finished_attempts == [(1, False, True), (2, True, False)]


def test_ownership_mismatch_is_rejected_before_any_tool_attempt() -> None:
    backend = TransientBackend()
    observer = RecordingObserver()
    tools = ReadOnlyToolset(backend, now=lambda: NOW)  # type: ignore[arg-type]

    with pytest.raises(OwnershipError, match="outside the active case"):
        tools.lookup_customer(
            LookupCustomerInput(customer_reference="org_atlas_002"),
            expected_customer_reference="org_atlas_001",
            observer=observer,
        )

    assert backend.calls == 0
    assert observer.started_attempts == []
    assert ReadToolName.LOOKUP_CUSTOMER.value == "lookup_customer"


def test_non_transient_not_found_is_typed_and_not_retried() -> None:
    backend = MissingCustomerBackend()
    observer = RecordingObserver()
    tools = ReadOnlyToolset(backend, max_attempts=3, now=lambda: NOW)  # type: ignore[arg-type]

    result = tools.lookup_customer(
        LookupCustomerInput(customer_reference="org_atlas_001"),
        expected_customer_reference="org_atlas_001",
        observer=observer,
    )

    assert result.ok is False
    assert result.error_code == "not_found"
    assert result.error_message == "Synthetic object was not found."
    assert backend.calls == 1
    assert observer.finished_attempts == [(1, False, False)]
