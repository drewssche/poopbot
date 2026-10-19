import hashlib
import hmac
import json
import time
from datetime import UTC, date, datetime
from urllib.parse import urlencode

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Chat, ChatMember, User, PoopEvent, Session as DaySession
from app.mini_app import Config, authenticate, create_app
from app.services.mini_app_service import read_view
from app.services.stats_common import Range
from app.services.stats_metrics import compute_chat_period_metrics
from app.services.stats_streaks import compute_user_chat_streak_live, compute_user_chat_best_streak_live

CONFIG = Config('test-token-not-real', 'https://example.test')


def signed(user=10, timestamp=None, **extra):
    fields = {'user': json.dumps({'id': user, 'first_name': 'Test'}),
              'auth_date': str(int(time.time()) if timestamp is None else timestamp), **extra}
    secret = hmac.new(b'WebAppData', CONFIG.token.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, '\n'.join(f'{k}={fields[k]}' for k in sorted(fields)).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_authentication_requires_valid_signed_recent_user():
    assert authenticate(signed(timestamp=10000), CONFIG, now=10001)["user"]["id"] == 10
    for raw in ['', 'hash=fake', signed(timestamp=10000)+'&auth_date=10000',
                signed(timestamp=10000).replace('Test', 'Other'), signed(timestamp=1),
                signed(timestamp=11000), signed(user=True, timestamp=10000)]:
        with pytest.raises(web.HTTPUnauthorized):
            authenticate(raw, CONFIG, now=10001)
    assert authenticate(signed(user=20, timestamp=10000), CONFIG, now=10001)["user"]["id"] == 20


@pytest.fixture
def db():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Chat(chat_id=-100, timezone='Europe/Minsk'), Chat(chat_id=-200),
                    User(user_id=10), User(user_id=20)])
        db.flush()
        db.add(ChatMember(chat_id=-100, user_id=10))
        for cid, day, user, origin, source, occurred, n in [
            (-100, '2026-09-30', 10, -100, 'recorded', datetime(2026,9,30,20), 1),
            (-100, '2026-10-01', 10, -100, 'recorded', datetime(2026,10,1,22), 1),
            (-100, '2026-10-01', 10, -100, 'restore', None, 2),
            (-100, '2026-10-01', 20, -100, 'recorded', datetime(2026,10,1,12), 1),
            (-100, '2026-10-01', 10, -200, 'recorded', datetime(2026,10,1,12), 3),
            (-200, '2026-10-01', 10, -200, 'recorded', datetime(2026,10,1,12), 1),
            (-100, '2026-10-03', 10, -100, 'recorded', datetime(2026,10,3,12), 1),
        ]:
            session = next((s for s in db.query(DaySession).all() if s.chat_id == cid and str(s.session_date) == day), None)
            if session is None:
                session = DaySession(chat_id=cid, session_date=date.fromisoformat(day))
                db.add(session); db.flush()
            db.add(PoopEvent(session_id=session.session_id, user_id=user, origin_chat_id=origin, event_n=n,
                             source=source, occurred_at=occurred, bristol=4 if occurred else None,
                             feeling='great' if occurred else None))
            db.flush()
        db.commit()
        yield db
    engine.dispose()


def test_view_matches_bot_metrics_and_preserves_unknowns_without_writes(db):
    now = datetime(2026,10,2,12,tzinfo=UTC)
    view = read_view(db, -100, 10, None, now=now)
    assert view['today'] == '2026-10-02'
    assert view['stats'] == {'total': 3, 'best': 2, 'current': 2}
    assert view['stats']['total'] == compute_chat_period_metrics(db,-100,Range(date(2026,1,1),now.date()),10).total_poops
    assert view['stats']['current'] == compute_user_chat_streak_live(db,-100,10,now.date())
    assert view['stats']['best'] == compute_user_chat_best_streak_live(db,-100,10,now.date())
    assert view['counts'] == {'2026-09-30': 1, '2026-10-01': 2}
    assert len(view['events']) == 2
    assert view['events'][0]['time'] == '01:00'  # Chat timezone, session date is retained.
    assert view['events'][0]['date'] == '2026-10-01'
    assert view['events'][1]['time'] is None
    assert view['events'][1]['kind'] is None and view['events'][1]['feeling'] is None
    assert not db.new and not db.dirty and not db.deleted
    assert len(read_view(db,-100,10,'2026-09',now=now)['events']) == 1
    assert read_view(db,-100,10,'2025-01',now=now)['events'] == []
    for month in ['2026-13', 'bad', '2027-01', 1, '']:
        with pytest.raises(ValueError): read_view(db,-100,10,month,now=now)


