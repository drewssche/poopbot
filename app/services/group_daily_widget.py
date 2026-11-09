"""Native daily questions for personal chats and the group pilot. All counts use the event ledger."""

import base64
import hashlib
import json
import re
import zlib
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, select, update

from app.db.models import (
    AppSetting,
    Chat,
    MiniAppRequest,
    PoopEvent,
    Session as DaySession,
    SessionMessage,
    User,
    WeeklyReport,
)
from app.db.session import db_session
from app.services.daily_widget_demo import (
    ASSETS,
    ROOT,
    DAYS,
    MONTHS,
    AnswerWidget,
    SendWidget,
)
from app.services.mini_app_notifications import database_work
from app.services.mini_app_service import change_mark, event_version, MarkRejected
from app.services.poop_event_service import list_origin_events
from app.services.q1_service import apply_minus, apply_plus
from app.services.rate_limit_service import check_rate_limit
from app.services.repo_service import ensure_chat_member, upsert_user
from app.services.restore_period_service import lock_restore_chat, mark_forgotten_day
from app.services.time_service import get_session_window

PILOT = -1001739559442
FLAG = "group_daily_widget_pilot"
PRIVATE_FLAG = "private_native_widgets"
PREFIX = "widget:q1:"


def enabled(db, chat_id):
    if chat_id > 0:
        from app.services.app_setting_service import get_bool_setting
        return get_bool_setting(db, PRIVATE_FLAG)
    if chat_id != PILOT:
        return False
    setting = db.get(AppSetting, FLAG)
    return setting is not None and setting.value == str(chat_id)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render(day, private=False):
    start = day - timedelta(days=day.weekday())
    # Keep the original silhouette and trim subpixel precision for Telegram's size limit.
    icon = (ROOT / "web/demo/assets/poop-day.svg").read_text().strip()
    icon = re.sub(r"\d+\.\d+", lambda m: str(round(float(m[0]))), icon)
    cells = "".join(
        f'<div class="cell{" today" if i == day.weekday() else ""}">{name}'
        f'<span class="dot off" id="d{i}">{icon}<b>{(start + timedelta(days=i)).day}</b>'
        "<i></i></span></div>"
        for i, name in enumerate(DAYS)
    )
    logo = base64.b64encode((ASSETS / "logo-110.png").read_bytes()).decode()
    css = (
        (ASSETS / "daily.css").read_text()
        + "#panel{text-align:center}#panel button{width:auto;margin:3px;padding:4px 8px;background:#f4f0ec;font-size:13px}#plus{font-size:18px}"
    )
    html = (
        "<style>"
        + css
        + "</style>"
        + (ASSETS / "daily-live.html")
        .read_text()
        .replace("__LOGO__", "data:image/png;base64," + logo)
        .replace("__WEEK__", cells)
        .replace("__SCOPE__", "Личная сессия" if private else "Наш чат")
        .replace("__HINT__", "Твои отметки" if private else "Отметки этого чата")
        .replace("__DETAILS__", '<details><summary>За день</summary><div id="history" style="white-space:pre-line"></div><button id="restore" style="width:auto;display:none">Вернуть стрик</button></details>' if private else "")
        .replace("__PRIVATE__", "true" if private else "false")
        .replace("__DATE__", f"{day.day} {MONTHS[day.month - 1]}")
        .replace("__INDEX__", str(day.weekday()))
    )
    if len(html.encode()) > 32768 or len(zlib.compress(html.encode(), 9)) > 4010:
        raise ValueError("Widget size budget exceeded")
    return html


