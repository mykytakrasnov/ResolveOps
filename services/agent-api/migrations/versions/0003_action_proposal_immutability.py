"""Protect immutable action proposal fields and enforce one proposal per run."""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_proposal_immutability"
down_revision: str | None = "0002_run_execution_lease_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_action_proposals_run_id",
        "action_proposals",
        ["run_id"],
        schema="app",
    )
    op.execute(
        """
        CREATE FUNCTION app.protect_action_proposal_immutable_fields()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'action proposals cannot be deleted';
          END IF;
          IF ROW(
            NEW.id,
            NEW.run_id,
            NEW.action_type,
            NEW.target_reference,
            NEW.canonical_parameters,
            NEW.proposal_hash,
            NEW.risk_level,
            NEW.policy_key,
            NEW.policy_version,
            NEW.idempotency_key,
            NEW.created_at
          ) IS DISTINCT FROM ROW(
            OLD.id,
            OLD.run_id,
            OLD.action_type,
            OLD.target_reference,
            OLD.canonical_parameters,
            OLD.proposal_hash,
            OLD.risk_level,
            OLD.policy_key,
            OLD.policy_version,
            OLD.idempotency_key,
            OLD.created_at
          ) THEN
            RAISE EXCEPTION 'immutable action proposal fields cannot be changed';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER action_proposals_immutable
        BEFORE UPDATE OR DELETE ON app.action_proposals
        FOR EACH ROW EXECUTE FUNCTION app.protect_action_proposal_immutable_fields()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER action_proposals_immutable ON app.action_proposals")
    op.execute("DROP FUNCTION app.protect_action_proposal_immutable_fields()")
    op.drop_constraint(
        "uq_action_proposals_run_id",
        "action_proposals",
        schema="app",
        type_="unique",
    )