def test_scope_membership_and_timezone_day_boundary(db):
    with pytest.raises(PermissionError): read_view(db,-200,10,None)
    view = read_view(db,-100,10,None,now=datetime(2026,9,30,22,tzinfo=UTC))
    assert view['today'] == '2026-10-01' and view['month'] == '2026-10'


@pytest.mark.asyncio
async def test_http_rejects_untrusted_requests_before_reader():
    calls = []
    def reader(chat,user,month):
        calls.append((chat,user['id'],month)); return {'readOnly': False}
    async with TestClient(TestServer(create_app(CONFIG,reader))) as client:
        headers = {'Origin':CONFIG.origin, 'Authorization':'tma '+signed()}
        for status, altered, body in [
            (401, {'Origin':CONFIG.origin}, {}),
            (403, {**headers,'Origin':'https://evil.test'}, {}),
            (401, {**headers,'Authorization':'tma '+signed(timestamp=1)}, {}),
            (400, headers, {'user_id':20}), (400, headers, {'chat_id':-200}),
            (400, headers, {'month':[]}), (400, headers, []),
        ]:
            response = await client.post('/view',headers=altered,json=body)
            assert response.status == status
            assert response.headers['Cache-Control'] == 'no-store'
        assert calls == []
        response = await client.post('/view',headers=headers,json={'month':'2026-10'})
        assert response.status == 200 and not (await response.json())['readOnly']
        assert calls == [(10,10,'2026-10')]
        assert (await client.post('/view?user_id=20',headers=headers,json={})).status == 400
        assert (await client.post('/view',headers=headers,data='{')).status == 400
        assert (await client.post('/view',headers=headers,json={'month':'x'*2000})).status == 413
        assert (await client.post('/events',headers=headers,json={})).status == 503
        assert (await client.delete('/view',headers=headers)).status == 405
        assert (await client.get('/health')).status == 200


@pytest.mark.asyncio
async def test_reader_failure_does_not_expose_records_or_secrets():
    def reader(*args): raise RuntimeError('private record or token')
    async with TestClient(TestServer(create_app(CONFIG,reader))) as client:
        response = await client.post('/view',headers={'Origin':CONFIG.origin,'Authorization':'tma '+signed()},json={})
        assert response.status == 503
        assert 'private' not in await response.text()


@pytest.mark.asyncio
async def test_slow_reads_are_bounded_and_scope_is_server_controlled():
    import asyncio
    import threading
    entered, release = threading.Event(), threading.Event()
    def reader(chat,user,month):
        assert (chat,user['id']) == (10,10)
        entered.set()
        release.wait(3)
        return {'readOnly':True}
    async with TestClient(TestServer(create_app(CONFIG,reader,workers=1,capacity=1))) as client:
        headers={'Origin':CONFIG.origin,'Authorization':'tma '+signed()}
        first=asyncio.ensure_future(client.post('/view',headers=headers,json={}))
        await asyncio.to_thread(entered.wait,2)
        try:
            assert entered.is_set()
            assert (await client.post('/view',headers=headers,json={})).status == 429
        finally:
            release.set()
        assert (await first).status == 200


@pytest.mark.asyncio
async def test_authorized_requests_are_rate_limited():
    calls=[]
    def reader(*args): calls.append(1); return {}
    async with TestClient(TestServer(create_app(CONFIG,reader))) as client:
        headers={'Origin':CONFIG.origin,'Authorization':'tma '+signed()}
        for _ in range(60):
            assert (await client.post('/view',headers=headers,json={})).status == 200
        assert (await client.post('/view',headers=headers,json={})).status == 429
        assert len(calls) == 60


