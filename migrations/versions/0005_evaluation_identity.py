"""One persisted observation per experimental mode and paired case."""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_evaluation_case", "evaluation_cases", ["suite_id", "template", "fault", "seed"])
    op.create_unique_constraint("uq_evaluation_result", "evaluation_results", ["evaluation_run_id", "case_id"])


def downgrade():
    raise RuntimeError("Evaluation evidence is not automatically dropped")
