from __future__ import annotations

import calendar
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import PoopEvent, SessionUserState, User
from app.services.stats_common import (
    Range,
    estimate_waste_metrics,
    format_mass,
    format_period,
    format_water,
    period_label,
    period_to_range,
    previous_period_range,
)
from app.services.stats_metrics import (
    BRISTOL_LEGEND,
    FEELING_LEGEND,
    TOP5_ROLES,
    bot_start_date as _bot_start_date,
    bristol_bucket as _bristol_bucket,
    bristol_from_avg as _bristol_from_avg,
    bristol_score as _bristol_score,
    chat_origin_events_in_range as _chat_origin_events_in_range,
    chat_start_date as _chat_start_date,
    collect_events_map as _collect_events_map,
    compute_chat_period_metrics,
    current_global_king as _current_global_king,
    display_name as _display_name,
    feeling_emoji as _feeling_emoji,
    feeling_from_avg as _feeling_from_avg,
    feeling_score as _feeling_score,
    format_dist_block as _format_dist_block,
    is_user_participant_in_chat as _is_user_participant_in_chat,
    iter_effective_events as _iter_effective_events,
    per_user_totals_dedup as _per_user_totals_dedup,
    sessions_in_range as _sessions_in_range,
)
from app.services.stats_rankings import collect_among_chats_snapshot, rank_chat_among_groups_by_total
from app.services.stats_streaks import (
    best_streak_from_days as _best_streak_from_days,
    calc_above_percent as _calc_above_percent,
    chat_streak_leader as _chat_streak_leader,
    compute_chat_user_streaks_live as _compute_chat_user_streaks_live,
    compute_user_chat_best_streak_live as _compute_user_chat_best_streak_live,
    compute_user_chat_streak_live as _compute_user_chat_streak_live,
    compute_user_global_best_streak_live as _compute_user_global_best_streak_live,
    compute_user_global_streak_live as _compute_user_global_streak_live,
    current_streak_from_days as _current_streak_from_days,
    build_stats_raw_debug_text,
    streak_nickname as _streak_nickname,
)
from app.services.time_service import now_in_tz, get_time_slot, get_slot_emoji, get_slot_title, get_dominant_slot


def _empty_slot_counts() -> dict[str, int]:
    return {"night": 0, "morning": 0, "afternoon": 0, "evening": 0}


def _compute_slot_patterns(db: Session, session_ids: list[int], user_id: int, tz_name: str = "Europe/Minsk") -> dict[str, int]:
    """Подсчитывает события пользователя по временным слотам."""
    if not session_ids:
        return _empty_slot_counts()
    
    events = db.scalars(
        select(PoopEvent).where(
            PoopEvent.session_id.in_(session_ids),
            PoopEvent.user_id == user_id
        )
    ).all()
    
    slot_counts = _empty_slot_counts()
    for ev in events:
        if ev.created_at:
            slot = get_time_slot(ev.created_at, tz_name)
            slot_counts[slot] = slot_counts.get(slot, 0) + 1

    return slot_counts


def _compute_slot_patterns_from_state_rows(
    states: list[SessionUserState],
    events_map: dict[tuple[int, int], list[PoopEvent]],
    *,
    tz_name: str = "Europe/Minsk",
) -> dict[str, int]:
    """Считает паттерны только по каноническим состояниям, чтобы не расходиться с dedup-итогами."""
    slot_counts = _empty_slot_counts()
    for state in states:
        for ev in events_map.get((int(state.session_id), int(state.user_id)), []):
            if ev.created_at:
                slot = get_time_slot(ev.created_at, tz_name)
                slot_counts[slot] = slot_counts.get(slot, 0) + 1
    return slot_counts


def _format_slot_patterns(slot_counts: dict[str, int]) -> list[str]:
    """Форматирует паттерны для отображения в статистике."""
    slot_total = sum(slot_counts.values())
    if slot_total == 0:
        return ["Пока нет данных для анализа паттернов."]
    
    lines = []
    labels = [
        ("night", "🌙 Ночь (00–06)"),
        ("morning", "🌅 Утро (06–12)"),
        ("afternoon", "☀️ День (12–18)"),
        ("evening", "🌆 Вечер (18–24)"),
    ]
    
    peak_slot = max(slot_counts.keys(), key=lambda s: slot_counts.get(s, 0)) if slot_total > 0 else None
    
    for slot, label in labels:
        count = slot_counts.get(slot, 0)
        pct = (count / slot_total * 100) if slot_total > 0 else 0
        peak_marker = " ← Пик!" if slot == peak_slot and count > 0 else ""
        lines.append(f"{label}:   {count} раз  ({pct:.0f}%){peak_marker}")
    
    return lines


