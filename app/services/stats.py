import calendar
from datetime import date, timedelta

from sqlalchemy import select

from app.db.engine import SessionMaker
from app.db.models import Q1Answer, Q2Answer, DailySession
from app.services.mentions import mention_user
from app.services.render_q1 import streak_text
from app.services.timeutils import today_local_date
from app.services.q1_storage import get_active_participants, calc_streak_for_user


def period_range(kind: str, today: date) -> tuple[date, date, str]:
    if kind == "today":
        return today, today, f"📊 Статистика за сегодня ({today.strftime('%d.%m.%y')})"
    if kind == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end, f"📊 Статистика за неделю ({start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')})"
    if kind == "month":
        start = today.replace(day=1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        end = today.replace(day=last_day)
        return start, end, f"📊 Статистика за месяц ({start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')})"
    if kind == "year":
        start = date(today.year, 1, 1)
        end = date(today.year, 12, 31)
        return start, end, f"📊 Статистика за год ({start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')})"
    return today, today, f"📊 Статистика за сегодня ({today.strftime('%d.%m.%y')})"


async def build_stats_text(chat_id: int, kind: str) -> str:
    today = today_local_date()
    start, end, title = period_range(kind, today)

    async with SessionMaker() as session:
        participants = await get_active_participants(session, chat_id)

        if not participants:
            return (
                f"{title}\n\n"
                "Пока здесь никого нет в списке.\n"
                "Нажми любую кнопку в ежедневном опросе, чтобы добавиться."
            )

        q1q = (
            select(Q1Answer.user_id, Q1Answer.answer, DailySession.day)
            .join(DailySession, DailySession.id == Q1Answer.session_id)
            .where(
                Q1Answer.chat_id == chat_id,
                DailySession.chat_id == chat_id,
                DailySession.day >= start,
                DailySession.day <= end,
            )
        )
        q1res = await session.execute(q1q)
        q1_rows = q1res.all()

        poop_counts: dict[int, int] = {}
        no_counts: dict[int, int] = {}
        answered_users: set[int] = set()

        for user_id, ans, _day in q1_rows:
            answered_users.add(user_id)
            if ans == "poop":
                poop_counts[user_id] = poop_counts.get(user_id, 0) + 1
            elif ans == "no":
                no_counts[user_id] = no_counts.get(user_id, 0) + 1

        q2q = (
            select(Q2Answer.user_id, Q2Answer.answer, DailySession.day)
            .join(DailySession, DailySession.id == Q2Answer.session_id)
            .where(
                Q2Answer.chat_id == chat_id,
                DailySession.chat_id == chat_id,
                DailySession.day >= start,
                DailySession.day <= end,
            )
        )
        q2res = await session.execute(q2q)
        q2_rows = q2res.all()

        good: dict[int, int] = {}
        ok: dict[int, int] = {}
        bad: dict[int, int] = {}

        for user_id, ans, _day in q2_rows:
            if ans == "good":
                good[user_id] = good.get(user_id, 0) + 1
            elif ans == "ok":
                ok[user_id] = ok.get(user_id, 0) + 1
            elif ans == "bad":
                bad[user_id] = bad.get(user_id, 0) + 1

        total_poop = sum(poop_counts.values())
        total_no = sum(no_counts.values())
        total_good = sum(good.values())
        total_ok = sum(ok.values())
        total_bad = sum(bad.values())

        total_participants = len(participants)
        total_answered = len({p.user_id for p in participants if p.user_id in answered_users})

        lines = [
            title,
            "",
            "Итог по чату:",
            f"💩 {total_poop} • ❌ {total_no}",
            f"😇 {total_good} • 😐 {total_ok} • 😫 {total_bad}",
            f"Ответили: {total_answered}/{total_participants} участников",
            "",
            "Участники:",
        ]

        for p in participants:
            m = mention_user(p.user_id, p.full_name, p.username)

            poop_n = poop_counts.get(p.user_id, 0)
            no_n = no_counts.get(p.user_id, 0)

            g = good.get(p.user_id, 0)
            o = ok.get(p.user_id, 0)
            b = bad.get(p.user_id, 0)

            streak_days, streak_start = await calc_streak_for_user(session, chat_id, p.user_id, today)
            st = streak_text(streak_days, streak_start)

            lines.append(
                f"- {m} — 💩 {poop_n} • ❌ {no_n} | 😇 {g} • 😐 {o} • 😫 {b} | {st}"
            )

        return "\n".join(lines)
