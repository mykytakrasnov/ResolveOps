"""Typed contracts for the synthetic AtlasFlow read-only tools."""

from datetime import date
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field

from resolveops.models.contracts import ContractModel


class LookupCustomerInput(ContractModel):
    customer_reference: str = Field(pattern=r"^org_atlas_\d{3}$")


class GetSubscriptionInput(ContractModel):
    account_id: UUID


class ListInvoicesInput(ContractModel):
    account_id: UUID
    from_date: date
    to_date: date
    limit: int = Field(default=50, ge=1, le=100)


class GetPaymentAttemptsInput(ContractModel):
    account_id: UUID
    invoice_id: UUID
    limit: int = Field(default=50, ge=1, le=100)


class GetPolicyInput(ContractModel):
    policy_key: str = Field(pattern=r"^[a-z0-9_]{1,80}$")
    version: str = Field(pattern=r"^\d{1,4}\.\d{1,4}$")


class CustomerRecord(ContractModel):
    account_id: UUID
    customer_reference: str = Field(pattern=r"^org_atlas_\d{3}$")
    name: str
    region: str
    status: str
    created_at: AwareDatetime


class SubscriptionRecord(ContractModel):
    subscription_id: UUID
    account_id: UUID
    plan: str
    status: str
    amount_cents: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    current_period_start: date
    current_period_end: date
    plan_limit_units: int = Field(gt=0)
    usage_units: int = Field(ge=0)
    previous_plan: str | None = None
    upgraded_at: AwareDatetime | None = None
    canceled_at: AwareDatetime | None = None


class InvoiceRecord(ContractModel):
    invoice_id: UUID
    account_id: UUID
    subscription_id: UUID
    period_start: date
    period_end: date
    amount_cents: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: str
    issued_at: AwareDatetime


class InvoicePage(ContractModel):
    items: list[InvoiceRecord]


class PaymentAttemptRecord(ContractModel):
    payment_attempt_id: UUID
    account_id: UUID
    invoice_id: UUID
    amount_cents: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: str
    processor_reference: str
    attempted_at: AwareDatetime


class PaymentAttemptPage(ContractModel):
    items: list[PaymentAttemptRecord]


class PolicyRecord(ContractModel):
    policy_id: UUID
    policy_key: str = Field(pattern=r"^[a-z0-9_]{1,80}$")
    version: str = Field(pattern=r"^\d{1,4}\.\d{1,4}$")
    action_type: str
    maximum_amount_cents: int | None = Field(default=None, gt=0)
    approval_required: bool
    effective_at: AwareDatetime
    body: str


class SyntheticReadBackend(Protocol):
    """Transport seam for an allowlisted synthetic systems client."""

    def lookup_customer(self, request: LookupCustomerInput) -> CustomerRecord: ...

    def get_subscription(self, request: GetSubscriptionInput) -> SubscriptionRecord: ...

    def list_invoices(self, request: ListInvoicesInput) -> InvoicePage: ...

    def get_payment_attempts(self, request: GetPaymentAttemptsInput) -> PaymentAttemptPage: ...

    def get_policy(self, request: GetPolicyInput) -> PolicyRecord: ...
