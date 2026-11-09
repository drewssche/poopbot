"""Remember the Telegram format of each personal report across edits."""

from alembic import op
import sqlalchemy as sa

revision = "personal_report_widgets"
down_revision = "report_visual_fingerprint"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "weekly_reports",
        sa.Column(
            "widget_report", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade():
    op.drop_column("weekly_reports", "widget_report")