def test_signed_group_selector_and_no_personal_fallback():
    from app.mini_app import resolve_chat
    from app.services.mini_app_links import group_scope
    group = group_scope(CONFIG.token, -100)
    identity = authenticate(signed(start_param=group, chat_type='supergroup'), CONFIG)
    assert resolve_chat(CONFIG, identity) == -100
    for extra in [{'start_param': group[:-1] + ('0' if group[-1] != '0' else '1')},
                  {'start_param': 'g200_fake'}, {'chat_type': 'supergroup'}]:
        with pytest.raises(web.HTTPForbidden):
            resolve_chat(CONFIG, authenticate(signed(**extra), CONFIG))
    with pytest.raises(web.HTTPForbidden):
        resolve_chat(CONFIG, identity, group_scope(CONFIG.token, -200))


@pytest.mark.asyncio
async def test_current_membership_required_for_both_read_and_write():
    from app.services.mini_app_links import group_scope
    calls = []
    allowed = False
    async def authorize(chat, user, *, writing):
        calls.append(('auth', chat, user, writing)); return allowed
    def reader(chat, user, month):
        calls.append(('read', chat, user['id'])); return {}
    def writer(chat, user, body):
        calls.append(('write', chat, user['id'])); return {}
    headers = {'Origin': CONFIG.origin, 'Authorization': 'tma '+signed(user=20, start_param=group_scope(CONFIG.token,-100))}
    async with TestClient(TestServer(create_app(CONFIG, reader, writer, authorize))) as client:
        for path in ['/view', '/events']:
            assert (await client.post(path, headers=headers, json={})).status == 403
        assert all(c[0] == 'auth' for c in calls)
        allowed = True
        for path in ['/view', '/events']:
            assert (await client.post(path, headers=headers, json={})).status == 200
        assert ('write', -100, 20) in calls and ('read', -100, 20) in calls
        # Raw IDs never select a different scope.
        assert (await client.post('/events', headers=headers, json={'chat_id':-200})).status == 400


@pytest.mark.asyncio
async def test_saved_mark_queues_message_refresh_without_waiting():
    def writer(*args): return {'eventId':'1'}
    def notifier(): pass
    async with TestClient(TestServer(create_app(CONFIG,lambda *a: {},writer,notifier=notifier))) as client:
        response = await client.post('/events',headers={'Origin':CONFIG.origin,'Authorization':'tma '+signed()},json={})
        assert response.status == 200
        assert await response.json() == {'eventId':'1','messageUpdateQueued':True}


def new_mark(**kw):
    from uuid import uuid4
    return {'requestId':str(uuid4()),'date':'2026-10-02','kind':1,'feeling':0,**kw}


def test_record_mark_atomic_idempotent_and_same_bot_statistics(db):
    from app.services.mini_app_service import record_mark, MarkRejected
    from app.db.models import SessionUserState, MiniAppRequest
    from app.services.q1_service import apply_plus
    now = datetime(2026,10,2,12,tzinfo=UTC)
    mark = new_mark()
    result = record_mark(db,-100,10,mark,now=now)
    db.commit()
    assert not result['repeated']
    assert record_mark(db,-100,10,mark,now=now)['repeated']
    db.commit()
    view = read_view(db,-100,10,None,now=now)
    assert view['stats']['total'] == 4 and view['counts']['2026-10-02'] == 1
    event = db.get(PoopEvent,int(result['eventId']))
    assert event.bristol == 4 and event.feeling == 'great' and event.origin_chat_id == -100
    state = db.get(SessionUserState,(event.session_id,10))
    assert state.poops_n == 1 and state.bristol == 4
    with pytest.raises(MarkRejected): record_mark(db,-100,10,{**mark,'kind':2},now=now)
    db.rollback()
    # Normal bot + and app + share the same event numbering/count/limit.
    assert apply_plus(db,event.session_id,10,-100)[0]
    db.commit()
    record_mark(db,-100,10,new_mark(),now=now)
    db.commit()
    assert read_view(db,-100,10,None,now=now)['counts']['2026-10-02'] == 3
    assert db.query(MiniAppRequest).count() == 2


