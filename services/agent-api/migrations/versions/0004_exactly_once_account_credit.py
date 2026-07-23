"""Persist approval hashes and exactly-once synthetic account credits."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_exactly_once_account_credit"
down_revision: str | None = "0003_proposal_immutability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column("decision_proposal_hash", sa.Text()),
        schema="app",
    )
    op.execute(
        """
        UPDATE app.approval_requests AS request
        SET decision_proposal_hash = proposal.proposal_hash
        FROM app.action_proposals AS proposal
        WHERE request.proposal_id = proposal.id
          AND request.decision IS NOT NULL
        """
    )
    op.create_check_constraint(
        "ck_approval_decision_proposal_hash",
        "approval_requests",
        "(decision IS NULL AND decision_proposal_hash IS NULL) OR "
        "(decision IS NOT NULL AND decision_proposal_hash ~ '^[0-9a-f]{64}$')",
        schema="app",
    )

    op.create_table(
        "account_credits",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("app.organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            UUID,
            sa.ForeignKey("app.support_cases.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "proposal_id",
            UUID,
            sa.ForeignKey("app.action_proposals.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("account_reference", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "amount_cents > 0 AND amount_cents <= 10000",
            name="ck_account_credits_amount",
        ),
        sa.CheckConstraint("currency = 'USD'", name="ck_account_credits_currency"),
        schema="demo",
    )
    op.create_index(
        "ix_account_credits_organization_created_at",
        "account_credits",
        ["organization_id", "created_at"],
        schema="demo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_credits_organization_created_at",
        table_name="account_credits",
        schema="demo",
    )
    op.drop_table("account_credits", schema="demo")
    op.drop_constraint(
        "ck_approval_decision_proposal_hash",
        "approval_requests",
        schema="app",
        type_="check",
    )
    op.drop_column("approval_requests", "decision_proposal_hash", schema="app")
