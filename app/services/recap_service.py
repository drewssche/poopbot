from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Session as DaySession
from app.db.models import SessionUserState, PoopEvent, User, ChatMember


def _year_flavor(year: int) -> tuple[str, str, str]:
    packs = {
        2026: (
            "Год боевого ритма",
            "Этот год проверял дисциплину. Ты держался(ась) достойно.",
            "Финал года принят. Новый сезон можно открывать.",
        ),
    }
    return packs.get(
        year,
        (
            "Год в потоке",
            "Год был длинный, но ритм ты не потерял(а).",
            "Итоги зафиксированы. Дальше — только стабильнее.",
        ),
    )


def _phrase_toilet(day_key: str, count: int) -> str:
    variants = {
        "feb9": (
            "Праздник был, активности не было.",
            "Праздник отмечен достойно.",
            "Праздник прошёл по-королевски.",
        ),
        "nov19": (
            "День прошёл в режиме наблюдателя.",
            "Профессиональный минимум выполнен.",
            "Всемирный день отработан с мировым размахом.",
        ),
    }
    zero, low, high = variants[day_key]
    if count <= 0:
        return zero
    if count <= 2:
        return low
    return high


def _bot_first_interaction_date(db: Session) -> date | None:
    return db.scalar(select(func.min(DaySession.session_date)))


def recap_target_year(today: date) -> int:
    if today.month == 1 and today.day <= 3:
        return today.year - 1
    return today.year


def is_recap_available(today: date, user_id: int, owner_id: int | None) -> bool:
    if owner_id is not None and int(user_id) == int(owner_id):
        return True
    return (today.month == 12 and today.day >= 30) or (today.month == 1 and today.day <= 3)


def list_user_recap_chat_ids(db: Session, user_id: int, year: int) -> list[int]:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    rows = db.scalars(
        select(DaySession.chat_id)
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id < 0,
            DaySession.session_date >= start,
            DaySession.session_date <= end,
            SessionUserState.user_id == user_id,
            SessionUserState.poops_n > 0,
        )
        .group_by(DaySession.chat_id)
        .order_by(DaySession.chat_id.asc())
    ).all()
    return [int(cid) for cid in rows]


def list_user_member_chat_ids(db: Session, user_id: int) -> list[int]:
    rows = db.scalars(
        select(ChatMember.chat_id)
        .where(
            ChatMember.user_id == user_id,
            ChatMember.chat_id < 0,
        )
        .group_by(ChatMember.chat_id)
        .order_by(ChatMember.chat_id.asc())
    ).all()
    return [int(cid) for cid in rows]


def pick_user_recap_source_chat(db: Session, user_id: int, year: int) -> int | None:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    row = db.execute(
        select(
            DaySession.chat_id,
            func.coalesce(func.sum(SessionUserState.poops_n), 0).label("poops"),
        )
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id < 0,
            DaySession.session_date >= start,
            DaySession.session_date <= end,
            SessionUserState.user_id == user_id,
            SessionUserState.poops_n > 0,
        )
        .group_by(DaySession.chat_id)
        .order_by(func.coalesce(func.sum(SessionUserState.poops_n), 0).desc(), DaySession.chat_id.asc())
    ).first()
    if row is None:
        return None
    return int(row.chat_id)


def _count_for_day(db: Session, chat_id: int, user_id: int, day: date) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(SessionUserState.poops_n), 0))
            .join(DaySession, DaySession.session_id == SessionUserState.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date == day,
                SessionUserState.user_id == user_id,
            )
        )
        or 0
    )


