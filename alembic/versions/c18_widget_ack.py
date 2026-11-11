"""Remember the last processed tap in the private daily widget's published state."""
from alembic import op
import sqlalchemy as sa

revision = "private_widget_ack"
down_revision = "private_menus"
branch_labels = depends_on = None


def upgrade():
    op.add_column("weekly_reports", sa.Column("widget_ack", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("weekly_reports", "widget_ack")
