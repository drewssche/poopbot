"""Keep private widget navigation bound to its Telegram message."""

from alembic import op
import sqlalchemy as sa

revision = "private_menus"
down_revision = "personal_report_widgets"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "private_menus",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("message_id", sa.BigInteger(), primary_key=True),
        sa.Column("revision", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("private_menus")