def build_my_year_recap_cards(db: Session, chat_id: int, user_id: int, year: int) -> list[str]:
    first_interaction = _bot_first_interaction_date(db)
    start = date(year, 1, 1)
    if first_interaction is not None and first_interaction.year == year:
        start = max(start, first_interaction)
    end = date(year, 12, 31)

    sessions = db.scalars(
        select(DaySession).where(
            DaySession.chat_id == chat_id,
            DaySession.session_date >= start,
            DaySession.session_date <= end,
        )
    ).all()
    if not sessions:
        return [f"🎉 Твой рекап {year}\n\nПока пусто за этот год."]

    session_ids = [int(s.session_id) for s in sessions]
    date_by_session = {int(s.session_id): s.session_date for s in sessions}
    states = db.scalars(
        select(SessionUserState).where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.user_id == user_id,
        )
    ).all()

    total = sum(int(s.poops_n or 0) for s in states)

    active_days = sorted(
        date_by_session[int(s.session_id)]
        for s in states
        if int(s.poops_n or 0) > 0 and int(s.session_id) in date_by_session
    )
    unique_active_days: list[date] = []
    for d in active_days:
        if not unique_active_days or unique_active_days[-1] != d:
            unique_active_days.append(d)

    best_streak = 0
    if unique_active_days:
        run = 1
        best_streak = 1
        for i in range(1, len(unique_active_days)):
            if unique_active_days[i] == unique_active_days[i - 1] + timedelta(days=1):
                run += 1
            else:
                run = 1
            best_streak = max(best_streak, run)

    day_totals: dict[date, int] = {}
    for s in states:
        sid = int(s.session_id)
        if sid not in date_by_session:
            continue
        d = date_by_session[sid]
        day_totals[d] = day_totals.get(d, 0) + int(s.poops_n or 0)
    peak_day = max(day_totals.items(), key=lambda x: (x[1], x[0])) if day_totals else None

    feb9 = _count_for_day(db, chat_id, user_id, date(year, 2, 9))
    nov19 = _count_for_day(db, chat_id, user_id, date(year, 11, 19))

    title, intro, outro = _year_flavor(year)

    cards: list[str] = []
    cards.append(
        "\n".join(
            [
                f"🎉 Твой кака-рекап {year}",
                f"Тема года: {title}",
                "",
                f"Период: {start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')}",
                intro,
            ]
        )
    )

    avg_active = (float(total) / float(len(unique_active_days))) if unique_active_days else 0.0
    avg_period = (float(total) / float((end - start).days + 1)) if end >= start else 0.0
    cards.append(
        "\n".join(
            [
                "📊 Итоги года",
                f"💩 Всего за год: {total}",
                f"📅 Активных дней: {len(unique_active_days)}/{(end - start).days + 1}",
                f"🔥 Лучший стрик: {best_streak} дн.",
                f"Средний темп: {avg_period:.2f} в день, {avg_active:.2f} в активный день.",
                "Хороший базис. Пусть новый год будет ещё стабильнее.",
            ]
        )
    )

    top_peaks = sorted(day_totals.items(), key=lambda x: (-x[1], x[0]))[:3]
    if peak_day is not None:
        peak_lines = [f"{d.strftime('%d.%m.%y')} — 💩({n})" for d, n in top_peaks]
        cards.append(
            "\n".join(
                [
                    "🧨 Пиковый день",
                    f"{peak_day[0].strftime('%d.%m.%y')}: 💩({peak_day[1]})",
                    "О боже, что же тогда произошло?",
                    "Топ-3 пиковых дня:",
                    *[f"- {line}" for line in peak_lines],
                    "Пусть пики остаются мощными, но контролируемыми.",
                ]
            )
        )

    cards.append(
        "\n".join(
            [
                "💩 9 февраля — National Poop Day",
                f"Результат дня: 💩({feb9})",
                _phrase_toilet("feb9", feb9),
                "Отмечай тематические дни без пропусков.",
            ]
        )
    )

    cards.append(
        "\n".join(
            [
                "🚽 19 ноября — World Toilet Day",
                f"Результат дня: 💩({nov19})",
                _phrase_toilet("nov19", nov19),
                "На профильный праздник — профильный результат.",
            ]
        )
    )

    weekday_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    weekday_counts = [0] * 7
    for d in unique_active_days:
        weekday_counts[d.weekday()] += 1
    if any(weekday_counts):
        best_weekday_idx = max(range(7), key=lambda i: weekday_counts[i])
        cards.append(
            "\n".join(
                [
                    "🧠 Любопытный факт",
                    f"Чаще всего активность была в {weekday_names[best_weekday_idx]} ({weekday_counts[best_weekday_idx]} дн.).",
                    "У организма явно есть любимый слот.",
                    "Закрепи этот ритм и перенеси на весь следующий год.",
                ]
            )
        )

    cards.append(
        "\n".join(
            [
                "🏁 Финал",
                outro,
                "",
                "Надеюсь, ты не просрал этот год и не просрёшь следующий.",
            ]
        )
    )

    return cards


def _user_label(user: User | None, user_id: int) -> str:
    if user is None:
        return f"id:{user_id}"
    if user.username:
        return f"@{user.username}"
    full = " ".join(part for part in [user.first_name or "", user.last_name or ""] if part).strip()
    return full or f"id:{user_id}"


