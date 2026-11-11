"""Native daily questions for personal chats and the group pilot. All counts use the event ledger."""

import hashlib
import json
import re
import zlib
from functools import lru_cache
from xml.etree import ElementTree
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
    DAYS,
    AnswerWidget,
    SendWidget,
)
from app.services import private_widget_history as history
from app.services.mini_app_notifications import database_work
from app.services.mini_app_service import change_mark, event_version, record_mark, MarkRejected
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
JOURNAL_PREFIX = PREFIX + "journal:"


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


@lru_cache(maxsize=1)
def private_brand():
    # Split static artwork across Telegram's HTML and public-data budgets.
    logo = (ASSETS / "brand.svg").read_text().strip()
    path = ElementTree.fromstring(logo).findall("{*}path")[-1].attrib["d"]
    art = [path]
    for name in ("day", "edit"):
        icon = ElementTree.parse(ASSETS / f"{name}.svg")
        art.append(icon.find(".//{*}path").attrib["d"])
    return logo.replace(f'd="{path}"', 'id="wordmark"'), encode(art)


def source_template(private):
    logo = private_brand()[0] if private else (ASSETS / "brand.svg").read_text().strip()
    return (
        "<style>"
        + (ASSETS / ("daily-private.css" if private else "daily-live.css")).read_text()
        + "</style>"
        + (ASSETS / "daily-live.html")
        .read_text()
        .replace('<div class="week">', '<div id="week">' if private else '<div class="week">')
        .replace("__VIEW__", (ASSETS / ("daily-private.js" if private else "daily-group.js")).read_text())
        .replace("__RETRY__", "control(panel,'Проверить отметку','↻',()=>act('',true))" if private
                 else "button('Проверить отметку',()=>act('',true))")
        .replace("__MONTH__", '<button id="month"></button>' if private else "__DATE__")
        .replace("__FORM__", (ASSETS / ("daily-private.html" if private else "daily-group.html")).read_text())
        .replace("__LOGO__", logo)
        .replace("__MENU__", '<button id="menu" aria-label="Меню">☰</button>' if private else "")
        .replace("__SCOPE__", "Личная сессия" if private else "Наш чат")
    )


@lru_cache(maxsize=32)
def render(day, private=False):
    start = day - timedelta(days=day.weekday())
    tag = "button" if private else "div"
    cells = "".join(
        f'<{tag} id="w{i}" class="cell{" today" if i == day.weekday() else ""}">{name}'
        f'<span class="day" id="d{i}" data-day="{(start + timedelta(days=i)).day}">{(start + timedelta(days=i)).day}</span></{tag}>'
        for i, name in enumerate(DAYS)
    )
    template = (ASSETS / "daily-private.min.html").read_text() if private else source_template(False)
    html = (template.replace("__WEEK__", "" if private else cells)
            .replace("__STAMP__", day.strftime("%Y%m%d"))
            .replace("__DATE__", f"{history.MONTHS[day.month - 1]} {day.year}")
            .replace("__INDEX__", str(day.weekday())))
    if len(html.encode()) > 32768 or len(zlib.compress(html.encode(), 9)) > 4010:
        raise ValueError("Widget size budget exceeded")
    return html


def fit_widget_history(payload, state):
    """Telegram allows 1024 UTF-8 bytes for each state or request answer."""
    if "h" not in state or len(encode(payload).encode()) <= 1024:
        return
    heading, _, events = state["h"].partition("- #")
    entries = ("- #" + events).splitlines() if events else []
    while entries and len(encode(payload).encode()) > 1024:
        entries.pop(0)
        state["h"] = heading + "…\n" + "\n".join(entries)


def confirmed_view(db, sess, ack):
    # Rebuild history from the ledger so a later refresh cannot expose a deleted entry.
    result = dict(ack)
    stamp = result.pop("stamp", None)
    if stamp is not None:
        day = history.EPOCH + timedelta(days=stamp)
        page = history.journal_day(db, sess, day, highlight=result.get("e"))
        if day != sess.session_date or page.get("number"):
            result["page"] = page
    return result


def fit_journal_confirmation(payload, confirmation):
    # A full home page plus a full archive page can exceed Telegram's 1 KiB.
    # Keep the live ledger and reopen the exact archive page with a read-only query.
    if "page" in confirmation and len(encode(payload).encode()) > 1024:
        page = confirmation.pop("page")
        confirmation["goto"] = f"t{page['day']}.{page.get('number', 0)}"


