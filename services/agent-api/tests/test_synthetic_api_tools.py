from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, date, datetime
from uuid import UUID

import httpx

from resolveops.tools.contracts import (
    GetPaymentAttemptsInput,
    GetPolicyInput,
    GetSubscriptionInput,
    ListInvoicesInput,
    LookupCustomerInput,
)
from resolveops.tools.synthetic_api import SyntheticApiBackend

NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
SECRET = "synthetic-test-secret"
ACCOUNT_ID = UUID("11111111-1111-5111-8111-111111111111")
SUBSCRIPTION_ID = UUID("55555555-5555-5555-8555-555555555555")
INVOICE_ID = UUID("44444444-4444-5444-8444-444444444444")


def _handler(request: httpx.Request) -> httpx.Response:
    timestamp = request.headers["X-Service-Timestamp"]
    nonce = request.headers["X-Service-Nonce"]
    account_id = request.headers.get("X-Service-Account-ID", "")
    canonical = "\n".join(
        (request.method, request.url.raw_path.decode(), timestamp, nonce, account_id)
    )
    expected = hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(request.headers["X-Service-Signature"], expected)
    assert timestamp == str(int(NOW.timestamp()))

    path = request.url.path
    if path == "/systems/v1/crm/accounts":
        return httpx.Response(
            200,
            json={
                "account_id": str(ACCOUNT_ID),
                "customer_reference": request.url.params["customer_reference"],
                "name": "Synthetic Company",
                "primary_email": "billing@example.com",
                "region": "us-east",
                "status": "active",
                "created_at": "2025-01-01T00:00:00Z",
            },
        )
    if path.endswith("/subscription"):
        return httpx.Response(
            200,
            json={
                "subscription_id": str(SUBSCRIPTION_ID),
                "account_id": str(ACCOUNT_ID),
                "plan": "starter",
                "status": "active",
                "amount_cents": 4900,
                "currency": "USD",
                "current_period_start": "2026-07-01",
                "current_period_end": "2026-08-01",
                "upgraded_at": None,
                "plan_limit_units": 1000,
                "usage_units": 900,
                "previous_plan": None,
                "canceled_at": None,
            },
        )
    if path.endswith("/invoices"):
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "invoice_id": str(INVOICE_ID),
                        "account_id": str(ACCOUNT_ID),
                        "subscription_id": str(SUBSCRIPTION_ID),
                        "period_start": "2026-07-01",
                        "period_end": "2026-08-01",
                        "amount_cents": 4900,
                        "currency": "USD",
                        "status": "paid",
                        "issued_at": "2026-07-21T00:00:00Z",
                    }
                ],
                "page": {"limit": 50, "next_cursor": None},
            },
        )
    if path.endswith("/payment-attempts"):
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "payment_attempt_id": "66666666-6666-5666-8666-666666666666",
                        "account_id": str(ACCOUNT_ID),
                        "invoice_id": str(INVOICE_ID),
                        "amount_cents": 4900,
                        "currency": "USD",
                        "status": "succeeded",
                        "processor_reference": "pay_synthetic",
                        "attempted_at": "2026-07-22T00:00:00Z",
                    }
                ],
                "page": {"limit": 50, "next_cursor": None},
            },
        )
    return httpx.Response(
        200,
        json={
            "policy_id": "77777777-7777-5777-8777-777777777777",
            "policy_key": "billing_duplicate_credit",
            "version": "3.0",
            "action_type": "apply_account_credit",
            "maximum_amount_cents": 10000,
            "approval_required": True,
            "effective_at": "2026-01-01T00:00:00Z",
            "body": "Synthetic policy.",
        },
    )


def test_synthetic_backend_uses_only_typed_signed_allowlisted_routes() -> None:
    backend = SyntheticApiBackend(
        base_url="https://resolveops.example",
        hmac_secret=SECRET,
        now=lambda: NOW,
        transport=httpx.MockTransport(_handler),
    )

    customer = backend.lookup_customer(LookupCustomerInput(customer_reference="org_atlas_001"))
    subscription = backend.get_subscription(GetSubscriptionInput(account_id=ACCOUNT_ID))
    invoices = backend.list_invoices(
        ListInvoicesInput(
            account_id=ACCOUNT_ID,
            from_date=date(2026, 7, 1),
            to_date=date(2026, 7, 31),
        )
    )
    attempts = backend.get_payment_attempts(
        GetPaymentAttemptsInput(account_id=ACCOUNT_ID, invoice_id=INVOICE_ID)
    )
    policy = backend.get_policy(
        GetPolicyInput(policy_key="billing_duplicate_credit", version="3.0")
    )

    assert customer.account_id == ACCOUNT_ID
    assert subscription.subscription_id == SUBSCRIPTION_ID
    assert invoices.items[0].invoice_id == INVOICE_ID
    assert attempts.items[0].invoice_id == INVOICE_ID
    assert policy.policy_key == "billing_duplicate_credit"
