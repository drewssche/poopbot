"""Authenticated, chat-scoped Mini App API, independent from bot polling."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from aiohttp import ClientSession, ClientTimeout, web
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.mini_app_links import scope_chat
from app.services.mini_app_service import MarkRejected, prepare_scope, read_view, record_mark, recover_request

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    token: str
    origin: str

    def __post_init__(self):
        if not self.token or not self.origin.startswith('https://'):
            raise ValueError('Mini App configuration is incomplete')


def authenticate(raw: str, config: Config, *, now: float | None = None) -> dict:
    try:
        if not raw or len(raw) > 12000:
            raise ValueError()
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError()
        received = fields.pop('hash')
        check = '\n'.join(f'{key}={fields[key]}' for key in sorted(fields))
        secret = hmac.new(b'WebAppData', config.token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received, expected):
            raise ValueError()
        age = (time.time() if now is None else now) - int(fields['auth_date'])
        if not -30 <= age <= 3600:
            raise ValueError()
        user = json.loads(fields['user'])
        if not isinstance(user, dict) or type(user.get('id')) is not int or user['id'] <= 0 or user.get('is_bot'):
            raise ValueError()
        for key, limit in [('username', 64), ('first_name', 128), ('last_name', 128)]:
            if user.get(key) is not None and (not isinstance(user[key], str) or len(user[key]) > limit):
                raise ValueError()
        return {'user': user, 'start_param': fields.get('start_param'), 'chat_type': fields.get('chat_type')}
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise web.HTTPUnauthorized() from None


def resolve_chat(config, identity, supplied_scope=None):
    signed_scope = identity['start_param']
    if signed_scope and supplied_scope and signed_scope != supplied_scope:
        raise web.HTTPForbidden()
    scope = signed_scope or supplied_scope
    if scope:
        try:
            return scope_chat(config.token, scope)
        except ValueError:
            raise web.HTTPForbidden() from None
    if identity['chat_type'] in {'group', 'supergroup', 'channel'}:
        raise web.HTTPForbidden(reason='Открой ссылку, которую бот выдал по /app в этой группе.')
    return identity['user']['id']


class TelegramGateway:
    def __init__(self, token):
        self.token = token
        self.http = None
        self.members = {}

    async def call(self, method, payload):
        async with self.http.post('https://api.telegram.org/bot' + self.token + '/' + method, json=payload) as response:
            data = await response.json()
            if not data.get('ok'):
                if method == 'editMessageText':
                    description = data.get('description', '').lower()
                    if 'message is not modified' in description:
                        return True
                    if data.get('error_code') == 400 and any(reason in description for reason in (
                        'message to edit not found', 'message not found', 'message_id_invalid',
                    )):
                        # A deleted post must not prevent the other session posts
                        # from refreshing. Do not recreate it unsolicited.
                        logger.info('Skipping deleted session message during Mini App refresh')
                        return False
                raise RuntimeError('Telegram request failed')
            return data['result']

    async def authorize(self, chat, user, *, writing=False):
        if chat > 0:
            return chat == user
        key, now = (chat, user), time.monotonic()
        if not writing and self.members.get(key, 0) > now:
            return True
        try:
            member = await self.call('getChatMember', {'chat_id': chat, 'user_id': user})
        except Exception:
            self.members.pop(key, None)
            raise web.HTTPServiceUnavailable(reason='Не удалось проверить участие в группе. Попробуй позже.') from None
        allowed = member['status'] in {'creator', 'administrator', 'member'} or (member['status'] == 'restricted' and member.get('is_member') is True)
        if allowed:
            if len(self.members) >= 1000:
                self.members = {k: v for k, v in self.members.items() if v > now}
                if len(self.members) >= 1000:
                    self.members.clear()
            self.members[key] = now + 30
        else:
            self.members.pop(key, None)
        return allowed


def create_app(config: Config, reader, writer=None, authorize=None, notifier=None, recoverer=None,
               *, workers=2, capacity=10, queue_timeout=6) -> web.Application:
    recent = {}
    slots = asyncio.Semaphore(workers)
    tasks = set()

    @web.middleware
    async def responses(request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.json_response({'error': exc.reason}, status=exc.status)
        except Exception as exc:
            logger.error('Mini App request failed: %s', type(exc).__name__)
            response = web.json_response({'error': 'Temporarily unavailable'}, status=503)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return response

    async def handle(request):
        if request.headers.get('Origin') != config.origin:
            raise web.HTTPForbidden()
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('tma '):
            raise web.HTTPUnauthorized()
        identity = authenticate(auth[4:], config)
        if request.query_string or request.content_type != 'application/json':
            raise web.HTTPBadRequest()
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest() from None
        recovering = request.path.endswith('/recover')
        writing = request.path.endswith('/events')
        allowed = {'scope', 'requestId'} if recovering else {'scope', 'requestId', 'date', 'kind', 'feeling', 'action', 'eventId', 'version'} if writing else {'scope', 'month'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise web.HTTPBadRequest()
        scope = body.pop('scope', None)
        if scope is not None and not isinstance(scope, str):
            raise web.HTTPBadRequest()
        if not writing and not recovering and body.get('month') is not None and not isinstance(body['month'], str):
            raise web.HTTPBadRequest()
        chat = resolve_chat(config, identity, scope)
        user = identity['user']
        current = time.monotonic()
        for key in list(recent):
            recent[key] = [t for t in recent[key] if current - t < 60]
            if not recent[key]:
                del recent[key]
        bucket = recent.setdefault(user['id'], [])
        if len(tasks) >= capacity or len(bucket) >= 60 or sum(map(len, recent.values())) >= 600:
            raise web.HTTPTooManyRequests()
        bucket.append(current)
        async def operation():
            # The slot belongs to the operation, not the HTTP connection. A disconnected
            # request must not release it while its transaction is still running.
            try:
                await asyncio.wait_for(slots.acquire(), timeout=queue_timeout)
            except TimeoutError:
                raise web.HTTPTooManyRequests() from None
            try:
                if chat < 0 and (authorize is None or not await authorize(chat, user['id'], writing=writing or recovering)):
                    raise web.HTTPForbidden()
                if writing:
                    if writer is None:
                        raise web.HTTPServiceUnavailable()
                    result = await asyncio.to_thread(writer, chat, user, body)
                    if notifier:
                        # Non-blocking signal only. The committed receipt is the durable job.
                        try:
                            notifier()
                        except Exception as exc:
                            logger.error('Mini App notification signal failed: %s', type(exc).__name__)
                        result['messageUpdateQueued'] = True
                elif recovering:
                    if recoverer is None or set(body) != {'requestId'}:
                        raise web.HTTPBadRequest()
                    result = await asyncio.to_thread(recoverer, chat, user, body['requestId'])
                else:
                    result = await asyncio.to_thread(reader, chat, user, body.get('month'))
                return result
            except PermissionError:
                raise web.HTTPForbidden() from None
            except MarkRejected as exc:
                raise web.HTTPConflict(reason=str(exc)) from None
            except ValueError:
                raise web.HTTPBadRequest() from None
            finally:
                slots.release()

        task = asyncio.create_task(operation())
        tasks.add(task)
        def finished(done):
            tasks.discard(done)
            if not done.cancelled():
                done.exception()  # Consume errors even if the HTTP client disconnected.
        task.add_done_callback(finished)
        data = await asyncio.shield(task)
        return web.json_response(data)

    async def health(request):
        return web.json_response({'ok': True})

    app = web.Application(middlewares=[responses], client_max_size=1024)
    app.router.add_post('/view', handle)
    app.router.add_post('/events', handle)
    app.router.add_post('/recover', handle)
    app.router.add_get('/health', health)
    async def drain(_app):
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
    app.on_shutdown.append(drain)
    return app


def make_engine(url):
    return create_engine(url, pool_size=3, max_overflow=0, pool_timeout=5, pool_pre_ping=True,
                         hide_parameters=True, connect_args={'options': '-c statement_timeout=5000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000'})


def main():
    config = Config(os.environ['BOT_TOKEN'], os.environ['MINI_APP_ORIGIN'])
    engine = make_engine(os.environ['DATABASE_URL'])
    gateway = TelegramGateway(config.token)

    def reader(chat, user, month):
        with Session(engine) as db:
            prepare_scope(db, chat, user)
            db.commit()
            # Consistent projection after the small onboarding transaction.
            db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            return read_view(db, chat, user['id'], month)

    def writer(chat, user, body):
        with Session(engine) as db:
            prepare_scope(db, chat, user)
            result = record_mark(db, chat, user['id'], body)
            db.commit()
            return result

    def recoverer(chat, user, request_id):
        with Session(engine) as db:
            prepare_scope(db, chat, user)
            result = recover_request(db, chat, user['id'], request_id)
            db.commit()
            return result

    async def notifier(chat, user, changed_date=None):
        from app.services.mini_app_message import current_messages
        def read_message():
            with Session(engine) as db:
                payloads = current_messages(db, chat, user)
                if changed_date:
                    from datetime import date
                    historical = current_messages(db, chat, user, date.fromisoformat(changed_date))
                    known = {p['message_id'] for p in payloads}
                    payloads.extend(p for p in historical if p['message_id'] not in known)
                from app.services.q1_card_state import queue_photo_questions
                payloads = queue_photo_questions(db, payloads)
                db.commit()
                return payloads
        from app.services.mini_app_notifications import database_work
        payloads = await database_work(read_message)
        from app.services.mini_app_notifications import deliver_messages
        await deliver_messages(gateway, payloads)

    from app.services.mini_app_notifications import NotificationWorker
    notifications = NotificationWorker(engine, notifier)
    app = create_app(config, reader, writer, gateway.authorize, notifications.kick, recoverer)

    async def startup(_app):
        gateway.http = ClientSession(timeout=ClientTimeout(total=5))
        notifications.start()

    async def cleanup(_app):
        await notifications.stop()
        await gateway.http.close()
        engine.dispose()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    logging.basicConfig(level=logging.INFO)
    web.run_app(app, host='0.0.0.0', port=8080, access_log=None, print=None)


if __name__ == '__main__':
    main()
