import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers import callbacks_fallback


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["command", "callback"])
async def test_unknown_input_is_not_logged_and_still_receives_reply(caplog, kind):
    caplog.set_level(logging.WARNING, logger=callbacks_fallback.__name__)
    secret = "PRIVATE-CONTENT-NOT-FOR-LOGS"
    user = SimpleNamespace(id=123)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-456),
        from_user=user,
        text=f"/{secret} token={secret}\nforged log line",
        answer=AsyncMock(),
    )
    callback = SimpleNamespace(
        message=message, from_user=user, data=f"unknown:{secret}", answer=AsyncMock()
    )

    if kind == "command":
        await callbacks_fallback.unknown_command(message)
        message.answer.assert_awaited_once_with("Не понял команду. Открой /help")
    else:
        await callbacks_fallback.unknown_callback(callback)
        callback.answer.assert_awaited_once_with(
            "Кнопка устарела. Обнови /start или /stats", show_alert=False
        )

    records = [r for r in caplog.records if r.name == callbacks_fallback.__name__]
    assert len(records) == 1
    assert records[0].getMessage() == f"Unhandled {kind} chat_id=-456 user_id=123"
    assert records[0].args == (-456, 123)
    assert secret not in caplog.text
    assert "forged log line" not in caplog.text
