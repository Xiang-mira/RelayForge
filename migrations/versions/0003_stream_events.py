"""Commit-ordered event publication independent of source transaction order."""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("stream_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source_event_id", sa.Integer, sa.ForeignKey("run_events.id"), unique=True, nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False))
    op.create_index("ix_stream_events_run_id", "stream_events", ["run_id"])


def downgrade():
    raise RuntimeError("Event history is not automatically dropped")