def _get_pattern_title(slot_counts: dict[str, int], total: int) -> str | None:
    """Определяет титул по паттернам."""
    if total == 0:
        return None

    dominant = get_dominant_slot(slot_counts)
    if dominant is None:
        return None

    return get_slot_title(dominant)


def build_stats_text_my(db: Session, chat_id: int, user_id: int, today: date, period: str) -> str:
    _ = chat_id
    bot_start = _bot_start_date(db)
    if bot_start is None:
        empty_range = period_to_range(today, period) if period in {"today", "week", "month", "year"} else Range(today, today)
        return (
            "🙋 Моя статистика\n"
            f"Период: {period_label(period)} (по всем чатам, {format_period(empty_range)})\n\n"
            "Пока нет данных."
        )

    if period in {"today", "week", "month", "year"}:
        r = period_to_range(today, period)
    else:
        r = Range(bot_start, today)
    sessions = _sessions_in_range(db, None, r)
    if not sessions:
        return (
            "🙋 Моя статистика\n"
            f"Период: {period_label(period)} (по всем чатам, {format_period(r)})\n\n"
            "Пока нет данных."
        )

    session_ids = [s.session_id for s in sessions]
    states = db.scalars(
        select(SessionUserState).where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.user_id == user_id,
            SessionUserState.poops_n > 0,
        )
    ).all()

    session_date_by_id = {int(s.session_id): s.session_date for s in sessions}
    states_by_day: dict[date, list[SessionUserState]] = {}
    for st in states:
        sid = int(st.session_id)
        if sid not in session_date_by_id:
            continue
        d = session_date_by_id[sid]
        states_by_day.setdefault(d, []).append(st)

    daily_poops: dict[date, int] = {}
    for d, day_states in states_by_day.items():
        daily_poops[d] = max(int(s.poops_n or 0) for s in day_states)

    total_poops = sum(daily_poops.values())
    days_total = (r.end - r.start).days + 1
    avg_per_day = (float(total_poops) / float(days_total)) if days_total > 0 else 0.0

    active_dates = sorted([d for d, n in daily_poops.items() if n > 0])
    days_any = len(active_dates)
    avg_per_active_day = (float(total_poops) / float(days_any)) if days_any > 0 else 0.0
    last_mark_date = active_dates[-1] if active_dates else None

    best_streak_live = _compute_user_global_best_streak_live(db, user_id, today)

    best_day = max(daily_poops.items(), key=lambda x: (x[1], x[0])) if daily_poops else None

    events_map = _collect_events_map(db, session_ids, user_id=user_id)
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    canonical_states: list[SessionUserState] = []
    for day_states in states_by_day.values():
        canonical_states.append(
            max(day_states, key=lambda s: (int(s.poops_n or 0), int(s.session_id)))
        )

    for st in canonical_states:
        for bristol, feeling in _iter_effective_events(st, events_map):
            b = _bristol_bucket(bristol)
            if b:
                br[b] += 1
            f = _feeling_emoji(feeling)
            if f:
                fe[f] += 1

    streak_val = _compute_user_global_streak_live(db, user_id, today)

    slot_counts = _compute_slot_patterns_from_state_rows(canonical_states, events_map)
    pattern_title = _get_pattern_title(slot_counts, total_poops)

    lines = [
        "🙋 Моя статистика",
        f"Период: {period_label(period)} (по всем чатам, {format_period(r)})",
        "",
        "Твои итоги:",
        f"- Всего: 💩({total_poops})",
        f"- Дней с 💩: {days_any}/{days_total}",
        f"- Текущий глобальный стрик (по всем чатам): {streak_val} дн.",
        f"- Лучший стрик: {best_streak_live} дн.",
        "",
    ]

    # Добавляем блок паттернов
    if total_poops > 0:
        lines.append("🕐 Твой ритм:")
        lines.extend(_format_slot_patterns(slot_counts))
        if pattern_title:
            lines.append("")
            lines.append(f"💡 Титул: «{pattern_title}»")
        lines.append("")

    lines.extend([
        "Твоя динамика:",
        f"- Среднее за календарный день: {avg_per_day:.2f}",
        f"- Среднее за день с отметкой: {avg_per_active_day:.2f}",
        (
            f"- Самый активный день: {best_day[0].strftime('%d.%m.%y')} (💩({best_day[1]}))"
            if best_day
            else "- Самый активный день: нет данных"
        ),
        (
            f"- Последняя отметка: {last_mark_date.strftime('%d.%m.%y')}"
            if last_mark_date
            else "- Последняя отметка: нет данных"
        ),
        "",
    ])
    mass_g, water_l, water_gal = estimate_waste_metrics(total_poops)
    lines.extend(
        [
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- Насрано примерно: {format_mass(mass_g)} (по 💩({int(total_poops)})).",
            f"- Воды на смыв: {format_water(water_l, water_gal)}.",
            "",
        ]
    )
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))
    return "\n".join(lines)