def snapshot(db, sess, revision, widget_ack=None, *, journal=False):
    if journal:
        week = history.week_data(db, sess.chat_id, sess.session_date, sess.session_date)
        timezone = ZoneInfo(db.get(Chat, sess.chat_id).timezone)
        events = list_origin_events(db, sess.session_id, sess.chat_id, sess.chat_id)
        expiry = datetime.combine(sess.session_date + timedelta(days=2), time.min, tzinfo=timezone)
        state = dict(**week, r=revision, c=int(sess.status == "closed"), x=int(expiry.timestamp()),
                     items=[history.compact_entry(e, timezone) for e in reversed(events[-3:])])
        if len(events) > 3:
            state["more"] = [history.more_entries(week["day"], len(events) - 3, 1)]
        if state["c"] and not events:
            state["restore"] = f"Отметить за {history.short_date(sess.session_date)}"
        if widget_ack:
            state["ack"] = confirmed_view(db, sess, json.loads(widget_ack))
            fit_journal_confirmation(state, state["ack"])
        return state
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
    timezone = ZoneInfo(db.get(Chat, sess.chat_id).timezone)
    expiry = datetime.combine(
        sess.session_date + timedelta(days=2),
        time.min,
        tzinfo=timezone,
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
        events = list_origin_events(db, sess.session_id, sess.chat_id, sess.chat_id)
        data["entries"] = [history.entry(e, timezone) for e in reversed(events[-3:])]
        if widget_ack:
            data["ack"] = json.loads(widget_ack)
        fit_widget_history(data, data)
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
        delivery_key=f"{JOURNAL_PREFIX if sess.chat_id > 0 else PREFIX}{sess.session_id}:{uuid4()}",
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
            chat_data=encode(snapshot(db, sess, 1, journal=sess.chat_id > 0)),
            public_data=private_brand()[1] if sess.chat_id > 0 else None,
        )
    )
    row.photo_message_id = (
        sent.message_id
    )  # Existing outbox message address, also used by Mini App refresh.
    return sent


