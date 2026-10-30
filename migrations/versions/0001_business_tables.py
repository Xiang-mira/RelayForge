"""Initial business schema. Checkpoint and external truth schemas remain separate.

Revision ID: 0001
Revises: none
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tasks", sa.Column("id", sa.String(36), primary_key=True), sa.Column("title", sa.String(200), nullable=False), sa.Column("template", sa.String(80), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("branches", sa.Column("id", sa.String(36), primary_key=True), sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False), sa.Column("parent_id", sa.String(36), sa.ForeignKey("branches.id")), sa.Column("checkpoint_id", sa.String(200)), sa.Column("workspace", sa.Text), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("runs", sa.Column("id", sa.String(36), primary_key=True), sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False), sa.Column("branch_id", sa.String(36), sa.ForeignKey("branches.id"), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("fault", sa.String(80), nullable=False), sa.Column("owner", sa.String(80)), sa.Column("lease_until", sa.DateTime(timezone=True)), sa.Column("test_exit_code", sa.Integer), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("effects",
        sa.Column("effect_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("logical_action_id", sa.String(100), nullable=False),
        sa.Column("parent_effect_id", sa.String(36), sa.ForeignKey("effects.effect_id")),
        sa.Column("tool_name", sa.String(80), nullable=False), sa.Column("effect_class", sa.String(40), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False), sa.Column("payload_summary", sa.JSON, nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("external_receipt", sa.JSON),
        sa.Column("reconciliation_method", sa.String(80)), sa.Column("reconciliation_result", sa.JSON),
        sa.Column("policy_decision", sa.String(10), nullable=False), sa.Column("approval_id", sa.String(36)),
        sa.Column("attempt_count", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("branch_id", "logical_action_id", name="uq_effect_branch_action"),
        sa.CheckConstraint("status IN ('PREPARED','IN_FLIGHT','CONFIRMED','FAILED','UNKNOWN')", name="ck_effect_status"),
        sa.CheckConstraint("effect_class IN ('READ_ONLY','WORKSPACE_MUTATION','IDEMPOTENT_WRITE','NON_IDEMPOTENT_WRITE','UNKNOWN_OUTCOME')", name="ck_effect_class"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_effect_attempts"))
    op.create_table("approvals", sa.Column("id", sa.String(36), primary_key=True), sa.Column("effect_id", sa.String(36), sa.ForeignKey("effects.effect_id"), nullable=False), sa.Column("branch_id", sa.String(36), sa.ForeignKey("branches.id"), nullable=False), sa.Column("payload_hash", sa.String(64), nullable=False), sa.Column("decision", sa.String(16), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("decided_at", sa.DateTime(timezone=True)))
    op.create_foreign_key("fk_effect_approval", "effects", "approvals", ["approval_id"], ["id"])
    op.create_table("run_events", sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False), sa.Column("effect_id", sa.String(36), sa.ForeignKey("effects.effect_id")), sa.Column("type", sa.String(80), nullable=False), sa.Column("data", sa.JSON, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("recovery_decisions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("effect_id", sa.String(36), sa.ForeignKey("effects.effect_id"), nullable=False), sa.Column("evidence", sa.JSON, nullable=False), sa.Column("decision", sa.String(80), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("effect_references", sa.Column("id", sa.String(36), primary_key=True), sa.Column("branch_id", sa.String(36), sa.ForeignKey("branches.id"), nullable=False), sa.Column("effect_id", sa.String(36), sa.ForeignKey("effects.effect_id"), nullable=False), sa.UniqueConstraint("branch_id", "effect_id", name="uq_branch_reference"))
    op.create_table("evaluation_suites", sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(200), nullable=False), sa.Column("config", sa.JSON, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("evaluation_runs", sa.Column("id", sa.String(36), primary_key=True), sa.Column("suite_id", sa.String(36), sa.ForeignKey("evaluation_suites.id"), nullable=False), sa.Column("mode", sa.String(24), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("evaluation_cases", sa.Column("id", sa.String(36), primary_key=True), sa.Column("suite_id", sa.String(36), sa.ForeignKey("evaluation_suites.id"), nullable=False), sa.Column("template", sa.String(80), nullable=False), sa.Column("fault", sa.String(80), nullable=False), sa.Column("seed", sa.Integer, nullable=False))
    op.create_table("evaluation_results", sa.Column("id", sa.String(36), primary_key=True), sa.Column("evaluation_run_id", sa.String(36), sa.ForeignKey("evaluation_runs.id"), nullable=False), sa.Column("case_id", sa.String(36), sa.ForeignKey("evaluation_cases.id"), nullable=False), sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id")), sa.Column("observed", sa.JSON, nullable=False))
    for table, columns in {"branches": ["task_id"], "runs": ["task_id", "branch_id", "status"], "effects": ["run_id", "branch_id", "status"], "approvals": ["effect_id"], "run_events": ["run_id"], "recovery_decisions": ["effect_id"]}.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    raise RuntimeError("Destructive downgrade requires an explicit backup/recovery operation; not automatic")
