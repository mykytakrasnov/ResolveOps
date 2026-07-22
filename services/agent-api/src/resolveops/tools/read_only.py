"""Bounded execution for idempotent synthetic read tools."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol, TypeVar, cast

import httpx
from pydantic import BaseModel, ValidationError

from resolveops.models.contracts import ReadToolName, SourceSystem, ToolResult
from resolveops.tools.contracts import (
    CustomerRecord,
    GetPaymentAttemptsInput,
    GetPolicyInput,
    GetSubscriptionInput,
    InvoicePage,
    ListInvoicesInput,
    LookupCustomerInput,
    PaymentAttemptPage,
    PolicyRecord,
    SubscriptionRecord,
    SyntheticReadBackend,
)

TOOL_TIMEOUT_SECONDS = 5.0
MAX_READ_ATTEMPTS = 3
_TRANSIENT_ERROR_CODES = frozenset({"timeout", "transport_error", "upstream_unavailable"})
T = TypeVar("T", bound=BaseModel)


class ToolAttemptObserver(Protocol):
    def started(
        self,
        *,
        tool_call_id: str,
        tool_name: ReadToolName,
        attempt: int,
        request_summary: dict[str, str | int],
    ) -> None: ...

    def finished(
        self,
        *,
        tool_call_id: str,
        tool_name: ReadToolName,
        result: ToolResult[BaseModel],
        response_summary: dict[str, str | int | list[str]],
        will_retry: bool,
    ) -> None: ...


class OwnershipError(ValueError):
    """Raised before transport when a requested object is outside the active case."""


class ReadOnlyToolset:
    """Typed tools with uniform timeout, retry, ownership, and result envelopes."""

    def __init__(
        self,
        backend: SyntheticReadBackend,
        *,
        timeout_seconds: float = TOOL_TIMEOUT_SECONDS,
        max_attempts: int = MAX_READ_ATTEMPTS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")
        if not 1 <= max_attempts <= MAX_READ_ATTEMPTS:
            raise ValueError(f"read attempts must be between 1 and {MAX_READ_ATTEMPTS}")
        self._backend = backend
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._now = now or (lambda: datetime.now(UTC))

    def lookup_customer(
        self,
        request: LookupCustomerInput,
        *,
        expected_customer_reference: str,
        observer: ToolAttemptObserver,
        tool_call_id: str = "lookup_customer",
    ) -> ToolResult[CustomerRecord]:
        if request.customer_reference != expected_customer_reference:
            raise OwnershipError("customer reference is outside the active case")
        return self._execute(
            tool_call_id=tool_call_id,
            tool_name=ReadToolName.LOOKUP_CUSTOMER,
            source_system=SourceSystem.CRM,
            request=request,
            request_summary={"customer_reference": request.customer_reference},
            call=lambda: self._backend.lookup_customer(request),
            observer=observer,
        )

    def get_subscription(
        self,
        request: GetSubscriptionInput,
        *,
        owned_account_id: str,
        observer: ToolAttemptObserver,
        tool_call_id: str = "get_subscription",
    ) -> ToolResult[SubscriptionRecord]:
        self._require_account(request.account_id, owned_account_id)
        return self._execute(
            tool_call_id=tool_call_id,
            tool_name=ReadToolName.GET_SUBSCRIPTION,
            source_system=SourceSystem.BILLING,
            request=request,
            request_summary={"account_id": str(request.account_id)},
            call=lambda: self._backend.get_subscription(request),
            observer=observer,
        )

    def list_invoices(
        self,
        request: ListInvoicesInput,
        *,
        owned_account_id: str,
        observer: ToolAttemptObserver,
        tool_call_id: str = "list_invoices",
    ) -> ToolResult[InvoicePage]:
        self._require_account(request.account_id, owned_account_id)
        if request.to_date < request.from_date or (request.to_date - request.from_date).days > 366:
            raise ValueError("invoice date range must be ordered and at most 366 days")
        return self._execute(
            tool_call_id=tool_call_id,
            tool_name=ReadToolName.LIST_INVOICES,
            source_system=SourceSystem.BILLING,
            request=request,
            request_summary={
                "account_id": str(request.account_id),
                "from": request.from_date.isoformat(),
                "to": request.to_date.isoformat(),
                "limit": request.limit,
            },
            call=lambda: self._backend.list_invoices(request),
            observer=observer,
        )

    def get_payment_attempts(
        self,
        request: GetPaymentAttemptsInput,
        *,
        owned_account_id: str,
        allowed_invoice_ids: frozenset[str],
        observer: ToolAttemptObserver,
        tool_call_id: str | None = None,
    ) -> ToolResult[PaymentAttemptPage]:
        self._require_account(request.account_id, owned_account_id)
        if str(request.invoice_id) not in allowed_invoice_ids:
            raise OwnershipError("invoice is outside the active case evidence")
        return self._execute(
            tool_call_id=tool_call_id or f"get_payment_attempts:{request.invoice_id}",
            tool_name=ReadToolName.GET_PAYMENT_ATTEMPTS,
            source_system=SourceSystem.BILLING,
            request=request,
            request_summary={
                "account_id": str(request.account_id),
                "invoice_id": str(request.invoice_id),
                "limit": request.limit,
            },
            call=lambda: self._backend.get_payment_attempts(request),
            observer=observer,
        )

    def get_policy(
        self,
        request: GetPolicyInput,
        *,
        observer: ToolAttemptObserver,
        tool_call_id: str = "get_policy",
    ) -> ToolResult[PolicyRecord]:
        return self._execute(
            tool_call_id=tool_call_id,
            tool_name=ReadToolName.GET_POLICY,
            source_system=SourceSystem.POLICY,
            request=request,
            request_summary={"policy_key": request.policy_key, "version": request.version},
            call=lambda: self._backend.get_policy(request),
            observer=observer,
        )

    @staticmethod
    def _require_account(account_id: object, owned_account_id: str) -> None:
        if str(account_id) != owned_account_id:
            raise OwnershipError("account is outside the active case")

    def _execute(
        self,
        *,
        tool_call_id: str,
        tool_name: ReadToolName,
        source_system: SourceSystem,
        request: BaseModel,
        request_summary: dict[str, str | int],
        call: Callable[[], T],
        observer: ToolAttemptObserver,
    ) -> ToolResult[T]:
        del request  # validated by the public typed method before this common executor
        last_result: ToolResult[T] | None = None
        for attempt in range(1, self._max_attempts + 1):
            observer.started(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                attempt=attempt,
                request_summary=request_summary,
            )
            started = monotonic()
            error_code: str | None = None
            error_message: str | None = None
            data: T | None = None
            executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"tool-{tool_name.value}",
            )
            try:
                data = executor.submit(call).result(timeout=self._timeout_seconds)
            except FutureTimeoutError:
                error_code = "timeout"
                error_message = "Synthetic read timed out."
            except httpx.TimeoutException:
                error_code = "timeout"
                error_message = "Synthetic read timed out."
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 404:
                    error_code = "not_found"
                    error_message = "Synthetic object was not found."
                elif error.response.status_code >= 500:
                    error_code = "upstream_unavailable"
                    error_message = "Synthetic read was unavailable."
                else:
                    error_code = "upstream_rejected"
                    error_message = "Synthetic read request was rejected."
            except (ValidationError, KeyError, ValueError):
                error_code = "malformed_response"
                error_message = "Synthetic read returned an invalid response."
            except (ConnectionError, httpx.TransportError):
                error_code = "transport_error"
                error_message = "Synthetic read was unavailable."
            except Exception:  # noqa: BLE001 - raw transport details must not cross this boundary
                error_code = "tool_error"
                error_message = "Synthetic read failed safely."
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            latency_ms = max(0, round((monotonic() - started) * 1_000))
            source_ids = _source_ids(data)
            result = ToolResult[T](
                ok=data is not None,
                data=data,
                error_code=error_code,
                error_message=error_message,
                source_system=source_system,
                source_ids=source_ids,
                observed_at=self._now(),
                latency_ms=latency_ms,
                attempt=attempt,
            )
            last_result = result
            will_retry = (
                not result.ok
                and result.error_code in _TRANSIENT_ERROR_CODES
                and attempt < self._max_attempts
            )
            observer.finished(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=cast(ToolResult[BaseModel], result),
                response_summary=_response_summary(result),
                will_retry=will_retry,
            )
            if not will_retry:
                return result
        if last_result is None:  # pragma: no cover - max_attempts validation guarantees a call
            raise RuntimeError("tool execution made no attempts")
        return last_result


def _source_ids(data: BaseModel | None) -> list[str]:
    if data is None:
        return []
    if isinstance(data, CustomerRecord):
        return [str(data.account_id)]
    if isinstance(data, SubscriptionRecord):
        return [str(data.subscription_id)]
    if isinstance(data, InvoicePage):
        return [str(item.invoice_id) for item in data.items]
    if isinstance(data, PaymentAttemptPage):
        return [str(item.payment_attempt_id) for item in data.items]
    if isinstance(data, PolicyRecord):
        return [str(data.policy_id)]
    return []


def _response_summary[T: BaseModel](
    result: ToolResult[T],
) -> dict[str, str | int | list[str]]:
    if result.ok:
        return {"source_ids": result.source_ids, "object_count": len(result.source_ids)}
    return {"error_code": result.error_code or "unknown", "source_ids": []}