def question_query(chat_id, message_id):
    return (
        select(WeeklyReport)
        .join(
            SessionMessage,
            (SessionMessage.session_id == WeeklyReport.q1_session_id)
            & (SessionMessage.kind == "Q1")
            & (SessionMessage.message_id == WeeklyReport.photo_message_id),
        )
        .where(
            WeeklyReport.source_chat_id == chat_id,
            WeeklyReport.photo_message_id == message_id,
            WeeklyReport.delivery_key.startswith(PREFIX),
            WeeklyReport.disabled.is_(False),
        )
    )


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
    row = db.scalar(question_query(chat_id, q.get("message_id")))
    if row is None:
        return reject("Этот вопрос уже неактуален")
    data = str(q.get("data", ""))
    journal = row.delivery_key.startswith(JOURNAL_PREFIX)
    if data.startswith("q:j:") and journal and chat_id > 0:
        return history.journal_page(db, db.get(DaySession, row.q1_session_id), data[4:])
    if data.startswith("q:n:") and chat_id > 0:
        return history.personal_page(db, db.get(DaySession, row.q1_session_id), data[4:])
    if data.startswith("q:p:") and chat_id > 0:
        return history.icon_page(db, db.get(DaySession, row.q1_session_id), data[4:])
    if data.startswith("q:v:"):
        return history.view(db, db.get(DaySession, row.q1_session_id), data[4:])
    match = re.fullmatch(
        r"q:([+fs-]|a[0-3_][0-2_]|[eE]([1-9]\d{0,9})\.([a-f0-9]{16})\.([0-3_])([0-2_])"
        r"|D([1-9]\d{0,9})\.([a-f0-9]{16})):([a-f0-9]{16})", data,
    )
    if not match:
        return reject("Неизвестное действие")
    op, eid, version, kind, feeling, delete_id, delete_version, nonce = match.groups()
    if "_" in op and (not journal or chat_id <= 0 or op[0] not in ("a", "E")):
        return reject("Неизвестное действие")
    private_entry = op[0] in "ED"
    if private_entry and chat_id < 0:
        return reject("Доступно только в личной сессии")
    if delete_id:
        eid, version = delete_id, delete_version
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

    def result(msg, event=None, confirmation=None):
        db.flush()
        db.refresh(row)
        a = {"t": msg, "s": snapshot(db, sess, row.revision, journal=journal)}
        if event and op == "+":
            a.update(e=event.id, v=event_version(event)[:16])
        if chat_id > 0 and msg in ("Отметка сохранена", "Уточнения сохранены", "Отметка удалена"):
            a["ok"] = True
        if confirmation:
            a.update(confirmed_view(db, sess, confirmation))
            fit_journal_confirmation(a, a)
        fit_widget_history(a, a["s"])
        return a

    if receipt:
        if receipt.payload_hash != digest:
            return reject("Нажатие уже обработано")
        confirmation = {}
        if journal and op[0] in "aED":
            confirmation = {"ok": True, "e": receipt.event_id}
            if receipt.notify_date and (op.startswith("E") or receipt.notify_date != sess.session_date):
                confirmation["stamp"] = (receipt.notify_date - history.EPOCH).days
        return result(
            "Нажатие уже обработано",
            db.get(PoopEvent, receipt.event_id) if receipt.event_id else None, confirmation,
        )
    event = None
    event_day = sess.session_date
    if op != "f" and not private_entry and (
        sess.status != "active"
        or window.is_blocked_window
        or sess.session_date != window.session_date
    ):
        return result("Сессия закрыта")
    if op == "f" and sess.status != "closed":
        return result("Сессия ещё не закрыта")
    if (op in "+-" or op.startswith("a")) and not check_rate_limit(
        db, chat_id=chat_id, user_id=uid, scope="Q1", cooldown_seconds=2
    ):
        return result("Слишком быстро. Попробуй через пару секунд")
    ensure_chat_member(db, chat_id, uid)
    db.flush()
    if op.startswith("a"):
        if chat_id <= 0:
            return reject("Доступно только в личной сессии")
        try:
            created = record_mark(db, chat_id, uid, {
                "requestId": str(uuid5(NAMESPACE_URL, "add:" + rid)),
                "date": sess.session_date.isoformat(),
                "kind": None if op[1] == "_" else int(op[1]),
                "feeling": None if op[2] == "_" else int(op[2]),
            }, optional_details=journal)
            event = db.get(PoopEvent, int(created["eventId"]))
        except MarkRejected as exc:
            return result(str(exc))
        msg = "Отметка сохранена"
    elif op == "f":
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
        if private_entry:
            event, event_day = history.dated_event(db, uid, eid, sess.session_date) or (None, None)
        else:
            event = db.scalar(select(PoopEvent).where(
                PoopEvent.id == int(eid), PoopEvent.user_id == uid,
                PoopEvent.origin_chat_id == chat_id, PoopEvent.session_id == sess.session_id,
            ))
            event_day = sess.session_date
        if not event or event_version(event)[:16] != version:
            return result("Отметка изменилась. Открой /app")
        action = "delete" if delete_id else "edit"
        body = dict(
            action=action, requestId=str(uuid5(NAMESPACE_URL, action + ":" + rid)),
            eventId=eid, version=event_version(event), date=event_day.isoformat(),
        )
        if action == "edit":
            body.update(kind=None if kind == "_" else int(kind), feeling=None if feeling == "_" else int(feeling))
        try:
            change_mark(db, chat_id, uid, body, optional_details=journal)
        except MarkRejected as exc:
            return result(str(exc))
        if delete_id:
            event = None
        msg = "Отметка удалена" if delete_id else "Уточнения сохранены"
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
            notify_date=event_day,
            notify_pending=False,
        )
    )
    confirmation = {}
    if chat_id > 0:
        ack = {"n": nonce, "t": msg}
        if op.startswith(("a", "E", "D")):
            ack["ok"] = True
        if event and op == "+":
            ack.update(e=event.id, v=event_version(event)[:16])
        if journal and ack.get("ok"):
            confirmation = {"ok": True, "e": event.id if event else None}
            if op.startswith("E") or event_day != sess.session_date:
                confirmation["stamp"] = (event_day - history.EPOCH).days
            ack.update(confirmation)
        db.flush()
        db.execute(
            update(WeeklyReport).where(WeeklyReport.id == row.id).values(
                widget_ack=encode(ack), revision=WeeklyReport.revision + 1,
                attempts=0, retry_at=None,
            )
        )
    return result(msg, event, confirmation)


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
