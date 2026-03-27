from __future__ import annotations

from datetime import date, timedelta

from aiogram import Bot
from sqlalchemy import select

from app.db.models import ChatMember, User
from app.services.command_message_service import get_command_message_id, set_command_message_id
from app.services.q1_service import mention
from app.services.reminder_service import LATE_REMINDER_COMMAND
from app.services.scheduler_telegram import safe_send_message
from app.services.stats_common import (
    estimate_waste_metrics,
    format_mass,
    format_water,
    period_to_range,
    previous_period_range,
)
from app.services.stats_service import (
    _compute_chat_user_streaks_live,
    build_stats_text_chat,
    build_stats_text_global,
    build_stats_text_my,
    collect_among_chats_snapshot,
    compute_chat_period_metrics,
    estimate_waste_metrics,
    format_mass,
    format_water,
    period_label,
    period_to_range,
    previous_period_range,
    rank_chat_among_groups_by_total,
    _compute_chat_slot_patterns,
    _format_slot_patterns,
    _get_pattern_title,
)


def _is_last_day_of_month(d: date) -> bool:
    return (d + timedelta(days=1)).month != d.month


def build_periodic_report_text(db, chat_id: int, local_date: date, period: str, title: str) -> str:
    def _trend_line(label: str, curr: int, prev: int) -> str:
        delta = curr - prev
        if delta > 0:
            sign = "📈"
        elif delta < 0:
            sign = "📉"
        else:
            sign = "➖"
        if prev > 0:
            pct = (float(delta) / float(prev)) * 100.0
            return f"- {label}: {curr} ({sign} {delta:+d}, {pct:+.1f}% к прошлому периоду)"
        if curr > 0:
            return f"- {label}: {curr} (🆕 в прошлом периоде было 0)"
        return f"- {label}: {curr} (➖ без изменений)"

    def _rank_line(curr_rank: int | None, prev_rank: int | None, total_chats: int) -> str:
        if curr_rank is None or total_chats <= 0:
            return "- Место чата среди чатов: нет данных за период"
        if prev_rank is None:
            return f"- Место чата среди чатов: #{curr_rank} из {total_chats}"
        delta = prev_rank - curr_rank
        if delta > 0:
            trend = f"📈 +{delta}"
        elif delta < 0:
            trend = f"📉 {delta}"
        else:
            trend = "➖ 0"
        return f"- Место чата среди чатов: #{curr_rank} из {total_chats} ({trend} к прошлому периоду)"

    is_private = chat_id > 0
    report_user_id = None
    if is_private:
        report_user_id = db.scalar(
            select(ChatMember.user_id)
            .where(ChatMember.chat_id == chat_id)
            .order_by(ChatMember.joined_at.asc())
            .limit(1)
        )
        if report_user_id is None:
            report_user_id = chat_id
    curr_r = period_to_range(local_date, period)
    prev_r = previous_period_range(local_date, period)

    curr_metrics = compute_chat_period_metrics(db, chat_id, curr_r, user_id=report_user_id)
    prev_metrics = compute_chat_period_metrics(db, chat_id, prev_r, user_id=report_user_id)

    text = title + "\n\n" + build_stats_text_chat(db, chat_id, local_date, period, user_id=report_user_id)

    # Вычисляем паттерны чата за период.
    chat_slot_counts, _ = _compute_chat_slot_patterns(db, chat_id, curr_r)
    chat_pattern_title = _get_pattern_title(chat_slot_counts, curr_metrics.total_poops)

    # Добавляем блок паттернов после основного текста статистики.
    if curr_metrics.total_poops > 0:
        # Находим позицию после заголовка статистики
        insert_marker = "\n\nТенденция к прошлому периоду:"
        if insert_marker in text:
            pattern_block = "\n\n🕐 Ритм за период:\n"
            pattern_block += "\n".join(_format_slot_patterns(chat_slot_counts))
            if chat_pattern_title:
                pattern_block += f"\n\n💡 Титул периода: «{chat_pattern_title}»"
            pattern_block += insert_marker
            text = text.replace(insert_marker, pattern_block)

    trend_lines = ["Тенденция к прошлому периоду:"]
    trend_lines.append(_trend_line("Всего 💩", curr_metrics.total_poops, prev_metrics.total_poops))
    trend_lines.append(
        _trend_line(
            "Активных дней",
            curr_metrics.active_days_count,
            prev_metrics.active_days_count,
        )
    )
    if not is_private:
        trend_lines.append(
            _trend_line(
                "Активных участников",
                curr_metrics.active_participants,
                prev_metrics.active_participants,
            )
        )
        curr_rank, total_chats = rank_chat_among_groups_by_total(db, chat_id, curr_r)
        prev_rank, _ = rank_chat_among_groups_by_total(db, chat_id, prev_r)
        trend_lines.append(_rank_line(curr_rank, prev_rank, total_chats))

    text = text + "\n\n" + "\n".join(trend_lines)

    mass_g, water_l, water_gal = estimate_waste_metrics(curr_metrics.total_poops)
    if is_private:
        waste_lines = [
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- Насрано примерно: {format_mass(mass_g)} (по 💩({curr_metrics.total_poops})).",
            f"- Воды на смыв: {format_water(water_l, water_gal)}.",
        ]
    else:
        waste_lines = [
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- В этом чате насрано примерно: {format_mass(mass_g)} (по 💩({curr_metrics.total_poops})).",
            f"- Воды на смыв в этом чате: {format_water(water_l, water_gal)}.",
        ]
    text = text + "\n\n" + "\n".join(waste_lines)

    if not is_private:
        praise_block = _build_streak_praise_block(db, chat_id, local_date)
        if praise_block:
            text = text + "\n\n" + praise_block
    return text


