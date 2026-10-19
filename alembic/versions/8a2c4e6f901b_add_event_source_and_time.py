"""Separate event provenance and occurrence time; leave historical rows untouched."""
from alembic import op
import sqlalchemy as sa

revision = "add_event_source_and_time"
down_revision = "add_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("poop_events", sa.Column("source", sa.String(32), nullable=True))
    op.add_column("poop_events", sa.Column("occurred_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("poop_events", "occurred_at")
    op.drop_column("poop_events", "source")
