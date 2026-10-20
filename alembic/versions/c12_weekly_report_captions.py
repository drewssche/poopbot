"""Combine weekly image and summary into one Telegram message."""
from alembic import op
import sqlalchemy as sa

revision = 'weekly_report_captions'
down_revision = 'weekly_report_cards'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('weekly_reports', sa.Column('caption_hash', sa.String(64)))
    # The concise caption depends only on this/previous week, including group ranks.
    op.execute('''CREATE OR REPLACE FUNCTION dirty_weekly_reports(cid bigint, day date) RETURNS void AS $$
    BEGIN
      UPDATE weekly_reports SET revision = revision + 1, attempts = 0, retry_at = NULL
      WHERE NOT disabled AND day BETWEEN period_start - 7 AND period_end
        AND (source_chat_id = cid OR (source_chat_id < 0 AND cid < 0));
    END; $$ LANGUAGE plpgsql;''')
    op.execute('UPDATE weekly_reports SET revision = revision + 1, attempts = 0, retry_at = NULL WHERE NOT disabled')


def downgrade():
    op.execute('''CREATE OR REPLACE FUNCTION dirty_weekly_reports(cid bigint, day date) RETURNS void AS $$
    BEGIN
      UPDATE weekly_reports SET revision = revision + 1, attempts = 0, retry_at = NULL
      WHERE NOT disabled AND ((source_chat_id = cid AND period_end >= day) OR
        (source_chat_id < 0 AND cid < 0 AND day BETWEEN period_start - 7 AND period_end));
    END; $$ LANGUAGE plpgsql;''')
    op.drop_column('weekly_reports', 'caption_hash')
