"""Persist verified initial workspace snapshot reference."""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("branches", sa.Column("initial_snapshot_id", sa.String(36), nullable=True))


def downgrade():
    raise RuntimeError("No automatic destructive downgrade")
