"""Share the report outbox with monthly and yearly calendar cards."""
from alembic import op
import sqlalchemy as sa

revision = 'calendar_report_periods'
down_revision = 'weekly_report_captions'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('weekly_reports', sa.Column('report_period', sa.String(8), nullable=False, server_default='week'))
    op.add_column('weekly_reports', sa.Column('previous_period_start', sa.Date()))
    # Existing deliveries are all weekly. Preserve their keys, IDs and revisions.
    op.execute('UPDATE weekly_reports SET previous_period_start = period_start - 7')
    op.alter_column('weekly_reports', 'previous_period_start', nullable=False)
    op.create_check_constraint('ck_report_period', 'weekly_reports', "report_period IN ('week', 'month', 'year')")
    op.execute('''CREATE OR REPLACE FUNCTION dirty_weekly_reports(cid bigint, day date) RETURNS void AS $$
    BEGIN
      UPDATE weekly_reports SET revision = revision + 1, attempts = 0, retry_at = NULL
      WHERE NOT disabled AND day BETWEEN previous_period_start AND period_end
        AND (source_chat_id = cid OR (source_chat_id < 0 AND cid < 0));
    END; $$ LANGUAGE plpgsql;''')


def downgrade():
    # Never reinterpret a published month/year as a week on rollback.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM weekly_reports WHERE report_period <> 'week') THEN
        RAISE EXCEPTION 'Cannot downgrade while monthly/yearly report deliveries exist';
      END IF;
    END $$;""")
    op.execute('''CREATE OR REPLACE FUNCTION dirty_weekly_reports(cid bigint, day date) RETURNS void AS $$
    BEGIN
      UPDATE weekly_reports SET revision = revision + 1, attempts = 0, retry_at = NULL
      WHERE NOT disabled AND day BETWEEN period_start - 7 AND period_end
        AND (source_chat_id = cid OR (source_chat_id < 0 AND cid < 0));
    END; $$ LANGUAGE plpgsql;''')
    op.drop_constraint('ck_report_period', 'weekly_reports', type_='check')
    op.drop_column('weekly_reports', 'previous_period_start')
    op.drop_column('weekly_reports', 'report_period')