def _compute_chat_slot_patterns(db: Session, chat_id: int, r: Range) -> tuple[dict[str, int], dict[int, dict[str, int]]]:
    """Подсчитывает паттерны чата и пользователей по слотам."""
    sessions = _sessions_in_range(db, chat_id, r)
    if not sessions:
        return {"night": 0, "morning": 0, "afternoon": 0, "evening": 0}, {}
    
    session_ids = [int(s.session_id) for s in sessions]
    
    # Получаем все события чата
    events = db.scalars(
        select(PoopEvent).where(
            PoopEvent.session_id.in_(session_ids),
            PoopEvent.origin_chat_id == chat_id
        )
    ).all()
    
    # Считаем общий паттерн чата
    chat_slot_counts = {"night": 0, "morning": 0, "afternoon": 0, "evening": 0}
    user_slot_counts: dict[int, dict[str, int]] = {}
    
    for ev in events:
        if ev.created_at:
            slot = get_time_slot(ev.created_at, "Europe/Minsk")
            chat_slot_counts[slot] = chat_slot_counts.get(slot, 0) + 1
            
            uid = int(ev.user_id)
            if uid not in user_slot_counts:
                user_slot_counts[uid] = {"night": 0, "morning": 0, "afternoon": 0, "evening": 0}
            user_slot_counts[uid][slot] = user_slot_counts[uid].get(slot, 0) + 1
    
    return chat_slot_counts, user_slot_counts


def _compute_global_slot_patterns(
    states: list[SessionUserState],
    events_map: dict[tuple[int, int], list[PoopEvent]],
    tz_name: str = "Europe/Minsk",
) -> dict[int, dict[str, int]]:
    """Подсчитывает глобальные паттерны по каноническим состояниям дня."""
    user_slot_counts: dict[int, dict[str, int]] = {}
    for st in states:
        uid = int(st.user_id)
        user_counts = user_slot_counts.setdefault(uid, _empty_slot_counts())
        for ev in events_map.get((int(st.session_id), uid), []):
            if ev.created_at:
                slot = get_time_slot(ev.created_at, tz_name)
                user_counts[slot] = user_counts.get(slot, 0) + 1
    return user_slot_counts


def _get_top_slot_users(user_slot_counts: dict[int, dict[str, int]], target_slot: str, limit: int = 1) -> list[tuple[int, int]]:
    """Возвращает топ пользователей по слоту."""
    ranked = [(uid, counts.get(target_slot, 0)) for uid, counts in user_slot_counts.items()]
    ranked.sort(key=lambda x: (-x[1], x[0]))
    return ranked[:limit]


