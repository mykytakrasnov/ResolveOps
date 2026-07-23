"""LangGraph PostgreSQL checkpointer wiring and explicit schema setup."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg.rows import dict_row

CHECKPOINT_SCHEMA = "langgraph"
CHECKPOINT_CONNECT_TIMEOUT_SECONDS = 5
CHECKPOINT_STATEMENT_TIMEOUT_MILLISECONDS = 10_000
CHECKPOINT_LOCK_TIMEOUT_MILLISECONDS = 5_000
_CONTRACTS_MODULE = "resolveops.models.contracts"
CHECKPOINT_MSGPACK_MODULE_ALLOWLIST = tuple(
    (_CONTRACTS_MODULE, class_name)
    for class_name in (
        "ActionType",
        "ActionProposalInput",
        "ArtifactKind",
        "CaseCategory",
        "CaseClassification",
        "DuplicateChargeValidation",
        "EvidenceGapAssessment",
        "EvidenceItem",
        "EvidenceVerification",
        "FinalResponse",
        "InvestigationPlan",
        "PolicyDecision",
        "ReadToolName",
        "RequestedToolCall",
        "ResolutionProposal",
        "RiskIndicator",
        "RiskLevel",
        "RunArtifact",
        "SourceSystem",
        "TicketInput",
        "Urgency",
        "WorkflowEvent",
        "WorkflowEventType",
        "WorkflowOutcome",
    )
)


def normalize_postgres_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def strict_checkpoint_serializer() -> JsonPlusSerializer:
    """Deserialize only LangGraph's safe built-in msgpack types, never pickle."""

    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=CHECKPOINT_MSGPACK_MODULE_ALLOWLIST,
    )


@asynccontextmanager
async def open_async_postgres_saver(dsn: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Open the runtime saver without creating or migrating checkpoint tables."""

    connection: AsyncConnection[dict[str, Any]] = await AsyncConnection.connect(
        normalize_postgres_dsn(dsn),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        connect_timeout=CHECKPOINT_CONNECT_TIMEOUT_SECONDS,
        options=(
            f"-c search_path={CHECKPOINT_SCHEMA} "
            f"-c statement_timeout={CHECKPOINT_STATEMENT_TIMEOUT_MILLISECONDS} "
            f"-c lock_timeout={CHECKPOINT_LOCK_TIMEOUT_MILLISECONDS}"
        ),
    )
    async with connection:
        result = await connection.execute(
            "SELECT to_regnamespace(%s) AS schema_oid",
            (CHECKPOINT_SCHEMA,),
        )
        schema_row = await result.fetchone()
        if schema_row is None or schema_row["schema_oid"] is None:
            raise RuntimeError("LangGraph checkpoint schema has not been migrated")
        yield AsyncPostgresSaver(
            connection,
            serde=strict_checkpoint_serializer(),
        )


async def setup_checkpoint_schema(dsn: str) -> None:
    """Apply package-owned checkpoint migrations as an explicit deployment task."""

    async with open_async_postgres_saver(dsn) as saver:
        await saver.setup()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup",))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _parser().parse_args(argv)
    dsn = os.getenv("DATABASE_URL_DIRECT")
    if not dsn:
        raise RuntimeError("DATABASE_URL_DIRECT is required for checkpoint setup")
    asyncio.run(setup_checkpoint_schema(dsn))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the deployment command
    raise SystemExit(main())