def test_marks_reject_backdating_future_closed_window_limit_and_ambiguous_data(db):
    from app.services.mini_app_service import record_mark, MarkRejected
    now = datetime(2026,10,2,12,tzinfo=UTC)
    for when, mark in [(now,new_mark(date='2026-10-01')), (now,new_mark(date='2026-10-03')),
                       (datetime(2026,10,1,21,2,tzinfo=UTC),new_mark())]:
        with pytest.raises(MarkRejected): record_mark(db,-100,10,mark,now=when)
        db.rollback()
    # The fixture contains a cross-chat historical copy: leave every row untouched.
    before = db.query(PoopEvent).count()
    with pytest.raises(MarkRejected):
        record_mark(db,-100,10,new_mark(date='2026-10-01'),now=datetime(2026,10,1,12,tzinfo=UTC))
    db.rollback()
    assert db.query(PoopEvent).count() == before
    for _ in range(10):
        record_mark(db,-100,10,new_mark(),now=now); db.commit()
    with pytest.raises(MarkRejected): record_mark(db,-100,10,new_mark(),now=now)
    db.rollback()
    sess = db.scalar(__import__('sqlalchemy').select(DaySession).where(DaySession.chat_id==-100,DaySession.session_date==now.date()))
    sess.status='closed'; db.commit()
    with pytest.raises(MarkRejected): record_mark(db,-100,10,new_mark(),now=now)


def test_personal_onboarding_is_empty_and_isolated(db):
    from app.services.mini_app_service import prepare_scope, record_mark
    now = datetime(2026,10,2,12,tzinfo=UTC)
    prepare_scope(db,10,{'id':10,'first_name':'Test'})
    db.commit()
    assert read_view(db,10,10,None,now=now)['stats']['total'] == 0
    record_mark(db,10,10,new_mark(),now=now); db.commit()
    assert read_view(db,10,10,None,now=now)['stats']['total'] == 1
    assert read_view(db,-100,10,None,now=now)['stats']['total'] == 3
    with pytest.raises(PermissionError): prepare_scope(db,20,{'id':10})


@pytest.mark.parametrize('field,value',[('kind',True),('kind',4),('feeling',3),('feeling',False),('requestId','bad'),('date',[])])
def test_invalid_marks_never_write(db,field,value):
    from app.services.mini_app_service import record_mark
    with pytest.raises(ValueError): record_mark(db,-100,10,new_mark(**{field:value}),now=datetime(2026,10,2,12,tzinfo=UTC))
    assert not db.new and not db.dirty and not db.deleted


def mutation(event, action='edit', **kw):
    from uuid import uuid4
    from app.services.mini_app_service import event_version
    return {'action':action, 'eventId':str(event.id), 'version':event_version(event),
            'date':'2026-10-02', 'requestId':str(uuid4()),
            **({'kind':2,'feeling':2} if action == 'edit' else {}), **kw}


