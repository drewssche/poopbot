"""Read-only pages for navigating an owner's daily widget."""

import calendar
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

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
    counts = dict(db.execute(
        select(DaySession.session_date, func.count(PoopEvent.id)).join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(DaySession.chat_id == owner, PoopEvent.user_id == owner, PoopEvent.origin_chat_id == owner,
               DaySession.session_date.between(month, min(last, anchor))).group_by(DaySession.session_date)
    ).all())
    nav = []
    if month > date(2000, 1, 1):
        nav.append(["‹", "m" + (month - timedelta(days=1)).strftime("%Y%m")])
    nav.append(["К опросу", "home"])
    if last < anchor:
        nav.append(["›", "m" + (last + timedelta(days=1)).strftime("%Y%m")])
    return dict(title=f"{MONTHS[month.month - 1]} {month.year}", month=month.strftime("%Y%m"),
                offset=month.weekday(), days=last.day, until=min(anchor,last).day,
                marked=[d.day for d in sorted(counts)], counts={d.day:n for d,n in counts.items()}, nav=nav)


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
    match = re.fullmatch(r"([uikxf])([1-9]\d{0,9})\.([a-f0-9]{16})(?:\.([0-3]))?", route)
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
    if step == "u":
        return editor_view(event, key, back, title)
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


KIND_ICONS = ("🪨", "🪵", "💩", "💧")
FEELING_ICONS = ("🙂", "😐", "🙁")


def editor_view(event=None, key=None, back="home", title="", *, labeled=False):
    from app.services.daily_widget_demo import ASSETS

    sections = []
    for name, options, glyphs in (
        ("Консистенция", KINDS, KIND_ICONS),
        ("Самочувствие", FEELINGS.values(), FEELING_ICONS),
    ):
        choices = "".join(
            f"<button>{glyph}<br>{label}</button>" if labeled else f'<button aria-label="{label}">{glyph}</button>'
            for label, glyph in zip(options, glyphs)
        )
        sections.append(f'<b>{name}</b><div class="choices">{choices}</div>')
    kind = event_kind(event) if event else None
    feeling = list(FEELINGS).index(event.feeling) if event and event.feeling in FEELINGS else -1
    page = dict(title=title.partition(" · ")[0], form="".join(sections), values=[-1 if kind is None else kind, feeling],
                op=f"E{key}." if key else "a", nav=[] if labeled else [["Назад", back]])
    if key:
        page["remove"] = f"x{key}"
        if not labeled:
            page["trash"] = (ASSETS / "trash.svg").read_text().strip().replace("#fff", "#934c21")
    return page

def icon_page(db, sess, route):
    if route == "menu":
        from app.services.q1_service import render_q1_private, should_show_restore_streak_button

        owner = sess.chat_id
        text = render_q1_private(db, owner, sess.session_id, owner, sess.session_date, closed=sess.status == "closed")
        nav = []
        if sess.status != "closed" and should_show_restore_streak_button(
            db, chat_id=owner, session_date=sess.session_date, viewer_user_id=owner, is_private_chat=True
        ):
            nav.append(["Вернуть стрик", "!s", "↶"])
        nav.extend([["Мини-апп", "app", "↗"], ["Назад", "home", "‹"]])
        body = "\n".join(text.splitlines()[4:]).split("\n\n")[0]
        return dict(title="Итоги дня", body=body[:180], nav=nav)
    page = editor_view() if route == "new" else view(db, sess, route)
    if "nav" in page:
        icons = {"Ещё": "›", "Месяц": "▦", "К опросу": "⌂", "Назад": "‹"}
        page["nav"] = [[label, key, icons.get(label, label)] for label, key in page["nav"]]
        if route.startswith("x"):
            label, key = page.pop("items")[0]
            page["nav"].insert(0, [label, key, "✓"])
        page.pop("marked", None)
    return page


def personal_page(db, sess, route):
    """Views for the daily widget with a persistent header and local back history."""
    owner = sess.chat_id
    if owner <= 0:
        return {"t": "Доступно только в личной сессии"}
    if route == "help":
        from app.bot.handlers.help_content import private_help_text

        return dict(title="Помощь", body=private_help_text(db.get(Chat, owner).timezone).partition("\n\n")[2], nav=[])
    periods = {"week": "Неделя", "month": "Месяц", "year": "Год"}
    if route in periods:
        from app.services.stats_common import Range, format_period, period_to_range
        from app.services.stats_metrics import compute_chat_period_metrics

        anchor = sess.session_date
        span = Range(period_to_range(anchor, route).start, anchor)
        metrics = compute_chat_period_metrics(db, owner, span, user_id=owner)
        body = (f"{format_period(span)}\n\nОтметок · {metrics.total_poops}\n"
                f"Дней с отметками · {metrics.active_days_count} из {metrics.period_days}")
        return dict(title=f"Статистика · {periods[route].lower()}", body=body,
                    nav=[[label, key] for key, label in periods.items() if key != route])
    page = icon_page(db, sess, route)
    if "nav" not in page:
        return page
    if "month" in page:
        counts = page.pop("counts")
        until = page.pop("until")
        page["cells"] = [counts.get(i, 0) if i <= until else -1 for i in range(1, page.pop("days") + 1)]
    if route == "menu":
        page["title"] = "Меню"
        page["nav"] = [["Статистика", "week"], ["Помощь", "help"]] + [
            [label, key] for label, key, *_ in page["nav"] if key != "home"
        ]
    else:
        nav = []
        for item in page["nav"]:
            label, key = item[:2]
            if label == "Месяц":
                nav.append(["Календарь", key])
            elif label not in ("Назад", "К опросу"):
                nav.append(item)
        page["nav"] = nav
        if page["title"].endswith(" · Нет отметок"):
            page["title"] = page["title"].split(" · ")[0]
            page["body"] = "Нет отметок"
    return page


