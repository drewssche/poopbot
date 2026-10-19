import asyncio
from datetime import date
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.db.base import Base
from app.db.models import MiniAppRequest, User
from app.services.mini_app_notifications import NotificationWorker


@pytest.mark.asyncio
async def test_durable_notifications_coalesce_restart_retry_and_sleep(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'jobs.db'))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(user_id=10));db.flush()
        for _ in range(3):
            db.add(MiniAppRequest(user_id=10,request_id=str(uuid4()),chat_id=-100,
                payload_hash='1'*64,notify_date=date(2026,10,2),notify_pending=True))
        db.commit()
    calls=[]
    async def failed(*args): calls.append(args);raise RuntimeError('offline')
    worker=NotificationWorker(engine,failed)
    worker.start()
    try:
        for _ in range(100):
            await asyncio.sleep(.01)
            with Session(engine) as db:
                rows=db.scalars(select(MiniAppRequest)).all()
                if all(r.notify_attempts==1 for r in rows):break
        assert len(calls)==1 and all(r.notify_pending and r.notify_after for r in rows)
    finally: await worker.stop()
    with Session(engine) as db:
        for r in db.scalars(select(MiniAppRequest)):r.notify_after=None
        db.commit()
    async def success(*args):calls.append(args)
    restarted=NotificationWorker(engine,success)
    reads=[]
    original=restarted.batch
    def counted():reads.append(1);return original()
    restarted.batch=counted
    restarted.start()
    try:
        for _ in range(100):
            await asyncio.sleep(.01)
            with Session(engine) as db:
                rows=db.scalars(select(MiniAppRequest)).all()
                if all(not r.notify_pending for r in rows):break
        assert len(calls)==2 and all(r.notify_attempts==2 for r in rows)
        await asyncio.sleep(.05)
        count=len(reads)
        await asyncio.sleep(.08)
        assert len(reads)==count  # No idle DB polling.
    finally: await restarted.stop();engine.dispose()


def test_notification_retries_stop_after_five_attempts(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'jobs.db'))
    Base.metadata.create_all(engine)
    rid=str(uuid4())
    with Session(engine) as db:
        db.add(User(user_id=10));db.flush()
        db.add(MiniAppRequest(user_id=10,request_id=rid,chat_id=10,payload_hash='1'*64,notify_pending=True))
        db.commit()
    worker=NotificationWorker(engine,None)
    worker.finish([(10,rid,4)],False)
    with Session(engine) as db:
        receipt=db.get(MiniAppRequest,(10,rid))
        assert not receipt.notify_pending and receipt.notify_attempts==5
    engine.dispose()


@pytest.mark.asyncio
async def test_delivery_attempts_every_message_and_retries_partial_failure():
    from app.services.mini_app_notifications import deliver_messages
    class Gateway:
        def __init__(self):self.calls=[]
        async def call(self,method,payload):
            assert method == 'editMessageText'
            self.calls.append(payload['message_id'])
            if payload['message_id'] == 2:raise RuntimeError('temporary failure')
    gateway=Gateway()
    with pytest.raises(RuntimeError, match='incomplete'):
        await deliver_messages(gateway,[{'message_id':i} for i in [1,2,3]])
    assert gateway.calls == [1,2,3]


@pytest.mark.asyncio
async def test_deleted_message_is_terminal_but_other_telegram_errors_retry():
    from app.mini_app import TelegramGateway
    class Response:
        def __init__(self,data):self.data=data
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def json(self):return self.data
    class HTTP:
        def post(self,*args,**kwargs):return Response(self.data)
    gateway=TelegramGateway('fake');gateway.http=HTTP()
    for description in ['Bad Request: message to edit not found','Bad Request: MESSAGE_ID_INVALID']:
        gateway.http.data={'ok':False,'error_code':400,'description':description}
        assert await gateway.call('editMessageText',{}) is False
    gateway.http.data={'ok':False,'error_code':400,'description':'message is not modified'}
    assert await gateway.call('editMessageText',{}) is True
    for code in [403,429,500]:
        gateway.http.data={'ok':False,'error_code':code,'description':'temporary error'}
        with pytest.raises(RuntimeError):await gateway.call('editMessageText',{})