def test_edit_delete_middle_last_and_retries_preserve_bot_consistency(db):
    from app.services.mini_app_service import record_mark, MarkRejected
    from app.db.models import SessionUserState
    from app.services.q1_service import apply_plus
    now = datetime(2026,10,2,12,tzinfo=UTC)
    original = new_mark()
    ids = []
    for body in [original, new_mark(), new_mark()]:
        ids.append(int(record_mark(db,-100,10,body,now=now)['eventId'])); db.commit()
    first, middle, last = [db.get(PoopEvent,i) for i in ids]
    sid, occurred, source = first.session_id, first.occurred_at, first.source
    edit = mutation(first)
    assert not record_mark(db,-100,10,edit,now=now)['repeated']; db.commit()
    assert first.bristol == 6 and first.feeling == 'bad'
    assert (first.occurred_at,first.source) == (occurred,source)
    assert record_mark(db,-100,10,edit,now=now)['repeated']; db.commit()
    with pytest.raises(MarkRejected): record_mark(db,-100,10,{**edit,'kind':0},now=now)
    db.rollback()
    stale = mutation(first,version=edit['version'])
    with pytest.raises(MarkRejected): record_mark(db,-100,10,stale,now=now)
    db.rollback()
    # Delete the middle row, retaining identities/times of all remaining records.
    deletion = mutation(middle,'delete')
    record_mark(db,-100,10,deletion,now=now); db.commit()
    assert record_mark(db,-100,10,deletion,now=now)['repeated']; db.commit()
    assert db.get(PoopEvent,ids[1]) is None
    assert db.get(PoopEvent,ids[2]).event_n == 2
    assert db.get(SessionUserState,(sid,10)).poops_n == 2
    assert read_view(db,-100,10,None,now=now)['counts']['2026-10-02'] == 2
    record_mark(db,-100,10,mutation(db.get(PoopEvent,ids[2]),'delete'),now=now); db.commit()
    state = db.get(SessionUserState,(sid,10))
    assert state.bristol == 6 and state.feeling == 'bad'
    record_mark(db,-100,10,mutation(db.get(PoopEvent,ids[0]),'delete'),now=now); db.commit()
    assert state.poops_n == 0 and state.bristol is None and state.achievement_text is None
    assert '2026-10-02' not in read_view(db,-100,10,None,now=now)['counts']
    # A creation retry cannot resurrect a deleted row. Bot creation still works.
    assert record_mark(db,-100,10,original,now=now)['repeated']; db.commit()
    assert apply_plus(db,sid,10,-100)[0]; db.commit()
    assert db.get(SessionUserState,(sid,10)).poops_n == 1


def test_mutations_isolate_other_users_chats_and_ambiguous_history(db):
    from app.services.mini_app_service import record_mark, MarkRejected
    now = datetime(2026,10,2,12,tzinfo=UTC)
    for e in db.query(PoopEvent).all():
        # All fixture rows are either ambiguous, missing state, foreign, or future.
        body = mutation(e,'delete',date=str(db.get(DaySession,e.session_id).session_date))
        with pytest.raises(MarkRejected): record_mark(db,-100,10,body,now=now)
        db.rollback()
    assert db.query(PoopEvent).count() == 7


def test_closed_historical_edits_and_delete_refresh_streak_without_invented_time(db):
    from app.services.mini_app_service import record_mark
    from app.db.models import UserStreak, SessionUserState
    now = datetime(2026,10,2,12,tzinfo=UTC)
    event_id = int(record_mark(db,-100,10,new_mark(),now=now)['eventId']); db.commit()
    event = db.get(PoopEvent,event_id)
    sess = db.get(DaySession,event.session_id)
    sess.status = 'closed'
    event.source, event.occurred_at = 'restore', None
    db.commit()
    later = datetime(2026,10,3,12,tzinfo=UTC)
    record_mark(db,-100,10,mutation(event),now=later); db.commit()
    assert event.occurred_at is None and event.source == 'restore'
    record_mark(db,-100,10,mutation(event,'delete'),now=later); db.commit()
    assert db.get(SessionUserState,(sess.session_id,10)).poops_n == 0
    streak = db.get(UserStreak,(-100,10))
    assert streak.current_streak == 0 and streak.last_poop_date == date(2026,10,1)
    assert read_view(db,-100,10,None,now=later)['stats']['total'] == 4


@pytest.mark.parametrize('field,value', [('action','unknown'),('eventId','0'),('eventId',True),
    ('version','bad'),('date',[]),('kind',True),('feeling',4),('requestId','bad'),('extra',1)])
def test_invalid_mutations_never_write(db,field,value):
    from app.services.mini_app_service import record_mark
    event = db.query(PoopEvent).first()
    with pytest.raises(ValueError):
        record_mark(db,-100,10,mutation(event,**{field:value}),now=datetime(2026,10,2,12,tzinfo=UTC))
    assert not db.new and not db.dirty and not db.deleted


