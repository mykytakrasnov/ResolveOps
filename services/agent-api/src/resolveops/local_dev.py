"""Local-only API assembly and deterministic AtlasFlow database seeding."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import psycopg
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from resolveops.api.app import create_app
from resolveops.api.runs import Principal, require_principal
from resolveops.repositories.runs import DatabaseRunRepository
from resolveops.synthetic_data import PublicCase

LOCAL_ORGANIZATION_ID = UUID("11111111-1111-5111-8111-111111111111")
LOCAL_USER_ID = UUID("22222222-2222-5222-8222-222222222222")


def _normalize_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def load_public_cases(generated_root: Path) -> list[PublicCase]:
    case_root = generated_root / "synthetic" / "v1" / "cases" / "public"
    case_files = sorted(case_root.glob("*.json"))
    if not case_files:
        raise RuntimeError(
            f"No generated AtlasFlow cases found at {case_root}. "
            "Generate it before starting local development."
        )
    return [PublicCase.model_validate_json(path.read_text(encoding="utf-8")) for path in case_files]


def seed_local_database(database_url: str, generated_root: Path) -> int:
    """Idempotently project public synthetic cases into the local run database."""

    cases = load_public_cases(generated_root)
    with psycopg.connect(_normalize_dsn(database_url)) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO app.users (id, workos_user_id, display_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET workos_user_id = EXCLUDED.workos_user_id,
                display_name = EXCLUDED.display_name
            """,
            (LOCAL_USER_ID, "workos_local_synthetic_operator", "Synthetic Operator"),
        )
        cursor.execute(
            """
            INSERT INTO app.organizations (id, name, slug, mode)
            VALUES (%s, %s, %s, 'demo')
            ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name, slug = EXCLUDED.slug, mode = EXCLUDED.mode
            """,
            (LOCAL_ORGANIZATION_ID, "AtlasFlow Local Demo", "atlasflow-local-demo"),
        )
        cursor.execute(
            """
            INSERT INTO app.organization_memberships (organization_id, user_id, role)
            VALUES (%s, %s, 'operator'), (%s, %s, 'reviewer')
            ON CONFLICT DO NOTHING
            """,
            (LOCAL_ORGANIZATION_ID, LOCAL_USER_ID, LOCAL_ORGANIZATION_ID, LOCAL_USER_ID),
        )
        for case in cases:
            cursor.execute(
                """
                INSERT INTO app.support_cases (
                    id, organization_id, dataset_case_id, subject, body,
                    customer_reference, status, attachment_keys, created_by, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET dataset_case_id = EXCLUDED.dataset_case_id,
                    subject = EXCLUDED.subject,
                    body = EXCLUDED.body,
                    customer_reference = EXCLUDED.customer_reference,
                    attachment_keys = EXCLUDED.attachment_keys
                """,
                (
                    case.case_id,
                    LOCAL_ORGANIZATION_ID,
                    str(case.case_id),
                    case.subject,
                    case.body,
                    case.customer_reference,
                    Jsonb(case.attachments),
                    LOCAL_USER_ID,
                    case.created_at,
                ),
            )
    return len(cases)


def _local_principal() -> Principal:
    return Principal(
        organization_id=LOCAL_ORGANIZATION_ID,
        user_id=LOCAL_USER_ID,
        roles=frozenset({"operator", "reviewer"}),
    )


def create_local_app() -> FastAPI:
    database_url = os.getenv("DATABASE_URL_POOLED")
    if not database_url:
        raise RuntimeError("DATABASE_URL_POOLED is required for the local development API")
    application = create_app(DatabaseRunRepository(database_url))
    application.dependency_overrides[require_principal] = _local_principal
    return application


def main() -> int:
    database_url = os.getenv("DATABASE_URL_DIRECT")
    generated_root = os.getenv("RESOLVEOPS_SYNTHETIC_DATA_ROOT")
    if not database_url:
        raise RuntimeError("DATABASE_URL_DIRECT is required to seed local development data")
    if not generated_root:
        raise RuntimeError("RESOLVEOPS_SYNTHETIC_DATA_ROOT is required to seed local data")
    count = seed_local_database(database_url, Path(generated_root))
    print(f"seeded {count} synthetic AtlasFlow cases for local development")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
