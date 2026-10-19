"""Owner-only read API. No Telegram polling, jobs or mutation routes."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from aiohttp import web
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.mini_app_service import read_view

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    token: str
    owner_id: int
    chat_id: int
    origin: str

    def __post_init__(self):
        if not self.token or self.owner_id <= 0 or self.chat_id >= 0 or not self.origin.startswith("https://"):
            raise ValueError("Mini App configuration is incomplete")


def authenticate(raw: str, config: Config, *, now: float | None = None) -> int:
    """Reject ambiguous input, invalid signatures and expired Telegram launches."""
    try:
        if not raw or len(raw) > 12000:
            raise ValueError()
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError()
        received = fields.pop("hash")
        check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
        secret = hmac.new(b"WebAppData", config.token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received, expected):
            raise ValueError()
        age = (time.time() if now is None else now) - int(fields["auth_date"])
        if not -30 <= age <= 3600:
            raise ValueError()
        user = json.loads(fields["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or user.get("is_bot"):
            raise ValueError()
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise web.HTTPUnauthorized(text="Open the app again from Telegram") from None
    if user["id"] != config.owner_id:
        raise web.HTTPForbidden(text="Pilot access is restricted")
    return user["id"]


def create_app(config: Config, reader) -> web.Application:
    # Only the authorized owner can reach DB work. One in-flight read, small burst budget.
    recent = []
    busy = False

    @web.middleware
    async def responses(request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.json_response({"error": exc.reason}, status=exc.status)
        except Exception as exc:
            # Never log initData, query strings, database parameters or user records.
            logger.error("Mini App request failed: %s", type(exc).__name__)
            response = web.json_response({"error": "Temporarily unavailable"}, status=503)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        return response

    async def view(request):
        nonlocal busy
        if request.headers.get("Origin") != config.origin:
            raise web.HTTPForbidden()
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("tma "):
            raise web.HTTPUnauthorized()
        user_id = authenticate(auth[4:], config)
        if request.query_string or request.content_type != "application/json":
            raise web.HTTPBadRequest()
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest() from None
        if not isinstance(body, dict) or set(body) - {"month"}:
            raise web.HTTPBadRequest()
        month = body.get("month")
        if month is not None and not isinstance(month, str):
            raise web.HTTPBadRequest()
        current = time.monotonic()
        recent[:] = [t for t in recent if current - t < 60]
        if busy or len(recent) >= 60:
            raise web.HTTPTooManyRequests()
        recent.append(current)
        busy = True
        try:
            data = await asyncio.to_thread(reader, config.chat_id, user_id, month)
        except PermissionError:
            raise web.HTTPForbidden() from None
        except ValueError:
            raise web.HTTPBadRequest() from None
        finally:
            busy = False
        return web.json_response(data)

    async def health(request):
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[responses], client_max_size=1024)
    app.router.add_post("/view", view)
    app.router.add_get("/health", health)
    return app


def make_engine(url):
    return create_engine(
        url, pool_size=1, max_overflow=0, pool_timeout=5, pool_pre_ping=True,
        isolation_level="REPEATABLE READ", hide_parameters=True,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=5000 -c idle_in_transaction_session_timeout=10000"},
    )


def main():
    config = Config(os.environ["BOT_TOKEN"], int(os.environ["BOT_OWNER_ID"]),
                    int(os.environ["MINI_APP_CHAT_ID"]), os.environ["MINI_APP_ORIGIN"])
    engine = make_engine(os.environ["DATABASE_URL"])

    def reader(chat_id, user_id, month):
        with Session(engine) as db:
            # Session closes with rollback. DB connections also enforce read-only transactions.
            return read_view(db, chat_id, user_id, month)

    app = create_app(config, reader)

    async def cleanup(_app):
        engine.dispose()

    app.on_cleanup.append(cleanup)
    logging.basicConfig(level=logging.INFO)
    web.run_app(app, host="0.0.0.0", port=8080, access_log=None, print=None)


if __name__ == "__main__":
    main()