@pytest.mark.asyncio
async def test_http_edit_delete_recheck_membership_and_notify_changed_day():
    from app.services.mini_app_links import group_scope
    calls = []
    async def authorize(chat,user,*,writing):
        calls.append(writing); return True
    def writer(chat,user,body):
        assert (chat,user['id']) == (-100,10)
        return {'date':body['date']}
    def notifier(): calls.append('queued')
    headers = {'Origin':CONFIG.origin,'Authorization':'tma '+signed(start_param=group_scope(CONFIG.token,-100))}
    async with TestClient(TestServer(create_app(CONFIG,lambda *a: {},writer,authorize,notifier))) as client:
        for action in ['edit','delete']:
            response = await client.post('/events',headers=headers,json={
                'action':action,'eventId':'1','version':'a'*64,'date':'2026-10-02'})
            assert response.status == 200
    assert calls == [True,'queued',True,'queued']


def test_mutation_cannot_edit_valid_event_of_another_user_or_chat(db):
    from app.services.mini_app_service import record_mark, prepare_scope, MarkRejected
    now = datetime(2026,10,2,12,tzinfo=UTC)
    event_id = int(record_mark(db,-100,10,new_mark(),now=now)['eventId']); db.commit()
    db.add(ChatMember(chat_id=-100,user_id=20))
    prepare_scope(db,10,{'id':10}); db.commit()
    body = mutation(db.get(PoopEvent,event_id))
    for chat,user in [(-100,20),(10,10)]:
        with pytest.raises(MarkRejected): record_mark(db,chat,user,body,now=now)
        db.rollback()
    assert db.get(PoopEvent,event_id).bristol == 4
    for when in [datetime(2026,10,1,21,2,tzinfo=UTC), datetime(2026,10,2,20,56,tzinfo=UTC)]:
        with pytest.raises(MarkRejected): record_mark(db,-100,10,body,now=when)
        db.rollback()