def build_stats_text_chat(
    db: Session, chat_id: int, today: date, period: str, user_id: int | None = None
) -> str:
    is_bounded_period = period in {"today", "week", "month", "year"}
    bounded_range = period_to_range(today, period) if is_bounded_period else None

    if bounded_range is not None:
        r = bounded_range
    else:
        chat_start = _chat_start_date(db, chat_id)
        r = Range(chat_start, today) if chat_start is not None else Range(today, today)

    rows = _chat_origin_events_in_range(db, chat_id, r, user_id=user_id)
    if not rows:
        if chat_id > 0 and user_id is not None:
            return f"💬 В этой личке\nПериод: {period_label(period)} ({format_period(r)})\n\nПока нет данных."
        return f"👥 В этом чате\nПериод: {period_label(period)} ({format_period(r)})\n\nПока пусто."

    by_day: dict[date, int] = {}
    by_user: dict[int, int] = {}
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    for d, uid, _n, bristol, feeling in rows:
        uid_int = int(uid)
        by_day[d] = by_day.get(d, 0) + 1
        by_user[uid_int] = by_user.get(uid_int, 0) + 1
        b = _bristol_bucket(bristol)
        if b:
            br[b] += 1
        f = _feeling_emoji(feeling)
        if f:
            fe[f] += 1

    total_poops = len(rows)
    active_days = sorted(d for d, cnt in by_day.items() if cnt > 0)
    active_days_count = len(active_days)
    period_days = (r.end - r.start).days + 1
    peak_day = max(by_day.items(), key=lambda x: (x[1], x[0])) if by_day else None

    if chat_id > 0 and user_id is not None:
        days_any = active_days_count
        avg_per_day = (float(total_poops) / float(period_days)) if period_days > 0 else 0.0
        avg_per_active_day = (float(total_poops) / float(days_any)) if days_any > 0 else 0.0
        last_mark_date = active_days[-1] if active_days else None

        # In private-chat stats, keep streaks consistent with the same local-origin dataset
        # used for totals and active days in this block.
        best_streak_live = _best_streak_from_days(active_days)
        streak_val = _current_streak_from_days(active_days, today)

        private_session_ids = [int(s.session_id) for s in _sessions_in_range(db, chat_id, r)]
        slot_counts = _compute_slot_patterns(db, private_session_ids, user_id)
        pattern_title = _get_pattern_title(slot_counts, total_poops)

        mass_g, water_l, water_gal = estimate_waste_metrics(total_poops)
        lines = [
            "💬 В этой личке",
            f"Период: {period_label(period)} ({format_period(r)})",
            "",
            "Твои итоги:",
            f"- Всего: 💩({total_poops})",
            f"- Дней с 💩: {days_any}/{period_days}",
            f"- Текущий стрик: {streak_val} дн.",
            f"- Лучший стрик: {best_streak_live} дн.",
            "",
        ]

        # Добавляем блок паттернов
        if total_poops > 0:
            lines.append("🕐 Твой ритм:")
            lines.extend(_format_slot_patterns(slot_counts))
            if pattern_title:
                lines.append("")
                lines.append(f"💡 Титул: «{pattern_title}»")
            lines.append("")

        lines.extend([
            "Твоя динамика:",
            f"- Среднее за календарный день: {avg_per_day:.2f}",
            f"- Среднее за день с отметкой: {avg_per_active_day:.2f}",
            (
                f"- Самый активный день: {peak_day[0].strftime('%d.%m.%y')} (💩({peak_day[1]}))"
                if peak_day
                else "- Самый активный день: нет данных"
            ),
            (
                f"- Последняя отметка: {last_mark_date.strftime('%d.%m.%y')}"
                if last_mark_date
                else "- Последняя отметка: нет данных"
            ),
            "",
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- Насрано примерно: {format_mass(mass_g)} (по 💩({int(total_poops)})).",
            f"- Воды на смыв: {format_water(water_l, water_gal)}.",
            "",
            "Примечание: в этой личке учитываются отметки, сделанные именно здесь.",
            "",
        ])
        lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
        lines.append("")
        lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))
        return "\n".join(lines)

    active_participants = len(by_user)
    avg_per_participant = (float(total_poops) / float(active_participants)) if active_participants > 0 else 0.0
    avg_per_active_day = (float(total_poops) / float(active_days_count)) if active_days_count > 0 else 0.0

    participant_rows = sorted(by_user.items(), key=lambda x: (-x[1], x[0]))
    top_rows = participant_rows[:5]
    top_user_ids = [uid for uid, _cnt in top_rows]

    streak_rank: list[tuple[int, int]] = []
    streaks_live = _compute_chat_user_streaks_live(db, [chat_id], today)
    for (cid, uid), days in streaks_live.items():
        if cid != chat_id or days <= 0:
            continue
        streak_rank.append((uid, days))
    streak_rank.sort(key=lambda x: (-x[1], x[0]))
    streak_top3 = streak_rank[:3]

    user_ids = sorted({uid for uid in by_user.keys()} | {uid for uid, _ in streak_top3})
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()} if user_ids else {}

    # Вычисляем паттерны чата
    chat_slot_counts, user_slot_counts = _compute_chat_slot_patterns(db, chat_id, r)
    chat_pattern_title = _get_pattern_title(chat_slot_counts, total_poops)

    lines = [
        "👥 В этом чате",
        f"Период: {period_label(period)} ({format_period(r)})",
        "",
        "Итоги:",
        f"- Всего: 💩({total_poops})",
        f"- Активных участников: {active_participants}",
        f"- Среднее на участника: {avg_per_participant:.2f}",
        f"- Дней с активностью: {active_days_count}/{period_days}",
        f"- Среднее в активный день: {avg_per_active_day:.2f}",
        (
            f"- Пиковый день: {peak_day[0].strftime('%d.%m.%y')} (💩({peak_day[1]}))"
            if peak_day is not None
            else "- Пиковый день: нет данных"
        ),
        "",
    ]

    # Добавляем блок паттернов чата
    if total_poops > 0:
        lines.append("🕐 Ритм чата:")
        lines.extend(_format_slot_patterns(chat_slot_counts))
        if chat_pattern_title:
            lines.append("")
            lines.append(f"💡 Чат — «{chat_pattern_title}»")
        lines.append("")

    lines.append("Топ-5 по количеству:")

    if top_rows:
        for idx, (uid, cnt) in enumerate(top_rows, start=1):
            user = users.get(uid)
            role = TOP5_ROLES[idx - 1] if idx - 1 < len(TOP5_ROLES) else "Участник рейтинга"
            
            # Добавляем разбивку по слотам
            user_slots = user_slot_counts.get(uid, {"night": 0, "morning": 0, "afternoon": 0, "evening": 0})
            slot_parts = []
            for slot in ["night", "morning", "afternoon", "evening"]:
                count = user_slots.get(slot, 0)
                if count > 0:
                    emoji = get_slot_emoji(slot)
                    slot_parts.append(f"{emoji}{count}")
            
            slot_str = " " + " ".join(slot_parts) if slot_parts else ""
            lines.append(f"- {idx}) {role} — {_display_name(user, uid)} • 💩({cnt}){slot_str}")
    else:
        lines.append("- пока никого в рейтинге")

    global_king = _current_global_king(db, today)
    if global_king is not None:
        king_uid, _king_total = global_king
        if _is_user_participant_in_chat(db, chat_id, king_uid):
            king_user = users.get(king_uid) or db.get(User, king_uid)
            lines.extend(["", f"👑 Титул чата: {TOP5_ROLES[0]} — {_display_name(king_user, king_uid)}"])

    lines.append("")
    lines.append("Топ-3 по стрику:")
    if streak_top3:
        for idx, (uid, days) in enumerate(streak_top3, start=1):
            user = users.get(uid)
            lines.append(f"- {idx}) {_streak_nickname(days)} — {_display_name(user, uid)} ({days} дн.)")
    else:
        lines.append("- пока нет активных стриков")

    mass_g, water_l, water_gal = estimate_waste_metrics(total_poops)
    lines.extend(
        [
            "",
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- Насрано примерно: {format_mass(mass_g)} (по 💩({int(total_poops)})).",
            f"- Воды на смыв: {format_water(water_l, water_gal)}.",
        ]
    )
    lines.extend(["", "По участникам (оценка):"])
    for uid, cnt in participant_rows:
        user = users.get(uid)
        p_mass_g, p_water_l, p_water_gal = estimate_waste_metrics(cnt)
        lines.append(
            f"- {_display_name(user, uid)}: 💩({cnt}) • насрал примерно {format_mass(p_mass_g)} • воды на смыв {format_water(p_water_l, p_water_gal)}"
        )

    lines.append("")
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))
    lines.append("")
    lines.append("Примечание: количество и распределения считаются по отметкам, сделанным именно в этом чате; стрики — по дневной активности именно в этом чате.")
    return "\n".join(lines)
