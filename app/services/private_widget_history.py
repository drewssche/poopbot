"""Read-only pages for navigating an owner's daily widget."""

import calendar
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import Chat, PoopEvent, Session as DaySession
from app.services.event_time_service import event_occurrence_time
from app.services.mini_app_service import event_kind, event_version
from app.services.poop_event_service import list_origin_events

MONTHS = "январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь".split()
KINDS = ("Жестко", "Норма", "Мягко", "Водичка")
FEELINGS = {"great": "Прекрасно", "ok": "Сойдёт", "bad": "Ужасно"}


def entry(event, timezone):
    occurred = event_occurrence_time(event)
    kind = event_kind(event)
    label = " · ".join(filter(None, (
        f"№{event.event_n}", occurred.astimezone(timezone).strftime("%H:%M") if occurred else "",
        KINDS[kind] if kind is not None else None, FEELINGS.get(event.feeling),
    )))
    return [label, f"i{event.id}.{event_version(event)[:16]}"]


def dated_event(db, owner, eid, last_day):
    return db.execute(
        select(PoopEvent, DaySession.session_date)
        .join(DaySession, DaySession.session_id == PoopEvent.session_id)
        .where(PoopEvent.id == int(eid), PoopEvent.user_id == owner,
               PoopEvent.origin_chat_id == owner, DaySession.chat_id == owner,
               DaySession.session_date <= last_day)
    ).one_or_none()


def month_view(db, owner, month, anchor):
    last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    marked = set(db.scalars(
        select(DaySession.session_date).join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(DaySession.chat_id == owner, PoopEvent.user_id == owner, PoopEvent.origin_chat_id == owner,
               DaySession.session_date.between(month, min(last, anchor))).distinct()
    ))
    nav = []
    if month > date(2000, 1, 1):
        nav.append(["‹", "m" + (month - timedelta(days=1)).strftime("%Y%m")])
    nav.append(["К опросу", "home"])
    if last < anchor:
        nav.append(["›", "m" + (last + timedelta(days=1)).strftime("%Y%m")])
    return dict(title=f"{MONTHS[month.month - 1]} {month.year}", month=month.strftime("%Y%m"),
                offset=month.weekday(), days=last.day, until=min(anchor,last).day,
                marked=[d.day for d in sorted(marked)], nav=nav)


def view(db, sess, route):
    owner, anchor = sess.chat_id, sess.session_date
    if owner <= 0:
        return {"t": "Доступно только в личной сессии"}
    if re.fullmatch(r"w[0-6]", route):
        day = anchor + timedelta(days=int(route[1]) - anchor.weekday())
        route = "d" + day.strftime("%Y%m%d")
    if route == "m":
        route += anchor.strftime("%Y%m")
    day_route = re.fullmatch(r"d(20\d{6})(?:\.([0-3]))?", route)
    month_route = re.fullmatch(r"m(20\d{4})", route)
    if day_route or month_route:
        stamp = day_route[1] if day_route else month_route[1] + "01"
        try:
            day = date.fromisoformat(stamp)
        except ValueError:
            return {"t": "Недоступная дата"}
        if day > anchor:
            return {"t": "Этот день ещё не наступил"}
        if month_route:
            return month_view(db, owner, day, anchor)
        page = int(day_route[2] or 0)
        if day == anchor and page == 0:
            return {"home": True}
        sid = db.scalar(select(DaySession.session_id).where(DaySession.chat_id == owner, DaySession.session_date == day))
        events = list_origin_events(db, sid, owner, owner) if sid else []
        timezone = ZoneInfo(db.get(Chat, owner).timezone)
        batch = list(reversed(events))[page * 3:page * 3 + 3]
        items = [entry(event, timezone) for event in batch]
        nav = []
        if page:
            nav.append(["‹", f"d{stamp}.{page - 1}"])
        if (page + 1) * 3 < len(events) and page < 3:
            nav.append(["Ещё", f"d{stamp}.{page + 1}"])
        nav.extend([["Месяц", "m" + stamp[:6]], ["К опросу", "home"]])
        return dict(title=day.strftime("%d.%m.%Y") + (" · Нет отметок" if not events else ""), items=items, nav=nav)
    match = re.fullmatch(r"([ikxf])([1-9]\d{0,9})\.([a-f0-9]{16})(?:\.([0-3]))?", route)
    if not match:
        return {"t": "Неизвестное действие"}
    step, eid, version, kind = match.groups()
    row = dated_event(db, owner, eid, anchor)
    if not row or event_version(row[0])[:16] != version:
        return {"t": "Отметка изменилась. Выбери её заново"}
    event, day = row
    key = eid + "." + version
    back = "home" if day == anchor else "d" + day.strftime("%Y%m%d")
    nav = [["Назад", back]]
    title = entry(event, ZoneInfo(db.get(Chat, owner).timezone))[0]
    if step == "i":
        items = [["Редактировать", "k" + key], ["Удалить", "x" + key]]
    elif step == "x":
        title = f"Удалить отметку №{event.event_n}?"
        items = [["Удалить", "!D" + key]]
    elif step == "k":
        title = "Консистенция кала"
        items = [[label, f"f{key}.{i}"] for i, label in enumerate(KINDS)]
    elif kind is not None:
        title = "Мои ощущения"
        items = [[label, f"!E{key}.{kind}{i}"] for i, label in enumerate(FEELINGS.values())]
    else:
        return {"t": "Неизвестное действие"}
    return dict(title=title, items=items, nav=nav)
