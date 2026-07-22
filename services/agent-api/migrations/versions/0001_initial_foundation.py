"""Create ResolveOps application, audit, demo, evaluation, and graph schemas."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    for schema in ("app", "audit", "demo", "eval", "langgraph"):
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    _create_identity_tables()
    _create_case_and_run_tables()
    _create_execution_tables()
    _create_audit_tables()
    _create_evaluation_tables()


def _create_identity_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workos_user_id", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        schema="app",
    )
    op.create_table(
        "organizations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("mode IN ('demo', 'internal')", name="ck_organizations_mode"),
        schema="app",
    )
    op.create_table(
        "organization_memberships",
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("app.organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.Text(), primary_key=True),
        sa.CheckConstraint(
            "role IN ('operator', 'reviewer', 'admin')",
            name="ck_organization_memberships_role",
        ),
        schema="app",
    )


def _create_case_and_run_tables() -> None:
    op.create_table(
        "support_cases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("app.organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("dataset_case_id", sa.Text()),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("customer_reference", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "attachment_keys",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_by", UUID, sa.ForeignKey("app.users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "status IN ('open', 'investigating', 'waiting_for_approval', 'resolved', 'escalated')",
            name="ck_support_cases_status",
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_support_cases_id_organization"),
        schema="app",
    )
    op.create_index(
        "ix_support_cases_organization_created_at",
        "support_cases",
        ["organization_id", "created_at"],
        schema="app",
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("case_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False, unique=True),
        sa.Column("initiated_by", UUID, sa.ForeignKey("app.users.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_node", sa.Text()),
        sa.Column("graph_version", sa.Text(), nullable=False),
        sa.Column("prompt_bundle_version", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text()),
        sa.Column("langfuse_trace_id", sa.Text()),
        sa.Column("aws_request_id", sa.Text()),
        sa.Column("resolved_model", sa.Text()),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("execution_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_lease_until", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["app.support_cases.id", "app.support_cases.organization_id"],
            ondelete="RESTRICT",
            name="fk_workflow_runs_case_organization",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'running', 'waiting_for_approval', "
            "'completed', 'escalated', 'failed')",
            name="ck_workflow_runs_status",
        ),
        sa.CheckConstraint("execution_attempt >= 0", name="ck_workflow_runs_attempt"),
        sa.CheckConstraint("version >= 1", name="ck_workflow_runs_version"),
        schema="app",
    )
    op.create_index(
        "ix_workflow_runs_organization_created_at",
        "workflow_runs",
        ["organization_id", "created_at"],
        schema="app",
    )
    op.create_index("ix_workflow_runs_case_id", "workflow_runs", ["case_id"], schema="app")
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"], schema="app")
    op.create_index(
        "ix_workflow_runs_execution_lease",
        "workflow_runs",
        ["execution_lease_until"],
        schema="app",
        postgresql_where=sa.text("execution_lease_until IS NOT NULL"),
    )


def _create_execution_tables() -> None:
    op.create_table(
        "tool_executions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("app.workflow_runs.id"), nullable=False),
        sa.Column("tool_call_id", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("request_summary", JSONB, nullable=False),
        sa.Column("response_summary", JSONB),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("attempt >= 1", name="ck_tool_executions_attempt"),
        sa.UniqueConstraint("run_id", "tool_call_id", "attempt", name="uq_tool_execution_attempt"),
        schema="app",
    )
    op.create_table(
        "model_calls",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("app.workflow_runs.id"), nullable=False),
        sa.Column("node_name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("requested_model", sa.Text(), nullable=False),
        sa.Column("resolved_model", sa.Text()),
        sa.Column("prompt_name", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("generation_id", sa.Text()),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="app",
    )
    op.create_table(
        "action_proposals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("app.workflow_runs.id"), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("target_reference", sa.Text(), nullable=False),
        sa.Column("canonical_parameters", JSONB, nullable=False),
        sa.Column("proposal_hash", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("policy_key", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')", name="ck_proposals_risk"
        ),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'approved', 'rejected', "
            "'blocked', 'executed', 'invalidated')",
            name="ck_proposals_status",
        ),
        sa.CheckConstraint("proposal_hash ~ '^[0-9a-f]{64}$'", name="ck_proposals_hash"),
        schema="app",
    )
    op.create_table(
        "approval_requests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "proposal_id",
            UUID,
            sa.ForeignKey("app.action_proposals.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("requested_by", UUID, sa.ForeignKey("app.users.id"), nullable=False),
        sa.Column("decided_by", UUID, sa.ForeignKey("app.users.id")),
        sa.Column("decision", sa.Text()),
        sa.Column("comment", sa.Text()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('approve', 'reject')", name="ck_approval_decision"
        ),
        sa.CheckConstraint(
            "(decision IS NULL AND decided_by IS NULL AND decided_at IS NULL) OR "
            "(decision IS NOT NULL AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_approval_decision_complete",
        ),
        sa.CheckConstraint(
            "decision <> 'reject' OR length(btrim(comment)) > 0",
            name="ck_approval_rejection_comment",
        ),
        schema="app",
    )
    op.create_table(
        "executed_actions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "proposal_id",
            UUID,
            sa.ForeignKey("app.action_proposals.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'ambiguous')", name="ck_actions_status"
        ),
        schema="app",
    )
    op.create_table(
        "run_artifacts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("app.workflow_runs.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_run_artifacts_sha256"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_run_artifacts_size"),
        schema="app",
    )
    op.create_table(
        "idempotency_records",
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", JSONB),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_idempotency_request_hash"),
        schema="app",
    )
    op.create_index(
        "ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"], schema="app"
    )
    op.create_table(
        "demo_usage",
        sa.Column("usage_date", sa.Date(), primary_key=True),
        sa.Column("principal_type", sa.Text(), primary_key=True),
        sa.Column("principal_hash", sa.Text(), primary_key=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("upload_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("upload_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("model_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "run_count >= 0 AND upload_count >= 0 AND upload_bytes >= 0 AND model_calls >= 0",
            name="ck_demo_usage_nonnegative",
        ),
        schema="app",
    )


def _create_audit_tables() -> None:
    op.create_table(
        "workflow_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("app.workflow_runs.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("node_name", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("public_payload", JSONB, nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("sequence >= 1", name="ck_workflow_events_sequence"),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_workflow_events_hash"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_workflow_events_run_sequence"),
        schema="audit",
    )
    op.execute(
        """
        CREATE FUNCTION audit.prevent_workflow_event_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit.workflow_events is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER workflow_events_append_only
        BEFORE UPDATE OR DELETE ON audit.workflow_events
        FOR EACH ROW EXECUTE FUNCTION audit.prevent_workflow_event_mutation()
        """
    )


def _create_evaluation_tables() -> None:
    op.create_table(
        "cases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("dataset_case_id", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("ground_truth", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("dataset_case_id", "dataset_version", name="uq_eval_case_version"),
        schema="eval",
    )
    op.create_table(
        "runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("graph_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        schema="eval",
    )
    op.create_table(
        "results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("eval_run_id", UUID, sa.ForeignKey("eval.runs.id"), nullable=False),
        sa.Column("case_id", UUID, sa.ForeignKey("eval.cases.id"), nullable=False),
        sa.Column("workflow_run_id", UUID, sa.ForeignKey("app.workflow_runs.id")),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.UniqueConstraint("eval_run_id", "case_id", name="uq_eval_result_case"),
        schema="eval",
    )
    op.create_table(
        "metric_values",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("result_id", UUID, sa.ForeignKey("eval.results.id"), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.Column("passed", sa.Boolean()),
        sa.UniqueConstraint("result_id", "metric_name", name="uq_eval_metric_result"),
        schema="eval",
    )
    op.create_table(
        "baselines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("eval_run_id", UUID, sa.ForeignKey("eval.runs.id"), nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="eval",
    )


def downgrade() -> None:
    op.drop_table("baselines", schema="eval")
    op.drop_table("metric_values", schema="eval")
    op.drop_table("results", schema="eval")
    op.drop_table("runs", schema="eval")
    op.drop_table("cases", schema="eval")

    op.execute("DROP TRIGGER workflow_events_append_only ON audit.workflow_events")
    op.execute("DROP FUNCTION audit.prevent_workflow_event_mutation()")
    op.drop_table("workflow_events", schema="audit")

    op.drop_table("demo_usage", schema="app")
    op.drop_index(
        "ix_idempotency_records_expires_at", table_name="idempotency_records", schema="app"
    )
    op.drop_table("idempotency_records", schema="app")
    op.drop_table("run_artifacts", schema="app")
    op.drop_table("executed_actions", schema="app")
    op.drop_table("approval_requests", schema="app")
    op.drop_table("action_proposals", schema="app")
    op.drop_table("model_calls", schema="app")
    op.drop_table("tool_executions", schema="app")
    op.drop_index("ix_workflow_runs_execution_lease", table_name="workflow_runs", schema="app")
    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs", schema="app")
    op.drop_index("ix_workflow_runs_case_id", table_name="workflow_runs", schema="app")
    op.drop_index(
        "ix_workflow_runs_organization_created_at", table_name="workflow_runs", schema="app"
    )
    op.drop_table("workflow_runs", schema="app")
    op.drop_index(
        "ix_support_cases_organization_created_at", table_name="support_cases", schema="app"
    )
    op.drop_table("support_cases", schema="app")
    op.drop_table("organization_memberships", schema="app")
    op.drop_table("organizations", schema="app")
    op.drop_table("users", schema="app")

    for schema in ("langgraph", "eval", "demo", "audit", "app"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema}")
