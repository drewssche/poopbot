"""Scoped recovery receipts and durable Telegram refresh jobs."""
from alembic import op
import sqlalchemy as sa

revision = 'mini_app_recovery_queue'
down_revision = 'add_mini_app_requests'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('mini_app_requests', sa.Column('chat_id', sa.BigInteger(), nullable=True))
    op.add_column('mini_app_requests', sa.Column('notify_date', sa.Date(), nullable=True))
    op.add_column('mini_app_requests', sa.Column('notify_pending', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('mini_app_requests', sa.Column('notify_attempts', sa.Integer(), server_default='0', nullable=False))
    op.add_column('mini_app_requests', sa.Column('notify_after', sa.DateTime(), nullable=True))
    op.create_index('ix_mini_app_notify_pending', 'mini_app_requests', ['notify_after'],
                    postgresql_where=sa.text('notify_pending = true'))


def downgrade():
    op.drop_index('ix_mini_app_notify_pending', table_name='mini_app_requests')
    for name in ['notify_after', 'notify_attempts', 'notify_pending', 'notify_date', 'chat_id']:
        op.drop_column('mini_app_requests', name)