def build_chat_year_recap_cards(db: Session, chat_id: int, year: int) -> list[str]:
    chat_first = db.scalar(
        select(func.min(DaySession.session_date)).where(DaySession.chat_id == chat_id)
    )
    start = date(year, 1, 1)
    if chat_first is not None and chat_first.year == year:
        start = max(start, chat_first)
    end = date(year, 12, 31)

    sessions = db.scalars(
        select(DaySession).where(
            DaySession.chat_id == chat_id,
            DaySession.session_date >= start,
            DaySession.session_date <= end,
        )
    ).all()
    if not sessions:
        return [f"📊 Рекап чата {year}\n\nЗа этот год в чате пока пусто."]

    session_ids = [int(s.session_id) for s in sessions]
    day_by_sid = {int(s.session_id): s.session_date for s in sessions}
    period_days = (end - start).days + 1

    total_poops = int(
        db.scalar(
            select(func.coalesce(func.sum(SessionUserState.poops_n), 0)).where(SessionUserState.session_id.in_(session_ids))
        )
        or 0
    )
    by_user = db.execute(
        select(SessionUserState.user_id, func.sum(SessionUserState.poops_n).label("poops"))
        .where(SessionUserState.session_id.in_(session_ids))
        .group_by(SessionUserState.user_id)
        .order_by(func.sum(SessionUserState.poops_n).desc(), SessionUserState.user_id.asc())
    ).all()
    active_users = [(int(r.user_id), int(r.poops or 0)) for r in by_user if int(r.poops or 0) > 0]
    users = {
        int(u.user_id): u
        for u in db.scalars(select(User).where(User.user_id.in_([uid for uid, _ in active_users]))).all()
    } if active_users else {}

    day_rows = db.execute(
        select(DaySession.session_date, func.coalesce(func.sum(SessionUserState.poops_n), 0).label("poops"))
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(DaySession.chat_id == chat_id, DaySession.session_id.in_(session_ids))
        .group_by(DaySession.session_date)
        .order_by(DaySession.session_date.asc())
    ).all()
    active_days = [(d, int(p or 0)) for d, p in day_rows if int(p or 0) > 0]
    peak_day = max(active_days, key=lambda x: (x[1], x[0])) if active_days else None

    user_days: dict[int, list[date]] = {}
    state_rows = db.execute(
        select(SessionUserState.user_id, SessionUserState.session_id, SessionUserState.poops_n)
        .where(SessionUserState.session_id.in_(session_ids), SessionUserState.poops_n > 0)
    ).all()
    for uid, sid, _poops in state_rows:
        d = day_by_sid.get(int(sid))
        if d is None:
            continue
        user_days.setdefault(int(uid), []).append(d)
    for uid in list(user_days.keys()):
        user_days[uid] = sorted(set(user_days[uid]))

    best_streak_user: tuple[int, int] | None = None
    for uid, days in user_days.items():
        if not days:
            continue
        run = 1
        best = 1
        for i in range(1, len(days)):
            if days[i] == days[i - 1] + timedelta(days=1):
                run += 1
            else:
                run = 1
            best = max(best, run)
        if best_streak_user is None or best > best_streak_user[1]:
            best_streak_user = (uid, best)

    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    ev_rows = db.scalars(select(PoopEvent).where(PoopEvent.session_id.in_(session_ids))).all()
    for e in ev_rows:
        if e.bristol is not None:
            b = int(e.bristol)
            if b <= 2:
                br["🧱"] += 1
            elif b <= 4:
                br["🍌"] += 1
            elif b <= 6:
                br["🍦"] += 1
            else:
                br["💦"] += 1
        if e.feeling == "great":
            fe["😇"] += 1
        elif e.feeling == "ok":
            fe["😐"] += 1
        elif e.feeling == "bad":
            fe["😫"] += 1

    if sum(br.values()) == 0 or sum(fe.values()) == 0:
        fallback_states = db.scalars(
            select(SessionUserState).where(SessionUserState.session_id.in_(session_ids), SessionUserState.poops_n > 0)
        ).all()
        for s in fallback_states:
            if sum(br.values()) == 0 and s.bristol is not None:
                b = int(s.bristol)
                if b <= 2:
                    br["🧱"] += 1
                elif b <= 4:
                    br["🍌"] += 1
                elif b <= 6:
                    br["🍦"] += 1
                else:
                    br["💦"] += 1
            if sum(fe.values()) == 0:
                if s.feeling == "great":
                    fe["😇"] += 1
                elif s.feeling == "ok":
                    fe["😐"] += 1
                elif s.feeling == "bad":
                    fe["😫"] += 1

    feb9_total = int(
        db.scalar(
            select(func.coalesce(func.sum(SessionUserState.poops_n), 0))
            .join(DaySession, DaySession.session_id == SessionUserState.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date == date(year, 2, 9),
            )
        )
        or 0
    )
    nov19_total = int(
        db.scalar(
            select(func.coalesce(func.sum(SessionUserState.poops_n), 0))
            .join(DaySession, DaySession.session_id == SessionUserState.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date == date(year, 11, 19),
            )
        )
        or 0
    )

    top3 = active_users[:3]
    top3_lines = [f"- {i}) {_user_label(users.get(uid), uid)} — 💩({poops})" for i, (uid, poops) in enumerate(top3, start=1)]

    def _dist_lines(title: str, data: dict[str, int]) -> list[str]:
        total = sum(data.values())
        if total <= 0:
            return [title, "- пока нет данных"]
        lines = [title]
        for icon, count in sorted(data.items(), key=lambda x: (-x[1], x[0])):
            pct = int(round(100.0 * float(count) / float(total)))
            lines.append(f"- {icon}: {pct}% ({count})")
        return lines

    cards: list[str] = []
    cards.append(
        "\n".join(
            [
                f"📊 Рекап чата {year}",
                f"Период: {start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')}",
                "",
                f"За год в чате набежало 💩({total_poops}).",
                f"Активных участников: {len(active_users)}.",
            ]
        )
    )

    avg_per_day = (float(total_poops) / float(period_days)) if period_days > 0 else 0.0
    cards.append(
        "\n".join(
            [
                "⚙️ Ритм чата",
                f"Средний темп: {avg_per_day:.2f} в день.",
                f"Активных дней: {len(active_days)}/{period_days}.",
                (
                    f"Пиковый день: {peak_day[0].strftime('%d.%m.%y')} — 💩({peak_day[1]})."
                    if peak_day is not None
                    else "Пиковый день: пока нет."
                ),
                "Да, было горячо.",
            ]
        )
    )

    top_block = ["🏆 Топ участников"]
    if top3_lines:
        top_block.extend(top3_lines)
    else:
        top_block.append("- пока нет данных")
    top_block.extend(["", "Вот кто тащил чат в этом году."])
    cards.append("\n".join(top_block))

    if best_streak_user is not None:
        uid, days = best_streak_user
        streak_line = f"Лучший стрик: {_user_label(users.get(uid), uid)} — {days} дн."
    else:
        streak_line = "Лучший стрик: пока нет данных."
    cards.append(
        "\n".join(
            [
                "🔥 Линия стабильности",
                streak_line,
                "Если держать так дальше — это уже стиль жизни.",
            ]
        )
    )

    cards.append("\n".join(_dist_lines("🧻 Бристоль по чату:", br) + [""] + _dist_lines("😮‍💨 Ощущения по чату:", fe)))

    cards.append(
        "\n".join(
            [
                "🎯 Праздничные даты",
                f"9 февраля (National Poop Day): 💩({feb9_total})",
                f"19 ноября (World Toilet Day): 💩({nov19_total})",
                "",
                "Праздники — это святое.",
            ]
        )
    )

    cards.append(
        "\n".join(
            [
                "🏁 Финал чата",
                "Год закрыт уверенно.",
                "Надеюсь, чат не просрал этот год и не просрёт следующий.",
            ]
        )
    )

    return cards


