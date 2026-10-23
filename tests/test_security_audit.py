import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.core.security_audit import denied, configure_security_audit, logger
from app.mini_app import create_app
from test_mini_app import CONFIG, signed


def records(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name == 'app.security']


@pytest.mark.asyncio
async def test_api_denials_log_reason_and_only_verified_identity(caplog):
    caplog.set_level(logging.WARNING, logger='app.security')
    reader = lambda *args: {'ok': True}
    secret = 'SECRET-NOT-FOR-LOGS'
    rid = 'a' * 32
    async with TestClient(TestServer(create_app(CONFIG, reader))) as client:
        cases = [
            ({'Origin': CONFIG.origin}, 401, 'authentication_missing', None),
            ({'Origin': CONFIG.origin, 'Authorization': 'tma '+secret}, 401, 'authentication_invalid', None),
            ({'Origin': secret, 'Authorization': 'tma '+signed()}, 403, 'origin_rejected', None),
            ({'Origin': CONFIG.origin, 'Authorization': 'tma '+signed()}, 400, 'invalid_request', 10),
        ]
        for headers, status, reason, actor in cases:
            response = await client.post('/view?'+secret, json={'secret':secret},
                headers={**headers, 'X-PoopCheck-Request-ID':rid})
            assert response.status == status
            event = records(caplog)[-1]
            assert event['reason'] == reason and event['status'] == status
            assert event.get('actor_id') == actor and event['request_id'] == rid
        before = len(records(caplog))
        response = await client.post('/view', json={}, headers={'Origin':CONFIG.origin, 'Authorization':'tma '+signed()})
        assert response.status == 200 and len(records(caplog)) == before
        response = await client.post('/'+secret, json={}, headers={'X-PoopCheck-Request-ID':secret})
        assert response.status == 404
        assert records(caplog)[-1]['action'] == 'unknown_route'
        assert 'request_id' not in records(caplog)[-1]
    assert secret not in caplog.text and CONFIG.token not in caplog.text and 'auth_date' not in caplog.text


@pytest.mark.asyncio
async def test_verified_user_membership_denial_is_recorded(caplog):
    from app.services.mini_app_links import group_scope
    scope = group_scope(CONFIG.token, -100)
    async with TestClient(TestServer(create_app(CONFIG, lambda *a: {}, authorize=AsyncMock(return_value=False)))) as client:
        response = await client.post('/view', json={}, headers={
            'Origin':CONFIG.origin, 'Authorization':'tma '+signed(start_param=scope)})
        assert response.status == 403
    assert records(caplog)[-1]['reason'] == 'membership_required'
    assert records(caplog)[-1]['actor_id'] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['debug', 'streak', 'debug_callback', 'streakadmin'])
async def test_closed_commands_log_denial_without_message_or_callback_contents(monkeypatch, caplog, kind):
    from app.bot.handlers import commands, callbacks_debug, callbacks_streak_admin
    monkeypatch.setattr('app.core.config.load_settings', lambda: SimpleNamespace(bot_owner_id=99))
    monkeypatch.setattr(callbacks_streak_admin, 'load_settings', lambda: SimpleNamespace(bot_owner_id=99))
    message = SimpleNamespace(chat=SimpleNamespace(id=10, type='private'), from_user=SimpleNamespace(id=10),
                              text='/debug SECRET-NOT-FOR-LOGS', answer=AsyncMock())
    cb = SimpleNamespace(message=message, from_user=message.from_user, data='debug:send:SECRET-NOT-FOR-LOGS', answer=AsyncMock())
    handlers = {'debug':(callbacks_debug.debug_cmd,message), 'streak':(commands.streak_admin_cmd,message),
                'debug_callback':(callbacks_debug.debug_callbacks,cb), 'streakadmin':(callbacks_streak_admin.streak_admin_callbacks,cb)}
    handler, event = handlers[kind]
    await handler(event)
    row = records(caplog)[-1]
    assert row['surface'] == 'telegram' and row['actor_id'] == 10 and row['action'] == kind
    assert 'SECRET-NOT-FOR-LOGS' not in caplog.text
    message.answer.assert_not_awaited()


def test_audit_file_persists_json_and_has_bounded_rotation(tmp_path, monkeypatch):
    path = tmp_path / 'security.jsonl'
    monkeypatch.setenv('SECURITY_AUDIT_LOG', str(path))
    previous = list(logger.handlers)
    logger.handlers.clear()
    try:
        configure_security_audit()
        configure_security_audit()
        assert len(logger.handlers) == 1
        handler = logger.handlers[0]
        assert handler.maxBytes == 2*1024*1024 and handler.backupCount == 5
        denied('telegram', 'debug', 'owner_required', actor_id=10, chat_id=10)
        row = json.loads(path.read_text())
        assert row['time'].endswith('+00:00') and row['actor_id'] == 10
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = previous
