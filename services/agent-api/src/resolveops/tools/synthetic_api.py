"""Allowlisted HMAC client for the synthetic AtlasFlow systems API."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

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
)

HTTP_TIMEOUT_SECONDS = 4.5


class SyntheticApiBackend:
    """Calls only fixed routes; tool or ticket content can never supply a URL."""

    def __init__(
        self,
        *,
        base_url: str,
        hmac_secret: str,
        timeout_seconds: float = HTTP_TIMEOUT_SECONDS,
        now: Callable[[], datetime] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        is_local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "http" and not is_local_http)
        ):
            raise ValueError("synthetic API base URL must be HTTPS or local HTTP")
        if not hmac_secret:
            raise ValueError("synthetic API HMAC secret is required")
        self._base_url = base_url.rstrip("/")
        self._secret = hmac_secret.encode()
        self._timeout = httpx.Timeout(timeout_seconds)
        self._now = now or (lambda: datetime.now(UTC))
        self._transport = transport

    def lookup_customer(self, request: LookupCustomerInput) -> CustomerRecord:
        payload = self._get(
            "/systems/v1/crm/accounts",
            params={"customer_reference": request.customer_reference},
        )
        return CustomerRecord.model_validate(
            {key: payload[key] for key in CustomerRecord.model_fields}
        )

    def get_subscription(self, request: GetSubscriptionInput) -> SubscriptionRecord:
        payload = self._get(
            f"/systems/v1/billing/accounts/{request.account_id}/subscription",
            account_id=str(request.account_id),
        )
        return SubscriptionRecord.model_validate(
            {key: payload[key] for key in SubscriptionRecord.model_fields}
        )

    def list_invoices(self, request: ListInvoicesInput) -> InvoicePage:
        payload = self._get(
            f"/systems/v1/billing/accounts/{request.account_id}/invoices",
            params={
                "from": request.from_date.isoformat(),
                "to": request.to_date.isoformat(),
                "limit": str(request.limit),
            },
            account_id=str(request.account_id),
        )
        return InvoicePage.model_validate({"items": payload["items"]})

    def get_payment_attempts(self, request: GetPaymentAttemptsInput) -> PaymentAttemptPage:
        payload = self._get(
            f"/systems/v1/billing/invoices/{request.invoice_id}/payment-attempts",
            params={"account_id": str(request.account_id), "limit": str(request.limit)},
            account_id=str(request.account_id),
        )
        return PaymentAttemptPage.model_validate({"items": payload["items"]})

    def get_policy(self, request: GetPolicyInput) -> PolicyRecord:
        payload = self._get(
            f"/systems/v1/policies/{request.policy_key}",
            params={"version": request.version},
        )
        return PolicyRecord.model_validate(payload)

    def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            request = client.build_request("GET", path, params=params)
            timestamp = str(int(self._now().timestamp()))
            nonce = uuid4().hex
            path_and_query = request.url.raw_path.decode("ascii")
            canonical = "\n".join(("GET", path_and_query, timestamp, nonce, account_id or ""))
            signature = hmac.new(self._secret, canonical.encode(), hashlib.sha256).hexdigest()
            request.headers.update(
                {
                    "X-Service-Timestamp": timestamp,
                    "X-Service-Nonce": nonce,
                    "X-Service-Signature": signature,
                }
            )
            if account_id is not None:
                request.headers["X-Service-Account-ID"] = account_id
            response = client.send(request)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("synthetic API returned a non-object response")
        return payload
