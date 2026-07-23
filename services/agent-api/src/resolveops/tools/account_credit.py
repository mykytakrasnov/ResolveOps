"""Typed synthetic account-credit side effect with query-by-key recovery."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from pydantic import AwareDatetime, Field

from resolveops.models.contracts import ContractModel

DB_CONNECT_TIMEOUT_SECONDS = 5
DB_STATEMENT_TIMEOUT_MILLISECONDS = 10_000
DB_LOCK_TIMEOUT_MILLISECONDS = 5_000


class AccountCreditError(Exception):
    """Base class for deterministic synthetic credit failures."""


class AmbiguousAccountCreditError(AccountCreditError):
    """The write may have committed and must be recovered by idempotency key."""


class DuplicateCaseCreditError(AccountCreditError):
    """A different proposal already credited the same synthetic case."""


class AccountCreditInput(ContractModel):
    organization_id: UUID
    case_id: UUID
    proposal_id: UUID
    account_reference: str = Field(min_length=1, max_length=160)
    amount_cents: int = Field(gt=0, le=10_000)
    currency: str = Field(pattern=r"^USD$")
    idempotency_key: str = Field(min_length=1, max_length=255)


class AccountCreditRecord(ContractModel):
    credit_id: UUID
    organization_id: UUID
    case_id: UUID
    proposal_id: UUID
    account_reference: str
    amount_cents: int
    currency: str
    idempotency_key: str
    created_at: AwareDatetime


def _normalize_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


class DatabaseAccountCreditTool:
    """Persist a synthetic credit exactly once inside the bounded demo schema."""

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

    def apply_account_credit(self, request: AccountCreditInput) -> AccountCreditRecord:
        """Insert once, returning the original record for an idempotent replay."""

        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO demo.account_credits (
                        id, organization_id, case_id, proposal_id, account_reference,
                        amount_cents, currency, idempotency_key
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING *
                    """,
                    (
                        uuid4(),
                        request.organization_id,
                        request.case_id,
                        request.proposal_id,
                        request.account_reference,
                        request.amount_cents,
                        request.currency,
                        request.idempotency_key,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        "SELECT * FROM demo.account_credits WHERE idempotency_key = %s",
                        (request.idempotency_key,),
                    )
                    row = cursor.fetchone()
                if row is None:  # pragma: no cover - unique-key replay always returns its row
                    raise AccountCreditError("account credit persistence returned no row")
                record = _record_from_row(row)
                if (
                    record.organization_id != request.organization_id
                    or record.case_id != request.case_id
                    or record.proposal_id != request.proposal_id
                    or record.account_reference != request.account_reference
                    or record.amount_cents != request.amount_cents
                    or record.currency != request.currency
                ):
                    raise AccountCreditError(
                        "the idempotency key belongs to a different synthetic account credit"
                    )
        except psycopg.errors.UniqueViolation as error:
            if error.diag.constraint_name in {
                "account_credits_case_id_key",
                "account_credits_proposal_id_key",
            }:
                raise DuplicateCaseCreditError(
                    "the synthetic case already has an account credit"
                ) from error
            raise
        except psycopg.OperationalError as error:
            raise AmbiguousAccountCreditError(
                "the synthetic account-credit write outcome is ambiguous"
            ) from error

        # This hook deliberately runs after commit so tests can model a transport
        # disconnect where the caller cannot tell whether the write succeeded.
        self._after_commit(record)
        return record

    def get_by_idempotency_key(
        self,
        *,
        organization_id: UUID,
        idempotency_key: str,
    ) -> AccountCreditRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM demo.account_credits
                WHERE organization_id = %s AND idempotency_key = %s
                """,
                (organization_id, idempotency_key),
            )
            row = cursor.fetchone()
            return _record_from_row(row) if row is not None else None

    def _after_commit(self, record: AccountCreditRecord) -> None:
        del record


def _record_from_row(row: dict[str, Any]) -> AccountCreditRecord:
    created_at = row["created_at"]
    if not isinstance(created_at, datetime):  # pragma: no cover - PostgreSQL type contract
        raise AccountCreditError("account credit timestamp is invalid")
    return AccountCreditRecord(
        credit_id=row["id"],
        organization_id=row["organization_id"],
        case_id=row["case_id"],
        proposal_id=row["proposal_id"],
        account_reference=row["account_reference"],
        amount_cents=row["amount_cents"],
        currency=row["currency"],
        idempotency_key=row["idempotency_key"],
        created_at=created_at,
    )
