"""Attach live weekly calendars to newly published daily questions."""
from alembic import op
import sqlalchemy as sa

revision = 'daily_question_cards'
down_revision = 'calendar_report_periods'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('weekly_reports', sa.Column('q1_session_id', sa.Integer(),
        sa.ForeignKey('sessions.session_id', ondelete='CASCADE')))
    op.add_column('weekly_reports', sa.Column('q1_flow_text', sa.Text()))
    op.add_column('weekly_reports', sa.Column('q1_flow_markup', sa.Text()))
    op.create_index('ix_weekly_reports_q1_session_id', 'weekly_reports', ['q1_session_id'])
    op.execute('''CREATE OR REPLACE FUNCTION dirty_weekly_reports(cid bigint, day date) RETURNS void AS $$
    BEGIN
      UPDATE weekly_reports SET revision = revision + 1, attempts = 0, retry_at = NULL
      WHERE NOT disabled AND (
        (q1_session_id IS NULL AND day BETWEEN previous_period_start AND period_end
          AND (source_chat_id = cid OR (source_chat_id < 0 AND cid < 0)))
        OR (q1_session_id IS NOT NULL AND source_chat_id = cid
          AND day BETWEEN period_start AND period_end));
    END; $$ LANGUAGE plpgsql;''')


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM weekly_reports WHERE q1_session_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Cannot downgrade while daily question cards exist';
      END IF;
    END $$;""")
    op.execute('''CREATE OR REPLACE FUNCTION dirty_weekly_reports(cid bigint, day date) RETURNS void AS $$
    BEGIN
      UPDATE weekly_reports SET revision = revision + 1, attempts = 0, retry_at = NULL
      WHERE NOT disabled AND day BETWEEN previous_period_start AND period_end
        AND (source_chat_id = cid OR (source_chat_id < 0 AND cid < 0));
    END; $$ LANGUAGE plpgsql;''')
    op.drop_index('ix_weekly_reports_q1_session_id', 'weekly_reports')
    op.drop_column('weekly_reports', 'q1_flow_markup')
    op.drop_column('weekly_reports', 'q1_flow_text')
    op.drop_column('weekly_reports', 'q1_session_id')
