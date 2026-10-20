"""Remember delivered visual content independently of captions and PNG encoding."""
from alembic import op
import sqlalchemy as sa

revision = 'report_visual_fingerprint'
down_revision = 'daily_question_cards'
branch_labels = None
depends_on = None


def upgrade():
    # Existing messages adopt a fingerprint on their next normal refresh.
    op.add_column('weekly_reports', sa.Column('visual_hash', sa.String(64)))


def downgrade():
    op.drop_column('weekly_reports', 'visual_hash')
