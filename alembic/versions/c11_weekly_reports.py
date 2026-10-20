"""Durable weekly reports; transactional invalidation from every mark writer."""
from alembic import op
import sqlalchemy as sa

revision = 'weekly_report_cards'
down_revision = 'mini_app_recovery_queue'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('weekly_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('delivery_key', sa.String(100), nullable=False, unique=True),
        sa.Column('source_chat_id', sa.BigInteger(), sa.ForeignKey('chats.chat_id', ondelete='CASCADE'), nullable=False),
        sa.Column('destination_chat_id', sa.BigInteger(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('anchor_date', sa.Date(), nullable=False),
        sa.Column('chat_title', sa.String(256)), sa.Column('label', sa.String(100)),
        sa.Column('photo_message_id', sa.BigInteger()),
        sa.Column('text_messages', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('card_hash', sa.String(64)),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('delivered_revision', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('retry_at', sa.DateTime()),
        sa.Column('disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('last_error', sa.String(200)),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_index('ix_weekly_reports_source_chat_id', 'weekly_reports', ['source_chat_id'])
    op.create_index('ix_weekly_reports_pending', 'weekly_reports', ['retry_at'],
                    postgresql_where=sa.text('NOT disabled AND revision > delivered_revision'))
    # Do not rewrite old reports or historical marks. Only newly registered cards
    # subscribe to changes. Triggers also cover bulk deletes, bot, restores and Mini App.
    op.execute("""
    CREATE FUNCTION dirty_weekly_reports(cid bigint, day date) RETURNS void AS $$
    BEGIN
      UPDATE weekly_reports SET revision = revision + 1, attempts = 0, retry_at = NULL
      WHERE NOT disabled AND (
        (source_chat_id = cid AND period_end >= day) OR
        (source_chat_id < 0 AND cid < 0 AND day BETWEEN period_start - 7 AND period_end)
      );
    END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE FUNCTION weekly_mark_changed() RETURNS trigger AS $$
    DECLARE s record;
    BEGIN
      IF TG_OP <> 'INSERT' THEN
        SELECT chat_id, session_date INTO s FROM sessions WHERE session_id = OLD.session_id;
        IF s.chat_id = OLD.origin_chat_id THEN PERFORM dirty_weekly_reports(s.chat_id, s.session_date); END IF;
      END IF;
      IF TG_OP <> 'DELETE' THEN
        SELECT chat_id, session_date INTO s FROM sessions WHERE session_id = NEW.session_id;
        IF s.chat_id = NEW.origin_chat_id THEN PERFORM dirty_weekly_reports(s.chat_id, s.session_date); END IF;
      END IF;
      RETURN NULL;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER weekly_mark_changed AFTER INSERT OR UPDATE OR DELETE ON poop_events
      FOR EACH ROW EXECUTE FUNCTION weekly_mark_changed();
    """)
    op.execute("""
    CREATE FUNCTION weekly_session_changed() RETURNS trigger AS $$
    BEGIN
      PERFORM dirty_weekly_reports(OLD.chat_id, OLD.session_date);
      IF TG_OP = 'UPDATE' THEN PERFORM dirty_weekly_reports(NEW.chat_id, NEW.session_date); END IF;
      RETURN OLD;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER weekly_session_deleted BEFORE DELETE ON sessions
      FOR EACH ROW EXECUTE FUNCTION weekly_session_changed();
    CREATE TRIGGER weekly_session_moved AFTER UPDATE OF session_date, chat_id ON sessions
      FOR EACH ROW EXECUTE FUNCTION weekly_session_changed();
    """)
    op.execute("""
    CREATE FUNCTION wake_weekly_reports() RETURNS trigger AS $$
    BEGIN
      PERFORM pg_notify('weekly_reports_changed', ''); RETURN NULL;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER weekly_report_created AFTER INSERT ON weekly_reports
      FOR EACH ROW EXECUTE FUNCTION wake_weekly_reports();
    CREATE TRIGGER weekly_report_changed AFTER UPDATE OF revision ON weekly_reports
      FOR EACH ROW EXECUTE FUNCTION wake_weekly_reports();
    """)


def downgrade():
    op.execute('DROP TRIGGER weekly_mark_changed ON poop_events')
    op.execute('DROP TRIGGER weekly_session_deleted ON sessions')
    op.execute('DROP TRIGGER weekly_session_moved ON sessions')
    op.drop_table('weekly_reports')
    for name, args in [('weekly_mark_changed',''), ('weekly_session_changed',''),
                       ('wake_weekly_reports',''), ('dirty_weekly_reports','bigint, date')]:
        op.execute(f'DROP FUNCTION {name}({args})')