def build_my_year_recap_cards_all_chats(db: Session, user_id: int, year: int) -> list[str]:
    first_interaction = _bot_first_interaction_date(db)
    start = date(year, 1, 1)
    if first_interaction is not None and first_interaction.year == year:
        start = max(start, first_interaction)
    end = date(year, 12, 31)

    states = db.scalars(
        select(SessionUserState)
        .join(DaySession, DaySession.session_id == SessionUserState.session_id)
        .where(
            DaySession.chat_id < 0,
            DaySession.session_date >= start,
            DaySession.session_date <= end,
            SessionUserState.user_id == user_id,
        )
    ).all()
    if not states:
        return [f"🎉 Твой рекап {year}\n\nПока пусто за этот год."]

    session_ids = [int(s.session_id) for s in states]
    session_rows = db.execute(
        select(DaySession.session_id, DaySession.session_date, DaySession.chat_id).where(DaySession.session_id.in_(session_ids))
    ).all()
    by_sid_date = {int(sid): sdate for sid, sdate, _ in session_rows}
    by_sid_chat = {int(sid): int(cid) for sid, _, cid in session_rows}

    total = sum(int(s.poops_n or 0) for s in states)
    period_days = (end - start).days + 1

    active_days = sorted(
        by_sid_date[int(s.session_id)]
        for s in states
        if int(s.poops_n or 0) > 0 and int(s.session_id) in by_sid_date
    )
    unique_days: list[date] = []
    for d in active_days:
        if not unique_days or unique_days[-1] != d:
            unique_days.append(d)

    best_streak = 0
    if unique_days:
        run = 1
        best_streak = 1
        for i in range(1, len(unique_days)):
            if unique_days[i] == unique_days[i - 1] + timedelta(days=1):
                run += 1
            else:
                run = 1
            best_streak = max(best_streak, run)

    day_totals: dict[date, int] = {}
    chat_totals: dict[int, int] = {}
    for s in states:
        sid = int(s.session_id)
        poops = int(s.poops_n or 0)
        if sid in by_sid_date:
            d = by_sid_date[sid]
            day_totals[d] = day_totals.get(d, 0) + poops
        if sid in by_sid_chat:
            cid = by_sid_chat[sid]
            chat_totals[cid] = chat_totals.get(cid, 0) + poops
    peak_day = max(day_totals.items(), key=lambda x: (x[1], x[0])) if day_totals else None
    top_chats = sorted(chat_totals.items(), key=lambda x: (-x[1], x[0]))[:3]

    ev_rows = db.scalars(
        select(PoopEvent)
        .where(
            PoopEvent.session_id.in_(session_ids),
            PoopEvent.user_id == user_id,
        )
    ).all()
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    for e in ev_rows:
        if e.bristol is not None:
            b = int(e.bristol)
            if b <= 2:
                br["🧱"] += 1
            elif b <= 4:
                br["🍌"] += 1
            elif b <= 6:
                br["🍦"] += 1
            else:
                br["💦"] += 1
        if e.feeling == "great":
            fe["😇"] += 1
        elif e.feeling == "ok":
            fe["😐"] += 1
        elif e.feeling == "bad":
            fe["😫"] += 1

    chat_count = len([cid for cid, val in chat_totals.items() if val > 0])
    avg_period = (float(total) / float(period_days)) if period_days > 0 else 0.0
    avg_active = (float(total) / float(len(unique_days))) if unique_days else 0.0

    def _dist_lines(title: str, data: dict[str, int]) -> list[str]:
        total_n = sum(data.values())
        if total_n <= 0:
            return [title, "- пока нет данных"]
        out = [title]
        for icon, count in sorted(data.items(), key=lambda x: (-x[1], x[0])):
            pct = int(round(100.0 * float(count) / float(total_n)))
            out.append(f"- {icon}: {pct}% ({count})")
        return out

    cards: list[str] = []
    cards.append(
        "\n".join(
            [
                f"🎉 Твой рекап {year}",
                f"Период: {start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')}",
                "",
                f"Собран по всем чатам: {chat_count}.",
            ]
        )
    )

    cards.append(
        "\n".join(
            [
                "📊 Твой общий итог",
                f"💩 Всего: {total}",
                f"📅 Активных дней: {len(unique_days)}/{period_days}",
                f"🔥 Лучший стрик: {best_streak} дн.",
                f"Средний темп: {avg_period:.2f} в день, {avg_active:.2f} в активный день.",
            ]
        )
    )

    if top_chats:
        lines = ["🏟 Вклад чатов"]
        for i, (cid, poops) in enumerate(top_chats, start=1):
            lines.append(f"- {i}) Чат {cid}: 💩({poops})")
        cards.append("\n".join(lines))

    if peak_day is not None:
        cards.append(
            "\n".join(
                [
                    "🧨 Пиковый день",
                    f"{peak_day[0].strftime('%d.%m.%y')}: 💩({peak_day[1]})",
                    "Да, было плотно.",
                ]
            )
        )

    cards.append("\n".join(_dist_lines("🧻 Бристоль:", br) + [""] + _dist_lines("😮‍💨 Ощущения:", fe)))
    cards.append("🏁 Финал\nСрез готов. Не просри следующий год.")
    return cards
