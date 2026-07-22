"""Fence workflow execution leases with an ownership token."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_run_execution_lease_token"
down_revision: str | None = "0001_initial_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("execution_lease_token", postgresql.UUID(as_uuid=True)),
        schema="app",
    )
    op.create_check_constraint(
        "ck_workflow_runs_lease_complete",
        "workflow_runs",
        "(execution_lease_until IS NULL AND execution_lease_token IS NULL) OR "
        "(execution_lease_until IS NOT NULL AND execution_lease_token IS NOT NULL)",
        schema="app",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workflow_runs_lease_complete",
        "workflow_runs",
        schema="app",
        type_="check",
    )
    op.drop_column("workflow_runs", "execution_lease_token", schema="app")
