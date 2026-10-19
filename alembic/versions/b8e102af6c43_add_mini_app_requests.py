"""Durable idempotency for Mini App marks; no historical data changes."""
from alembic import op
import sqlalchemy as sa

revision = 'add_mini_app_requests'
down_revision = 'add_event_source_and_time'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mini_app_requests',
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('payload_hash', sa.String(64), nullable=False),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('poop_events.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'request_id'))


def downgrade():
    op.drop_table('mini_app_requests')
