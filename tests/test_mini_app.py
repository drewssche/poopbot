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

CONFIG = Config('test-token-not-real', 10, -100, 'https://example.test')


def signed(user=10, timestamp=None, **extra):
    fields = {'user': json.dumps({'id': user, 'first_name': 'Test'}),
              'auth_date': str(int(time.time()) if timestamp is None else timestamp), **extra}
    secret = hmac.new(b'WebAppData', CONFIG.token.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, '\n'.join(f'{k}={fields[k]}' for k in sorted(fields)).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_authentication_requires_valid_signed_recent_owner():
    assert authenticate(signed(timestamp=10000), CONFIG, now=10001) == 10
    for raw in ['', 'hash=fake', signed(timestamp=10000)+'&auth_date=10000',
                signed(timestamp=10000).replace('Test', 'Other'), signed(timestamp=1),
                signed(timestamp=11000), signed(user=True, timestamp=10000)]:
        with pytest.raises(web.HTTPUnauthorized):
            authenticate(raw, CONFIG, now=10001)
    with pytest.raises(web.HTTPForbidden):
        authenticate(signed(user=20, timestamp=10000), CONFIG, now=10001)


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
async def test_http_rejects_untrusted_requests_before_reader_and_has_no_write_route():
    calls = []
    def reader(chat,user,month):
        calls.append((chat,user,month)); return {'readOnly': True}
    async with TestClient(TestServer(create_app(CONFIG,reader))) as client:
        headers = {'Origin':CONFIG.origin, 'Authorization':'tma '+signed()}
        for status, altered, body in [
            (401, {'Origin':CONFIG.origin}, {}),
            (403, {**headers,'Origin':'https://evil.test'}, {}),
            (403, {**headers,'Authorization':'tma '+signed(user=20)}, {}),
            (401, {**headers,'Authorization':'tma '+signed(timestamp=1)}, {}),
            (400, headers, {'user_id':20}), (400, headers, {'chat_id':-200}),
            (400, headers, {'month':[]}), (400, headers, []),
        ]:
            response = await client.post('/view',headers=altered,json=body)
            assert response.status == status
            assert response.headers['Cache-Control'] == 'no-store'
        assert calls == []
        response = await client.post('/view',headers=headers,json={'month':'2026-10'})
        assert response.status == 200 and (await response.json())['readOnly']
        assert calls == [(-100,10,'2026-10')]
        assert (await client.post('/view?user_id=20',headers=headers,json={})).status == 400
        assert (await client.post('/view',headers=headers,data='{')).status == 400
        assert (await client.post('/view',headers=headers,json={'month':'x'*2000})).status == 413
        assert (await client.post('/events',headers=headers,json={})).status == 404
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
        assert (chat,user) == (-100,10)
        entered.set()
        release.wait(3)
        return {'readOnly':True}
    async with TestClient(TestServer(create_app(CONFIG,reader))) as client:
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
