import os
import subprocess
import sys
from pathlib import Path


def test_initial_migration_renders_offline_without_credentials() -> None:
    service_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("DATABASE_URL_DIRECT", None)

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head", "--sql"],
        cwd=service_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for schema in ("app", "audit", "demo", "eval", "langgraph"):
        assert f"CREATE SCHEMA IF NOT EXISTS {schema}" in result.stdout
    for table in (
        "app.support_cases",
        "app.workflow_runs",
        "audit.workflow_events",
        "app.action_proposals",
        "app.approval_requests",
        "app.executed_actions",
        "demo.account_credits",
        "app.run_artifacts",
        "app.idempotency_records",
    ):
        assert f"CREATE TABLE {table}" in result.stdout
