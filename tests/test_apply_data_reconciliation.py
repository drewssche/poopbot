from datetime import date, datetime
import hashlib
import json

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import AppSetting, Chat, PoopEvent, Session as DaySession, SessionUserState, User
from app.services.data_reconciliation_preview import build_preview
from scripts import apply_data_reconciliation as repair


@pytest.fixture
def prepared():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Chat(chat_id=-1), Chat(chat_id=-2), User(user_id=1), User(user_id=2)])
        db.flush()
        for sid, cid, day in [(1,-1,date(2026,2,10)), (2,-1,date(2026,2,11)),
                              (3,-2,date(2026,2,11)), (4,-1,date(2026,10,17)),
                              (5,-1,date(2026,2,12))]:
            db.add(DaySession(session_id=sid,chat_id=cid,session_date=day,status='closed'))
        db.flush()
        db.add_all([SessionUserState(session_id=1,user_id=1,poops_n=2,bristol=4),
                    SessionUserState(session_id=2,user_id=1,poops_n=1),
                    SessionUserState(session_id=3,user_id=1,poops_n=1),
                    SessionUserState(session_id=4,user_id=1,poops_n=1,achievement_text='__streak_restore_recent__'),
                    SessionUserState(session_id=5,user_id=2,poops_n=1)])
        db.add_all([PoopEvent(id=1,session_id=2,user_id=1,origin_chat_id=-2,event_n=1,created_at=datetime(2026,2,11,9)),
                    PoopEvent(id=2,session_id=3,user_id=1,origin_chat_id=-2,event_n=1,created_at=datetime(2026,2,11,9)),
                    PoopEvent(id=3,session_id=4,user_id=1,origin_chat_id=-1,event_n=1,created_at=datetime(2026,10,18,9)),
                    PoopEvent(id=4,session_id=5,user_id=2,origin_chat_id=-2,event_n=1,created_at=datetime(2026,2,12,9))])
        db.commit()
    with engine.connect() as c:
        original = repair.read_snapshot(c)
    plan = build_preview(original,today=date(2026,10,19))
    yield engine, original, plan
    engine.dispose()


def test_apply_exact_plan_and_repeat_is_noop(prepared):
    engine, original, plan = prepared
    with engine.begin() as c:
        result = repair.apply_plan(c,original,plan,'a'*64,apply=True)
    assert (result['added'],result['deleted'],result['time_corrected']) == (2,1,1)
    assert result['global_total_before'] == result['global_total_after'] == 5
    assert result['current_streaks_changed'] == 0
    with engine.begin() as c:
        after = repair.read_snapshot(c)
        repeated = repair.apply_plan(c,original,plan,'a'*64,apply=True)
        assert repeated['status'] == 'already_applied'
        assert repair.read_snapshot(c) == after
        assert repair.event_map(after)[4] == repair.event_map(original)[4]
        assert repair.state_map(after)[5,2] == repair.state_map(original)[5,2]
        assert repair.event_map(after)[3]['source'] == 'legacy_restore'
        assert all(e['occurred_at'] is None for e in after['poop_events'])


def test_validation_does_not_write(prepared):
    engine, original, plan = prepared
    with engine.begin() as c:
        assert repair.apply_plan(c,original,plan,'b'*64)['status'] == 'validated'
        assert repair.read_snapshot(c) == original
        assert c.scalar(select(AppSetting.key)) is None


@pytest.mark.parametrize('changed', ['state', 'original', 'new_event'])
def test_changed_target_or_original_aborts_before_writes(prepared,changed):
    engine, original, plan = prepared
    with engine.begin() as c:
        if changed == 'state':
            c.execute(update(SessionUserState).where(SessionUserState.session_id==1).values(poops_n=3))
        elif changed == 'original':
            c.execute(update(PoopEvent).where(PoopEvent.id==2).values(bristol=7))
        else:
            c.execute(PoopEvent.__table__.insert().values(session_id=1,user_id=1,origin_chat_id=-1,event_n=1))
        baseline = repair.read_snapshot(c)
    with pytest.raises(ValueError,match='changed'):
        with engine.begin() as c:
            repair.apply_plan(c,original,plan,'c'*64,apply=True)
    with engine.connect() as c:
        assert repair.read_snapshot(c) == baseline
        assert c.scalar(select(AppSetting.key)) is None


def test_failed_postcheck_rolls_back_every_change_and_audit(prepared,monkeypatch):
    engine, original, plan = prepared
    real_metrics = repair.metrics
    calls = 0
    def mismatching_metrics(snapshot,today):
        nonlocal calls
        calls += 1
        result = real_metrics(snapshot,today)
        if calls == 2:
            result['global_users'][1]['total'] += 1
        return result
    monkeypatch.setattr(repair,'metrics',mismatching_metrics)
    with pytest.raises(ValueError,match='Global metrics changed'):
        with engine.begin() as c:
            repair.apply_plan(c,original,plan,'d'*64,apply=True)
    with engine.connect() as c:
        assert repair.read_snapshot(c) == original
        assert c.scalar(select(AppSetting.key)) is None


def test_altered_plan_rejected(prepared,tmp_path):
    _, original, plan = prepared
    snapshot_path, plan_path = tmp_path/'source.json', tmp_path/'plan.json'
    snapshot_path.write_text(json.dumps(original))
    plan['snapshot_sha256'] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    plan_path.write_text(json.dumps(plan))
    repair.load_approved(snapshot_path,plan_path)
    plan['deduplicate'][0]['copies'][0]['original_id'] = 4
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(ValueError,match='differs'):
        repair.load_approved(snapshot_path,plan_path)
