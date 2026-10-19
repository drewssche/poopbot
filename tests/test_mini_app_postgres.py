"""Optional integration tests: only an explicitly provided disposable local DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.db.base import Base
from app.db.models import Chat, User, PoopEvent, Session as DaySession, SessionUserState, MiniAppRequest
from app.services.mini_app_service import prepare_scope, record_mark, event_version, MarkRejected
from app.services.q1_service import apply_plus

URL = os.getenv('MINI_APP_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not URL, reason='Disposable local PostgreSQL not configured')


def test_parallel_app_retries_and_bot_marks():
    engine = create_engine(URL)
    # Isolate within a fresh schema, without touching any existing table.
    schema = 'mini_test_' + uuid4().hex
    with engine.begin() as c:
        c.exec_driver_sql('CREATE SCHEMA ' + schema)
    scoped = engine.execution_options(schema_translate_map={None:schema})
    try:
        Base.metadata.create_all(scoped)
        now = datetime(2026,10,19,12,tzinfo=UTC)
        with Session(scoped) as db:
            prepare_scope(db,10,{'id':10,'first_name':'Test'}); db.commit()
        mark = {'requestId':str(uuid4()),'date':'2026-10-19','kind':1,'feeling':0}
        def add(body):
            with Session(scoped) as db:
                result = record_mark(db,10,10,body,now=now)
                db.commit()
                return result
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(add,[mark]*4))
        assert len({r['eventId'] for r in results}) == 1
        assert sum(not r['repeated'] for r in results) == 1
        with Session(scoped) as db:
            sid = db.scalar(select(DaySession.session_id))
        def bot_add(_):
            with Session(scoped) as db:
                result = apply_plus(db,sid,10,10)
                db.commit()
                return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(add,{**mark,'requestId':str(uuid4())})
            b = pool.submit(bot_add,None)
            assert a.result()['eventId'] and b.result()[0]
        with Session(scoped) as db:
            assert db.get(SessionUserState,(sid,10)).poops_n == 3
            assert db.scalars(select(PoopEvent.event_n).order_by(PoopEvent.event_n)).all() == [1,2,3]
            assert len(db.scalars(select(MiniAppRequest)).all()) == 2
        with Session(scoped) as db:
            event = db.scalar(select(PoopEvent).order_by(PoopEvent.event_n))
            deletion = {'requestId':str(uuid4()), 'action':'delete', 'eventId':str(event.id),
                        'date':'2026-10-19', 'version':event_version(event)}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(add,[deletion]*4))
        assert sum(not r['repeated'] for r in results) == 1
        with Session(scoped) as db:
            assert db.get(SessionUserState,(sid,10)).poops_n == 2
            assert db.scalars(select(PoopEvent.event_n).order_by(PoopEvent.event_n)).all() == [1,2]
            event = db.scalar(select(PoopEvent).order_by(PoopEvent.event_n))
            edit = {'requestId':str(uuid4()), 'action':'edit', 'eventId':str(event.id),
                    'date':'2026-10-19', 'version':event_version(event), 'kind':3, 'feeling':2}
        # Two devices editing the same revision: exactly one succeeds.
        def edit_outcome(body):
            try:
                return add(body)
            except MarkRejected:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit_outcome,[edit,{**edit,'requestId':str(uuid4()),'kind':0}]))
        assert results.count('conflict') == 1
        with Session(scoped) as db:
            event = db.scalar(select(PoopEvent).order_by(PoopEvent.event_n))
            deletion = {'requestId':str(uuid4()), 'action':'delete', 'eventId':str(event.id),
                        'date':'2026-10-19', 'version':event_version(event)}
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(add,deletion)
            b = pool.submit(bot_add,None)
            assert not a.result()['repeated'] and b.result()[0]
        with Session(scoped) as db:
            assert db.get(SessionUserState,(sid,10)).poops_n == 2
            assert db.scalars(select(PoopEvent.event_n).order_by(PoopEvent.event_n)).all() == [1,2]
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP SCHEMA ' + schema + ' CASCADE')
        engine.dispose()