def build_stats_text_global(db: Session, user_id: int, today: date, period: str) -> str:
    if period in {"today", "week", "month", "year"}:
        r = period_to_range(today, period)
    else:
        bot_start = _bot_start_date(db)
        r = Range(bot_start, today) if bot_start is not None else Range(today, today)

    sessions = _sessions_in_range(db, None, r)
    if not sessions:
        return f"🌍 Глобальная статистика\nПериод: {period_label(period)} ({format_period(r)})\n\nПока пусто."

    session_ids = [s.session_id for s in sessions]
    per_user_total = _per_user_totals_dedup(db, r)

    users_count = len(per_user_total)
    total_poops = sum(per_user_total.values())
    avg_per_user = (float(total_poops) / float(users_count)) if users_count > 0 else 0.0

    ranking_rows = sorted(per_user_total.items(), key=lambda x: (-x[1], x[0]))
    totals = [poops for _, poops in ranking_rows]
    my_total = per_user_total.get(user_id, 0)
    my_rank = next((idx for idx, (uid, _) in enumerate(ranking_rows, start=1) if uid == user_id), None)
    above_pct = _calc_above_percent(my_total, totals) if my_rank is not None else None
    top5 = [(TOP5_ROLES[i], poops) for i, (_uid, poops) in enumerate(ranking_rows[:5])]

    projected_streaks_by_user: dict[int, int] = {
        int(uid): _compute_user_global_streak_live(db, int(uid), today)
        for uid in per_user_total.keys()
    }

    states_pos = db.scalars(
        select(SessionUserState).where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.poops_n > 0,
        )
    ).all()
    session_date_by_id = {int(s.session_id): s.session_date for s in sessions}
    canonical_state_by_user_day: dict[tuple[int, date], SessionUserState] = {}
    for st in states_pos:
        sid = int(st.session_id)
        d = session_date_by_id.get(sid)
        if d is None:
            continue
        key = (int(st.user_id), d)
        curr = canonical_state_by_user_day.get(key)
        if curr is None:
            canonical_state_by_user_day[key] = st
            continue
        curr_key = (int(curr.poops_n or 0), int(curr.session_id))
        new_key = (int(st.poops_n or 0), int(st.session_id))
        if new_key > curr_key:
            canonical_state_by_user_day[key] = st
    events_map = _collect_events_map(db, session_ids)
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    user_br_scores: dict[int, list[int]] = {}
    user_fe_scores: dict[int, list[int]] = {}

    for st in canonical_state_by_user_day.values():
        uid = int(st.user_id)
        for bristol, feeling in _iter_effective_events(st, events_map):
            b = _bristol_bucket(bristol)
            if b:
                br[b] += 1
            f = _feeling_emoji(feeling)
            if f:
                fe[f] += 1

            bs = _bristol_score(bristol)
            if bs is not None:
                user_br_scores.setdefault(uid, []).append(bs)
            fs = _feeling_score(feeling)
            if fs is not None:
                user_fe_scores.setdefault(uid, []).append(fs)

    br_map = {uid: (sum(vals) / len(vals)) for uid, vals in user_br_scores.items() if vals}
    fe_map = {uid: (sum(vals) / len(vals)) for uid, vals in user_fe_scores.items() if vals}

    my_br_avg = br_map.get(user_id)
    my_fe_avg = fe_map.get(user_id)
    my_br_icon = _bristol_from_avg(my_br_avg)
    my_fe_icon = _feeling_from_avg(my_fe_avg)

    my_br_pct = (
        _calc_above_percent(int(round(my_br_avg * 1000)), [int(round(v * 1000)) for v in br_map.values()])
        if my_br_avg is not None
        else None
    )
    my_fe_pct = (
        _calc_above_percent(int(round(my_fe_avg * 1000)), [int(round(v * 1000)) for v in fe_map.values()])
        if my_fe_avg is not None
        else None
    )

    me = db.get(User, user_id)
    me_name = _display_name(me, user_id)

    # Вычисляем глобальные паттерны для титулов
    user_slot_counts = _compute_global_slot_patterns(list(canonical_state_by_user_day.values()), events_map)
    
    # Считаем общее по слотам
    total_slot_counts = {"night": 0, "morning": 0, "afternoon": 0, "evening": 0}
    for counts in user_slot_counts.values():
        for slot, count in counts.items():
            total_slot_counts[slot] += count

    lines = [
        "🌍 Глобальная статистика",
        f"Период: {period_label(period)} ({format_period(r)})",
        "",
        "Итоги:",
        f"- Участников: {int(users_count)}",
        f"- Всего: 💩({int(total_poops)})",
        f"- 💩 на 1 участника: {avg_per_user:.2f}",
        "",
    ]

    # Добавляем блок паттернов по всем
    slot_total = sum(total_slot_counts.values())
    if slot_total > 0:
        lines.append("🕐 Когда чаще ходят (все участники):")
        labels = [
            ("night", "🌙 Ночь (00–06)"),
            ("morning", "🌅 Утро (06–12)"),
            ("afternoon", "☀️ День (12–18)"),
            ("evening", "🌆 Вечер (18–24)"),
        ]
        peak_slot = max(total_slot_counts.keys(), key=lambda s: total_slot_counts.get(s, 0))
        for slot, label in labels:
            count = total_slot_counts.get(slot, 0)
            pct = (count / slot_total * 100) if slot_total > 0 else 0
            peak_marker = " ← Пик!" if slot == peak_slot and count > 0 else ""
            lines.append(f"{label}:   {count} раз ({pct:.0f}%){peak_marker}")
        lines.append("")

    # Добавляем титулы месяца (обезличенные)
    if user_slot_counts:
        lines.append("🏆 Титулы месяца:")
        slot_labels = [
            ("night", "Ночной серун", "ночных"),
            ("morning", "Утренний просер", "утренних"),
            ("afternoon", "Дневной навальщик", "дневных"),
            ("evening", "Вечерний сливатор", "вечерних"),
        ]
        for slot, title, adj in slot_labels:
            top_users = _get_top_slot_users(user_slot_counts, slot, limit=10)
            if top_users and top_users[0][1] > 0:
                # Находим место текущего пользователя
                user_rank = next((i + 1 for i, (uid, _) in enumerate(top_users) if uid == user_id), None)
                _top_uid, top_count = top_users[0]
                my_count = next((count for uid, count in top_users if uid == user_id), 0)
                
                if user_rank == 1:
                    lines.append(f"{get_slot_emoji(slot)} {title} — ТЫ — {top_count} {adj} походов 👑")
                else:
                    rank_str = f"#{user_rank}" if user_rank else f"#{len(top_users) + 1}+"
                    count_for_line = my_count if user_rank else top_count
                    lines.append(f"{get_slot_emoji(slot)} {title} — {rank_str} — {count_for_line} {adj} походов")
        lines.append("")

    lines.extend([
        "Топ-5:",
    ])

    if top5:
        for role, poops in top5:
            lines.append(f"- {role} — 💩({poops})")
    else:
        lines.append("- пока нет данных")

    mass_g, water_l, water_gal = estimate_waste_metrics(total_poops)
    lines.extend(
        [
            "",
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- Насрано примерно: {format_mass(mass_g)} (по 💩({int(total_poops)})).",
            f"- Воды на смыв: {format_water(water_l, water_gal)}.",
        ]
    )
    if ranking_rows:
        king_uid, king_total = ranking_rows[0]
        king_mass_g, king_water_l, king_water_gal = estimate_waste_metrics(king_total)
        lines.append(
            f"- {TOP5_ROLES[0]}: 💩({int(king_total)}) • насрал примерно {format_mass(king_mass_g)} • воды на смыв {format_water(king_water_l, king_water_gal)}"
        )

    lines.extend(["", "Лидеры глобальных стриков:"])
    top_streaks = sorted(
        [(uid, days) for uid, days in projected_streaks_by_user.items() if int(days) > 0],
        key=lambda x: (-x[1], x[0]),
    )[:3]
    if not top_streaks:
        lines.append("- пока нет данных")
    else:
        for idx, (_, days) in enumerate(top_streaks, start=1):
            lines.append(f"- #{idx} {_streak_nickname(int(days))} — {int(days)} дн.")

    lines.extend(["", "Твоя позиция:", f"- {me_name}"])
    if my_rank is None:
        lines.append("- пока не видно в глобальном рейтинге")
    else:
        lines.append(f"- Место: #{my_rank} из {len(ranking_rows)}")
        lines.append(f"- Всего: 💩({my_total})")
        if 1 <= my_rank <= len(TOP5_ROLES):
            lines.append(f"- Текущий титул: {TOP5_ROLES[my_rank - 1]}")
            if my_rank == 1:
                lines.append("- Ты сейчас держишь трон: 👑 Король какашек")
        if above_pct is not None:
            lines.append(f"- Выше {above_pct}% участников")

    lines.append("")
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))

    lines.extend(["", "Твои распределения:"])
    if my_br_pct is None or my_br_icon is None:
        lines.append("- Бристоль: нет данных")
    else:
        lines.append(f"- Бристоль: {my_br_icon} (выше {my_br_pct}%)")

    if my_fe_pct is None or my_fe_icon is None:
        lines.append("- Ощущения: нет данных")
    else:
        lines.append(f"- Ощущения: {my_fe_icon} (выше {my_fe_pct}%)")

    return "\n".join(lines)
