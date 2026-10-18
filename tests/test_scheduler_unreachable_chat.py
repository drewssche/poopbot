import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Chat
from app.db.session import db_session
from app.services import scheduler_service as scheduler
from app.services.repo_service import upsert_chat


@pytest.mark.parametrize("startup", [False, True])
@pytest.mark.parametrize("message,disabled", [
    ("Bad Request: CHAT_WRITE_FORBIDDEN", True),
    ("Bad Request: message can't be edited", False),
])
def test_inaccessible_chat_is_skipped_on_next_pass(monkeypatch, startup, message, disabled):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    blocked_id, healthy_id = -100, -200
    with db_session(factory) as db:
        db.add_all([Chat(chat_id=blocked_id), Chat(chat_id=healthy_id)])

    async def process(bot, session_factory, chat_id):
        if chat_id == blocked_id:
            raise TelegramBadRequest(
                method=EditMessageText(chat_id=chat_id, message_id=1, text="closed"),
                message=message,
            )
        return False

    mock = AsyncMock(side_effect=process)
    monkeypatch.setattr(scheduler, "_recover_chat_q1_on_startup" if startup else "_process_chat", mock)
    run = scheduler.recover_missing_q1_on_startup if startup else scheduler._tick
    try:
        asyncio.run(run(None, factory, chat_throttle_sec=0))
        with db_session(factory) as db:
            assert db.get(Chat, blocked_id).is_enabled is not disabled
            assert db.get(Chat, healthy_id).is_enabled is True

        mock.reset_mock()
        asyncio.run(run(None, factory, chat_throttle_sec=0))
        called_ids = [call.args[2] for call in mock.await_args_list]
        assert healthy_id in called_ids
        assert (blocked_id not in called_ids) is disabled

        if disabled:
            # Fresh inbound activity retains the existing reactivation behavior.
            with db_session(factory) as db:
                upsert_chat(db, blocked_id)
            with db_session(factory) as db:
                assert db.get(Chat, blocked_id).is_enabled is True
    finally:
        engine.dispose()