EPOCH = date(1970, 1, 1)


def week_data(db, owner, selected, anchor):
    start = selected - timedelta(days=selected.weekday())
    counts = dict(db.execute(
        select(DaySession.session_date, func.count(PoopEvent.id))
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(DaySession.chat_id == owner, PoopEvent.origin_chat_id == owner, PoopEvent.user_id == owner,
               DaySession.session_date.between(start, min(start + timedelta(days=6), anchor)))
        .group_by(DaySession.session_date)
    ).all())
    return dict(start=(start - EPOCH).days, day=(selected - EPOCH).days,
                label=f"{MONTHS[selected.month - 1]} {selected.year}",
                d=[counts.get(start + timedelta(days=i), 0) for i in range(7)])


def compact_entry(event, timezone):
    occurred = event_occurrence_time(event)
    time = occurred.astimezone(timezone).strftime("%H:%M") if occurred else "—"
    kind = event_kind(event)
    choices = []
    if kind is not None:
        choices.append((KIND_ICONS[kind], KINDS[kind]))
    if event.feeling in FEELINGS:
        choices.append((FEELING_ICONS[list(FEELINGS).index(event.feeling)], FEELINGS[event.feeling]))
    return [" ".join([time, *(icon for icon, _ in choices)]), f"{event.id}.{event_version(event)[:16]}",
            ", ".join(label for _, label in choices)]


def journal_day(db, sess, day, page=0, *, highlight=None):
    owner = sess.chat_id
    week = week_data(db, owner, day, sess.session_date)
    sid = db.scalar(select(DaySession.session_id).where(DaySession.chat_id == owner, DaySession.session_date == day))
    events = list(reversed(list_origin_events(db, sid, owner, owner))) if sid else []
    if highlight:
        page = next((i // 3 for i, event in enumerate(events) if event.id == highlight), page)
    page = min(page, max(0, (len(events) - 1) // 3))
    nav = []
    for offset, label in ((-1, "‹"), (1, "›")):
        target = page + offset
        if 0 <= target * 3 < len(events):
            nav.append([label, f"t{week['day']}.{target}"])
    timezone = ZoneInfo(db.get(Chat, owner).timezone)
    return dict(**week, title=day.strftime("%d.%m.%Y"), nav=nav,
                body="" if events else "Нет отметок",
                items=[compact_entry(e, timezone) for e in events[page * 3:page * 3 + 3]])


def journal_page(db, sess, route):
    if sess.chat_id <= 0:
        return {"t": "Доступно только в личной сессии"}
    day_match = re.fullmatch(r"t(\d{5})(?:\.([0-3]))?", route)
    if day_match:
        day = EPOCH + timedelta(days=int(day_match[1]))
        if day > sess.session_date:
            return {"t": "Этот день ещё не наступил"}
        if day == sess.session_date and not int(day_match[2] or 0):
            return {"home": True}
        return journal_day(db, sess, day, int(day_match[2] or 0))
    if route == "new":
        return editor_view(labeled=True)
    event_route = re.fullmatch(r"([ux])([1-9]\d{0,9})\.([a-f0-9]{16})", route)
    if event_route:
        action, eid, version = event_route.groups()
        row = dated_event(db, sess.chat_id, eid, sess.session_date)
        if not row or event_version(row[0])[:16] != version:
            return {"t": "Отметка изменилась. Выбери её заново"}
        event = row[0]
        occurred = event_occurrence_time(event)
        time = occurred.astimezone(ZoneInfo(db.get(Chat, sess.chat_id).timezone)).strftime("%H:%M") if occurred else None
        key = route[1:]
        if action == "u":
            return editor_view(event, key, title=time or "Отметка", labeled=True)
        title = f"Удалить отметку за {time}?" if time else f"Удалить отметку №{event.event_n}?"
        return dict(title=title, nav=[["Удалить", "!D" + key]])
    page = personal_page(db, sess, route)
    if "month" in page:
        month = date.fromisoformat(page["month"] + "01")
        page["start"] = (month - EPOCH).days
    elif route in ("week", "month", "year") and "body" in page:
        page.update(active=route, nav=[["Неделя", "week"], ["Месяц", "month"], ["Год", "year"]])
    return page
