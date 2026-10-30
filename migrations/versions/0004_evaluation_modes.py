"""Explicit evaluation ownership and local experimental configurations."""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("mode", sa.String(24), nullable=False, server_default="full"))
    op.add_column("runs", sa.Column("execution_config", sa.JSON, nullable=False, server_default="{}"))
    op.add_column("runs", sa.Column("evaluation_run_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_run_evaluation", "runs", "evaluation_runs", ["evaluation_run_id"], ["id"])
    op.create_index("ix_runs_evaluation_run_id", "runs", ["evaluation_run_id"])
    op.create_check_constraint("ck_run_mode", "runs", "mode IN ('baseline','journal','full')")


def downgrade():
    raise RuntimeError("Evaluation evidence is not automatically dropped")