def test_refresh_closed_q1_keeps_closed_controls_and_corrected_count(db,monkeypatch):
    from app.services import mini_app_message
    from app.services.time_service import SessionWindow
    from app.services.mini_app_service import record_mark
    from app.db.models import SessionMessage
    now = datetime(2026,10,2,12,tzinfo=UTC)
    event_id = int(record_mark(db,-100,10,new_mark(),now=now)['eventId']); db.commit()
    event = db.get(PoopEvent,event_id)
    sess = db.get(DaySession,event.session_id)
    sess.status = 'closed'
    db.add(SessionMessage(session_id=sess.session_id,kind='Q1',message_id=123)); db.commit()
    monkeypatch.setattr(mini_app_message,'get_session_window',lambda _:SessionWindow(date(2026,10,3),False,True))
    record_mark(db,-100,10,mutation(event,'delete'),now=datetime(2026,10,3,12,tzinfo=UTC)); db.commit()
    payload = mini_app_message.current_message(db,-100,10,date(2026,10,2))
    assert payload['message_id'] == 123 and '🔒 Сессия закрыта.' in payload['text']
    assert payload['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'q1:forgot'
    assert '02.10.26' in payload['text'] and '— —' in payload['text']
    monkeypatch.setattr(mini_app_message,'get_session_window',lambda _:SessionWindow(date(2026,10,4),False,True))
    assert mini_app_message.current_message(db,-100,10,date(2026,10,2))['reply_markup'] == {'inline_keyboard':[]}


def test_recovery_fences_late_writes_and_isolates_scopes(db):
    from app.services.mini_app_service import record_mark, recover_request, MarkRejected, prepare_scope
    now = datetime(2026,10,2,12,tzinfo=UTC)
    mark = new_mark()
    assert recover_request(db,-100,10,mark['requestId']) == {'status':'cancelled'}; db.commit()
    with pytest.raises(MarkRejected): record_mark(db,-100,10,mark,now=now)
    db.rollback()
    assert recover_request(db,-100,10,mark['requestId']) == {'status':'cancelled'}; db.commit()
    prepare_scope(db,10,{'id':10}); db.commit()
    with pytest.raises(PermissionError): recover_request(db,10,10,mark['requestId'])
    db.rollback()
    prepare_scope(db,-100,{'id':20}); db.commit()
    # The same UUID in another account says nothing about the first user's receipt.
    assert recover_request(db,-100,20,mark['requestId']) == {'status':'cancelled'}; db.commit()
    assert read_view(db,-100,10,None,now=now)['stats']['total'] == 3


def test_recovery_confirms_create_edit_delete_even_after_event_removed(db):
    from app.services.mini_app_service import record_mark, recover_request
    from app.db.models import MiniAppRequest
    now = datetime(2026,10,2,12,tzinfo=UTC)
    mark = new_mark()
    event_id = int(record_mark(db,-100,10,mark,now=now)['eventId']); db.commit()
    event = db.get(PoopEvent,event_id)
    edit = mutation(event)
    record_mark(db,-100,10,edit,now=now); db.commit()
    deletion = mutation(event,'delete')
    record_mark(db,-100,10,deletion,now=now); db.commit()
    for body in [mark,edit,deletion]:
        assert recover_request(db,-100,10,body['requestId']) == {'status':'completed'}; db.commit()
        receipt = db.get(MiniAppRequest,(10,body['requestId']))
        assert receipt.notify_pending and receipt.chat_id == -100
        assert receipt.notify_date == date(2026,10,2)
        assert receipt.event_id is None


@pytest.mark.asyncio
async def test_recovery_endpoint_rechecks_membership_and_rejects_overrides():
    from app.services.mini_app_links import group_scope
    from uuid import uuid4
    calls=[]
    async def authorize(chat,user,*,writing): calls.append(writing); return user == 10
    def recoverer(chat,user,request_id):
        calls.append((chat,user['id'],request_id)); return {'status':'completed'}
    body={'requestId':str(uuid4())}
    async with TestClient(TestServer(create_app(CONFIG,lambda *a: {},authorize=authorize,recoverer=recoverer))) as client:
        headers={'Origin':CONFIG.origin,'Authorization':'tma '+signed(start_param=group_scope(CONFIG.token,-100))}
        assert (await client.post('/recover',headers=headers,json=body)).status == 200
        assert calls == [True,(-100,10,body['requestId'])]
        assert (await client.post('/recover',headers=headers,json={**body,'user_id':20})).status == 400
        headers['Authorization']='tma '+signed(user=20,start_param=group_scope(CONFIG.token,-100))
        assert (await client.post('/recover',headers=headers,json=body)).status == 403


@pytest.mark.asyncio
async def test_bounded_queue_accepts_burst_without_unbounded_db_work():
    import asyncio, threading
    release=threading.Event()
    lock=threading.Lock()
    current=peak=0
    def reader(*args):
        nonlocal current,peak
        with lock: current+=1; peak=max(peak,current)
        release.wait(4)
        with lock: current-=1
        return {}
    async with TestClient(TestServer(create_app(CONFIG,reader))) as client:
        headers={'Origin':CONFIG.origin,'Authorization':'tma '+signed()}
        requests=[asyncio.ensure_future(client.post('/view',headers=headers,json={})) for _ in range(12)]
        try:
            for _ in range(100):
                if sum(t.done() for t in requests)>=2: break
                await asyncio.sleep(.01)
            assert sum(t.done() for t in requests)==2
            assert peak==2
        finally: release.set()
        statuses=[r.status for r in await asyncio.gather(*requests)]
        assert statuses.count(200)==10 and statuses.count(429)==2


@pytest.mark.asyncio
async def test_queue_timeout_does_not_start_rejected_work():
    import asyncio,threading
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def reader(*args): calls.append(1); entered.set(); release.wait(3); return {}
    async with TestClient(TestServer(create_app(CONFIG,reader,workers=1,queue_timeout=.05))) as client:
        headers={'Origin':CONFIG.origin,'Authorization':'tma '+signed()}
        first=asyncio.ensure_future(client.post('/view',headers=headers,json={}))
        await asyncio.to_thread(entered.wait,2)
        try: assert (await client.post('/view',headers=headers,json={})).status==429
        finally: release.set()
        assert (await first).status==200 and calls==[1]
