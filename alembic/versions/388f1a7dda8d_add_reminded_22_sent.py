"""add reminded_22_sent

Revision ID: 388f1a7dda8d
Revises: a2df27456666
Create Date: 2026-02-10

"""
from alembic import op
import sqlalchemy as sa

revision = "388f1a7dda8d"
down_revision = "a2df27456666"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем с server_default, чтобы существующие строки получили false
    op.add_column(
        "sessions",
        sa.Column(
            "reminded_22_sent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Убираем default (не обязательно, но аккуратно)
    op.alter_column("sessions", "reminded_22_sent", server_default=None)


def downgrade() -> None:
    op.drop_column("sessions", "reminded_22_sent")
