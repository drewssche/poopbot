from datetime import UTC, date, datetime
import importlib.util

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
import pytest

from app.db.base import Base
from app.db.models import Chat, PoopEvent, Session as DaySession, SessionUserState, User
from app.services.event_time_service import event_occurrence_time
from app.services.poop_event_service import create_event
from app.services.q1_service import _get_user_slot_counts, restore_streak_for_date, restore_recent_streak_window
from app.services.restore_period_service import add_missing_mark, RestorePeriod, restore_period, mark_forgotten_day
from app.services.stats_common import Range
from app.services.stats_metrics import compute_chat_period_metrics
from app.services.stats_rankings import collect_among_chats_snapshot, rank_chat_among_groups_by_total, collect_among_chats_slot_patterns
from app.services.stats_service import (_compute_chat_slot_patterns, _compute_slot_patterns,
    _compute_slot_patterns_from_state_rows, _compute_global_slot_patterns, build_stats_text_my)


@pytest.fixture
def db():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Chat(chat_id=-1, timezone='America/New_York'), Chat(chat_id=-2), User(user_id=1), User(user_id=2)])
        db.flush()
        yield db
    engine.dispose()


def session(db, cid, day):
    row=DaySession(chat_id=cid,session_date=day,status='closed')
    db.add(row);db.flush()
    return row


def test_ranking_uses_same_local_events_for_total_participants_and_record(db):
    day=date(2026,10,18);a=session(db,-1,day);b=session(db,-2,day)
    db.add_all([SessionUserState(session_id=a.session_id,user_id=1,poops_n=9),
                SessionUserState(session_id=a.session_id,user_id=2,poops_n=5),
                SessionUserState(session_id=b.session_id,user_id=1,poops_n=1)])
    for n in (1,2): create_event(db,a.session_id,1,n,origin_chat_id=-1)
    create_event(db,a.session_id,2,1,origin_chat_id=-2)  # Old copied event.
    create_event(db,b.session_id,1,1,origin_chat_id=-2)
    db.flush()
    r=Range(day,day);snap=collect_among_chats_snapshot(db,day,r)
    assert snap['top_total']==[(-1,2),(-2,1)]
    assert snap['top_avg'][0]==(-1,2.0,2,1)
    assert snap['record_day']==(-1,day,2)
    assert snap['total_poops_all']==3
    assert compute_chat_period_metrics(db,-1,r).total_poops==2
    assert rank_chat_among_groups_by_total(db,-1,r)==(1,2)
    assert sum(collect_among_chats_slot_patterns(db,r)[1].values())==3


@pytest.mark.parametrize('source', ['restore','late','reconciled','legacy_import'])
def test_unknown_occurrence_time_is_excluded_from_every_rhythm(db,source):
    day=date(2026,10,18);s=session(db,-1,day)
    create_event(db,s.session_id,1,1,origin_chat_id=-1,source=source)
    db.add(SessionUserState(session_id=s.session_id,user_id=1,poops_n=1));db.flush()
    ev=db.scalar(select(PoopEvent));st=db.get(SessionUserState,(s.session_id,1))
    assert ev.created_at is not None and ev.occurred_at is None and ev.source==source
    assert event_occurrence_time(ev) is None
    assert sum(_get_user_slot_counts(db,s.session_id,1).values())==0
    assert sum(_compute_slot_patterns(db,[s.session_id],1).values())==0
    assert sum(_compute_chat_slot_patterns(db,-1,Range(day,day))[0].values())==0
    assert sum(_compute_slot_patterns_from_state_rows([st],{(s.session_id,1):[ev]}).values())==0
    assert _compute_global_slot_patterns([st],{(s.session_id,1):[ev]})=={}
    assert sum(collect_among_chats_slot_patterns(db,Range(day,day))[1].values())==0
    assert compute_chat_period_metrics(db,-1,Range(day,day)).total_poops==1
    rendered=build_stats_text_my(db,-1,1,day,'all')
    assert 'Всего: 💩(1)' in rendered and 'глобальный стрик (по всем чатам): 1 дн.' in rendered


def test_new_regular_mark_has_known_time_and_chat_timezone_is_used(db):
    day=date(2026,10,18);s=session(db,-1,day)
    create_event(db,s.session_id,1,1,origin_chat_id=-1)
    ev=db.scalar(select(PoopEvent));assert ev.source=='recorded' and ev.occurred_at is not None
    ev.occurred_at=datetime(2026,10,18,13,0);db.flush()  # 09:00 New York, 16:00 Minsk.
    counts=_compute_chat_slot_patterns(db,-1,Range(day,day))[0]
    assert counts['morning']==1 and counts['afternoon']==0
    assert collect_among_chats_slot_patterns(db,Range(day,day))[0][-1]==counts


def test_legacy_time_is_preserved_as_utc_until_history_is_reviewed():
    ev=PoopEvent(created_at=datetime(2026,2,11,9,0))
    assert event_occurrence_time(ev)==datetime(2026,2,11,9,0,tzinfo=UTC)


@pytest.mark.parametrize('flow', ['period','forgotten','old_single','old_window'])
def test_all_recovery_entry_points_create_unknown_time_once(db,flow):
    today=date(2026,10,19);yesterday=date(2026,10,18)
    prev=session(db,-1,date(2026,10,17));create_event(db,prev.session_id,1,1,origin_chat_id=-1)
    closed=session(db,-1,yesterday)
    if flow=='period':
        action=lambda:restore_period(db,chat_id=-1,user_id=1,period=RestorePeriod(yesterday,yesterday),today=today)
    elif flow=='forgotten':
        action=lambda:mark_forgotten_day(db,sess=closed,user_id=1,today=today)
    elif flow=='old_single':
        action=lambda:restore_streak_for_date(db,-1,1,yesterday)
    else:
        action=lambda:restore_recent_streak_window(db,chat_id=-1,user_id=1,current_session_date=today,days_back=1)
    assert action()[0]
    db.flush();action();db.flush()
    evs=db.scalars(select(PoopEvent).where(PoopEvent.session_id==closed.session_id)).all()
    assert len(evs)==1 and evs[0].source in {'restore','late'}
    assert event_occurrence_time(evs[0]) is None


def test_additive_migration_does_not_rewrite_historical_rows():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    spec=importlib.util.spec_from_file_location('migration','alembic/versions/8a2c4e6f901b_add_event_source_and_time.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with engine.begin() as c:
        c.execute(text('CREATE TABLE poop_events (id INTEGER PRIMARY KEY, created_at DATETIME)'))
        c.execute(text("INSERT INTO poop_events VALUES (1, '2026-02-11 09:00:00')"))
        with Operations.context(MigrationContext.configure(c)): module.upgrade()
        assert c.execute(text('SELECT id, created_at, source, occurred_at FROM poop_events')).one()==(1,'2026-02-11 09:00:00',None,None)
    engine.dispose()