def snapshot(db, sess, revision):
    day = sess.session_date - timedelta(days=sess.session_date.weekday())
    counts = dict(
        db.execute(
            select(DaySession.session_date, func.count(PoopEvent.id))
            .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
            .where(
                DaySession.chat_id == sess.chat_id,
                PoopEvent.origin_chat_id == sess.chat_id,
                *([PoopEvent.user_id == sess.chat_id] if sess.chat_id > 0 else []),
                DaySession.session_date.between(day, day + timedelta(days=6)),
            )
            .group_by(DaySession.session_date)
        ).all()
    )
    expiry = datetime.combine(
        sess.session_date + timedelta(days=2),
        time.min,
        tzinfo=ZoneInfo(db.get(Chat, sess.chat_id).timezone),
    )
    data = dict(
        r=revision,
        c=int(sess.status == "closed"),
        x=int(expiry.timestamp()),
        d=[counts.get(day + timedelta(days=i), 0) for i in range(7)],
    )
    if sess.chat_id > 0:
        from app.services.q1_service import render_q1_private, should_show_restore_streak_button
        summary = render_q1_private(db, sess.chat_id, sess.session_id, sess.chat_id, sess.session_date, closed=bool(data["c"]))
        data["h"] = "\n".join(summary.splitlines()[4:])
        data["restore"] = not data["c"] and should_show_restore_streak_button(db, chat_id=sess.chat_id,
            session_date=sess.session_date, viewer_user_id=sess.chat_id, is_private_chat=True)
    return data


async def send(bot, db, sess):
    day = sess.session_date
    start = day - timedelta(days=day.weekday())
    # /start may replace a question deleted in Telegram. Retire its publisher as well.
    db.execute(
        update(WeeklyReport)
        .where(
            WeeklyReport.q1_session_id == sess.session_id,
            WeeklyReport.delivery_key.startswith(PREFIX),
            WeeklyReport.disabled.is_(False),
        )
        .values(disabled=True)
    )
    # The worker re-reads after commit, catching changes racing the initial projection.
    row = WeeklyReport(
        delivery_key=f"{PREFIX}{sess.session_id}:{uuid4()}",
        source_chat_id=sess.chat_id,
        destination_chat_id=sess.chat_id,
        q1_session_id=sess.session_id,
        report_period="week",
        previous_period_start=start,
        period_start=start,
        period_end=start + timedelta(days=6),
        anchor_date=day,
        chat_title="Личные отметки" if sess.chat_id > 0 else "Отметки этого чата",
        revision=1,
        delivered_revision=0,
    )
    db.add(row)
    db.flush()
    sent = await bot(
        SendWidget(
            chat_id=sess.chat_id,
            html=render(day, private=sess.chat_id > 0),
            chat_data=encode(snapshot(db, sess, 1)),
        )
    )
    row.photo_message_id = (
        sent.message_id
    )  # Existing outbox message address, also used by Mini App refresh.
    return sent