async def send_periodic_stats(bot: Bot, db, chat_id: int, local_date: date) -> None:
    report_for_date = local_date - timedelta(days=1)

    def _already_sent(kind: str, anchor_date: date) -> bool:
        return get_command_message_id(db, chat_id, 0, kind, anchor_date) is not None

    async def _send(kind: str, period: str, title: str, anchor_date: date) -> None:
        if _already_sent(kind, anchor_date):
            return
        text = build_periodic_report_text(db, chat_id=chat_id, local_date=anchor_date, period=period, title=title)
        sent = await safe_send_message(bot, chat_id=chat_id, text=text)
        set_command_message_id(db, chat_id, 0, kind, anchor_date, sent.message_id)

    if report_for_date.weekday() == 6:
        await _send("weekly_stats", "week", "📉 Итоги недели", report_for_date)
    if _is_last_day_of_month(report_for_date):
        await _send("monthly_stats", "month", "📉 Итоги месяца", report_for_date)
    if report_for_date.month == 12 and report_for_date.day == 31:
        await _send("yearly_stats", "year", "📉 Итоги года", report_for_date)


def _streak_rank_label(days: int) -> str:
    if days >= 365:
        return "🌟 Легенда стрика"
    if days >= 180:
        return "👑 Полугодовой чемпион"
    if days >= 90:
        return "💪 Квартальный титан"
    if days >= 30:
        return "🏅 Месячный монолит"
    if days >= 7:
        return "🔥 Железная неделя"
    return "👏 Держит ритм"


def _build_streak_praise_block(db, chat_id: int, today: date) -> str | None:
    streaks_by_user = _compute_chat_user_streaks_live(db, [chat_id], today)
    rank = sorted(
        [(uid, days) for (cid, uid), days in streaks_by_user.items() if cid == chat_id and days > 0],
        key=lambda x: (-x[1], x[0]),
    )[:10]
    if not rank:
        return None
    users = {
        int(u.user_id): u
        for u in db.scalars(select(User).where(User.user_id.in_([uid for uid, _ in rank]))).all()
    }

    lines = ["👏 Кто держит стрик:"]
    for user_id, streak_days in rank:
        days = int(streak_days or 0)
        if days <= 0:
            continue
        user = users.get(int(user_id))
        if user is None:
            continue
        lines.append(f"- {_streak_rank_label(days)}: {mention(user)} — {days} дн.")

    return "\n".join(lines) if len(lines) > 1 else None


async def send_holiday_notice_if_needed(bot: Bot, db, chat_id: int, session_id: int, local_date: date) -> None:
    holiday_text = None
    if local_date.month == 2 and local_date.day == 9:
        holiday_text = "Сегодня Национальный день какашек (National Poop Day)."
    elif local_date.month == 11 and local_date.day == 19:
        holiday_text = "Сегодня Всемирный день туалета (World Toilet Day)."

    if holiday_text is None:
        return

    from app.services.repo_service import get_session_message_id

    q1_id = get_session_message_id(db, session_id, "Q1")
    q2_id = get_session_message_id(db, session_id, "Q2")
    q3_id = get_session_message_id(db, session_id, "Q3")
    if not (q1_id and q2_id and q3_id):
        return

    if get_command_message_id(db, chat_id, 0, "holiday_notice", local_date) is not None:
        return

    sent = await safe_send_message(bot, chat_id=chat_id, text=holiday_text)
    set_command_message_id(db, chat_id, 0, "holiday_notice", local_date, sent.message_id)
