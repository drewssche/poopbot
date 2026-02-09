from app.db.engine import SessionMaker
from app.services.question1 import render_question1_empty
from app.services.timeutils import today_local_date, fmt_day
from app.services.mentions import mention_user
from app.services.render_q1 import render_q1, status_text, streak_text
from app.services.render_q2 import render_q2, status_q2_text
from app.services.q1_storage import (
    get_or_create_session,
    get_active_participants,
    get_q1_answers_map,
    calc_streak_for_user,
    get_q1_poop_user_ids,
)
from app.services.q2_storage import get_q2_answers_map


async def build_q1_text(chat_id: int) -> str:
    day = today_local_date()

    async with SessionMaker() as session:
        sess = await get_or_create_session(session, chat_id, day)
        participants = await get_active_participants(session, chat_id)

        if len(participants) == 0:
            return render_question1_empty(fmt_day(day))

        answers = await get_q1_answers_map(session, sess.id)

        lines = []
        for p in participants:
            ans = answers.get(p.user_id)
            answer_val = ans[0] if ans else None
            remind_at = ans[1] if ans else None

            streak_days, streak_start = await calc_streak_for_user(session, chat_id, p.user_id, day)

            m = mention_user(p.user_id, p.full_name, p.username)
            st = status_text(answer_val, remind_at)
            st_text = f"{st} • {streak_text(streak_days, streak_start)}"
            lines.append((m, st_text))

        return render_q1(day, lines)


async def build_q2_text(chat_id: int, sess_id: int) -> str:
    day = today_local_date()

    async with SessionMaker() as session:
        poop_ids = await get_q1_poop_user_ids(session, sess_id)
        if not poop_ids:
            return ""

        parts = await get_active_participants(session, chat_id)
        parts = [p for p in parts if p.user_id in poop_ids]

        answers = await get_q2_answers_map(session, sess_id)

        lines = []
        for p in parts:
            m = mention_user(p.user_id, p.full_name, p.username)
            st = status_q2_text(answers.get(p.user_id))
            lines.append((m, st))

        return render_q2(day, lines)