def mutate(db, q):
    chat = q.get("chat") or {}
    actor = q.get("from") or {}
    uid = actor.get("id")
    reject = lambda msg: {"t": msg}
    chat_id = chat.get("id")
    if type(chat_id) is not int or not uid or actor.get("is_bot"):
        return reject("Недоступно для этого чата")
    if chat_id > 0:
        if chat.get("type") != "private" or chat_id != uid:
            return reject("Недоступно для этого чата")
    elif chat.get("type") not in ("group", "supergroup") or not enabled(db, chat_id):
        return reject("Недоступно для этого чата")
    match = re.fullmatch(
        r"q:([+fs-]|e([1-9]\d{0,9})\.([a-f0-9]{16})\.([0-3])([0-2])):([a-f0-9]{16})",
        str(q.get("data", "")),
    )
    if not match:
        return reject("Неизвестное действие")
    row = db.scalar(
        select(WeeklyReport)
        .join(
            SessionMessage,
            (SessionMessage.session_id == WeeklyReport.q1_session_id)
            & (SessionMessage.kind == "Q1")
            & (SessionMessage.message_id == WeeklyReport.photo_message_id),
        )
        .where(
            WeeklyReport.source_chat_id == chat_id,
            WeeklyReport.photo_message_id == q.get("message_id"),
            WeeklyReport.delivery_key.startswith(PREFIX),
            WeeklyReport.disabled.is_(False),
        )
    )
    if row is None:
        return reject("Этот вопрос уже неактуален")
    op, eid, version, kind, feeling, nonce = match.groups()
    if op == "f":
        lock_restore_chat(db, chat_id)
    # Two simultaneous first taps must converge on the same user lock as the Mini App.
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        db.execute(
            insert(User)
            .values(user_id=uid)
            .on_conflict_do_nothing(index_elements=[User.user_id])
        )
    db.scalar(select(User).where(User.user_id == uid).with_for_update())
    upsert_user(
        db, uid, actor.get("username"), actor.get("first_name"), actor.get("last_name")
    )
    db.flush()
    sess = db.get(DaySession, row.q1_session_id, with_for_update=True)
    window = get_session_window(db.get(Chat, chat_id).timezone)
    rid = str(
        uuid5(
            NAMESPACE_URL,
            f"poopcheck:widget:{chat_id}:{row.photo_message_id}:{uid}:{nonce}",
        )
    )
    digest = hashlib.sha256(op.encode()).hexdigest()
    receipt = db.get(MiniAppRequest, (uid, rid))

    def result(msg, event=None):
        db.flush()
        db.refresh(row)
        a = {"t": msg, "s": snapshot(db, sess, row.revision)}
        if event and op == "+":
            a.update(e=event.id, v=event_version(event)[:16])
        return a

    if receipt:
        if receipt.payload_hash != digest:
            return reject("Нажатие уже обработано")
        return result(
            "Нажатие уже обработано",
            db.get(PoopEvent, receipt.event_id) if receipt.event_id else None,
        )
    event = None
    if op != "f" and (
        sess.status != "active"
        or window.is_blocked_window
        or sess.session_date != window.session_date
    ):
        return result("Сессия закрыта")
    if op == "f" and sess.status != "closed":
        return result("Сессия ещё не закрыта")
    if op in "+-" and not check_rate_limit(
        db, chat_id=chat_id, user_id=uid, scope="Q1", cooldown_seconds=2
    ):
        return result("Слишком быстро. Попробуй через пару секунд")
    ensure_chat_member(db, chat_id, uid)
    if op == "f":
        _, msg = mark_forgotten_day(
            db, sess=sess, user_id=uid, today=window.session_date
        )
    elif op == "s":
        from app.services.q1_service import restore_streak_for_user, should_show_restore_streak_button
        if chat_id <= 0 or not should_show_restore_streak_button(db, chat_id=chat_id, session_date=sess.session_date,
                                                                 viewer_user_id=uid, is_private_chat=True):
            return result("Восстановление недоступно")
        _, msg = restore_streak_for_user(db, chat_id, uid, sess.session_date)
    elif eid:
        event = db.scalar(
            select(PoopEvent).where(
                PoopEvent.id == int(eid),
                PoopEvent.user_id == uid,
                PoopEvent.origin_chat_id == chat_id,
                PoopEvent.session_id == sess.session_id,
            )
        )
        if not event or event_version(event)[:16] != version:
            return result("Отметка изменилась. Открой /app")
        try:
            change_mark(
                db,
                chat_id,
                uid,
                dict(
                    action="edit",
                    requestId=str(uuid5(NAMESPACE_URL, "edit:" + rid)),
                    eventId=eid,
                    version=event_version(event),
                    date=sess.session_date.isoformat(),
                    kind=int(kind),
                    feeling=int(feeling),
                ),
            )
        except MarkRejected as exc:
            return result(str(exc))
        msg = "Уточнения сохранены"
    else:
        changed, msg = (
            apply_plus(db, sess.session_id, uid, chat_id)
            if op == "+"
            else apply_minus(db, sess.session_id, uid)
        )
        db.flush()
        events = list_origin_events(db, sess.session_id, uid, chat_id)
        if changed:
            msg = f"Твоих отметок: {len(events)}"
            if op == "+" and events:
                event = events[-1]
    db.add(
        MiniAppRequest(
            user_id=uid,
            request_id=rid,
            payload_hash=digest,
            event_id=event.id if event else None,
            chat_id=chat_id,
            notify_date=sess.session_date,
            notify_pending=False,
        )
    )
    return result(msg, event)


class GroupQueries:
    def __init__(self, factory):
        self.factory = factory

    def handle(self, q):
        with db_session(self.factory) as db:
            return mutate(db, q)

    async def __call__(self, handler, event, data):
        q = (event.model_extra or {}).get("widget_query") or {}
        if not str(q.get("data", "")).startswith("q:"):
            return await handler(event, data)
        reply = await database_work(self.handle, q)
        return await data["bot"](
            AnswerWidget(widget_query_id=q["id"], data=encode(reply))
        )
