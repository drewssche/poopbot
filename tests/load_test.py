#!/usr/bin/env python3
"""
Нагрузочный тест для Poopbot

Заполняет БД тестовыми данными и проверяет производительность оптимизаций.
Масштаб: 10 чатов × 118 пользователей × 365 дней истории
"""
import time
from datetime import date, timedelta, time as dt_time
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Chat, User, ChatMember, Session as DaySession, SessionUserState, PoopEvent, UserStreak
from app.db.engine import make_engine
from app.services.scheduler_service import _recalculate_streaks_from_history
from app.services.stats_streaks import compute_chat_user_streaks_live, compute_user_chat_streak_live
from app.services.stats_service import build_stats_text_chat, build_stats_text_my

# Масштаб данных (как на проде)
NUM_CHATS = 10
NUM_USERS = 118
DAYS_HISTORY = 365

DATABASE_URL = "postgresql+psycopg://poopbot:super_strong_password@db:5432/poopbot"


def create_test_data(
    db: Session,
    num_chats: int = NUM_CHATS,
    num_users: int = NUM_USERS,
    days_history: int = DAYS_HISTORY,
) -> None:
    """Создаёт тестовые данные, похожие на продакшен."""
    print(f"Создание тестовых данных: {num_chats} чатов × {num_users} пользователей × {days_history} дней...")
    
    import random
    start_date = date.today() - timedelta(days=days_history)
    
    # Создаём чаты
    chat_ids = []
    for chat_idx in range(num_chats):
        chat_id = -1000 - chat_idx
        chat = Chat(
            chat_id=chat_id,
            timezone="Europe/Minsk",
            post_time=dt_time(10, 0),
            is_enabled=True,
            notifications_enabled=True,
            q2_q3_enabled=True,
            late_reminder_enabled=True,
        )
        db.add(chat)
        chat_ids.append(chat_id)
    
    db.flush()
    
    # Создаём пользователей
    user_ids = []
    for user_idx in range(num_users):
        user_id = 100000 + user_idx
        user = User(
            user_id=user_id,
            username=f"testuser{user_idx}",
            first_name=f"Test{user_idx}",
            last_name="User",
        )
        db.add(user)
        user_ids.append(user_id)
    
    db.flush()
    
    # Распределяем пользователей по чатам (каждый в 1-3 чата)
    total_memberships = 0
    for user_id in user_ids:
        # Каждый пользователь в 1-3 чатах
        num_chats_for_user = random.randint(1, 3)
        user_chats = random.sample(chat_ids, k=num_chats_for_user)
        for chat_id in user_chats:
            db.add(ChatMember(chat_id=chat_id, user_id=user_id))
            db.add(UserStreak(chat_id=chat_id, user_id=user_id, current_streak=0, last_poop_date=None))
            total_memberships += 1
    
    db.flush()
    print(f"   Членств в чатах: {total_memberships}")
    
    # Создаём сессии и события
    total_events = 0
    active_rate = 0.25  # 25% пользователей активны в день
    
    for chat_idx, chat_id in enumerate(chat_ids):
        for day_offset in range(days_history):
            session_date = start_date + timedelta(days=day_offset)
            
            sess = DaySession(
                chat_id=chat_id,
                session_date=session_date,
                status="closed" if day_offset < days_history - 1 else "active",
            )
            db.add(sess)
            db.flush()
            
            # Получаем пользователей этого чата
            chat_members = list(db.scalars(
                select(ChatMember.user_id).where(ChatMember.chat_id == chat_id)
            ).all())
            
            if not chat_members:
                continue
            
            # Активные пользователи дня
            random.seed(day_offset + chat_idx)
            active_users = random.sample(
                chat_members, 
                k=max(1, int(len(chat_members) * active_rate))
            )
            
            for user_id in active_users:
                poops_n = random.randint(1, 3)
                db.add(SessionUserState(
                    session_id=sess.session_id,
                    user_id=user_id,
                    poops_n=poops_n,
                ))
                
                for event_n in range(1, poops_n + 1):
                    db.add(PoopEvent(
                        session_id=sess.session_id,
                        user_id=user_id,
                        event_n=event_n,
                        origin_chat_id=chat_id,
                        bristol=random.randint(3, 4),
                        feeling=random.choice(["great", "ok", "bad"]),
                    ))
                    total_events += 1
    
    db.commit()
    print(f"   Создано PoopEvent: {total_events}")
    print(f"   Дней истории: {days_history}")


def test_streak_recalc_performance(db: Session, chat_id: int, today: date) -> None:
    """Тест производительности пересчёта стриков."""
    print(f"\n📊 Тест пересчёта стриков для chat_id={chat_id}...")
    
    start = time.perf_counter()
    _recalculate_streaks_from_history(db, chat_id, today)
    elapsed = time.perf_counter() - start
    
    print(f"   ⏱️  Время пересчёта: {elapsed*1000:.1f}ms")
    
    if elapsed > 1.0:
        print(f"   ⚠️  ПРЕДУПРЕЖДЕНИЕ: пересчёт занял >1с (ожидалось <100ms)")
    else:
        print(f"   ✅ OK: пересчёт быстрый")
    
    db.rollback()  # Не сохраняем изменения


def test_batch_streak_query(db: Session, chat_id: int, today: date) -> None:
    """Тест производительности batch-запроса стриков."""
    print(f"\n📊 Тест batch-запроса стриков для chat_id={chat_id}...")
    
    user_ids = list(db.scalars(
        select(ChatMember.user_id).where(ChatMember.chat_id == chat_id).limit(50)
    ).all())
    
    start = time.perf_counter()
    for user_id in user_ids:
        compute_user_chat_streak_live(db, chat_id, user_id, today)
    elapsed_old = time.perf_counter() - start
    
    start = time.perf_counter()
    compute_chat_user_streaks_live(db, [chat_id], today)
    elapsed_new = time.perf_counter() - start
    
    print(f"   ⏱️  По одному запросу (N+1): {elapsed_old*1000:.1f}ms")
    print(f"   ⏱️  Batch-запрос: {elapsed_new*1000:.1f}ms")
    
    if elapsed_old > 0:
        speedup = elapsed_old / max(0.001, elapsed_new)
        print(f"   🚀 Ускорение: {speedup:.1f}×")


def test_stats_rendering(db: Session, chat_id: int, user_id: int, today: date) -> None:
    """Тест производительности рендеринга статистики."""
    print(f"\n📊 Тест рендеринга статистики для user_id={user_id}...")
    
    # Моя статистика
    start = time.perf_counter()
    try:
        stats_my = build_stats_text_my(db, chat_id, user_id, today, "month")
        elapsed = time.perf_counter() - start
        print(f"   /stats my (месяц): {elapsed*1000:.1f}ms")
    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"   /stats my (месяц): {elapsed*1000:.1f}ms — ошибка: {e}")
    
    # Статистика чата
    start = time.perf_counter()
    try:
        stats_chat = build_stats_text_chat(db, chat_id, None, today, "month")
        elapsed = time.perf_counter() - start
        print(f"   /stats chat (месяц): {elapsed*1000:.1f}ms")
    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"   /stats chat (месяц): {elapsed*1000:.1f}ms — ошибка: {e}")


def main():
    print("🧪 Нагрузочный тест Poopbot")
    print("=" * 60)
    print(f"Масштаб: {NUM_CHATS} чатов × {NUM_USERS} пользователей × {DAYS_HISTORY} дней")
    print("=" * 60)
    
    engine = make_engine(DATABASE_URL, pool_size=6, max_overflow=4)
    
    try:
        with engine.connect() as conn:
            conn.execute(select(func.now()))
        print("✅ Подключение к БД успешно")
    except Exception as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        print("   Убедитесь, что Docker запущен: docker compose up -d db")
        return 1
    
    Base.metadata.create_all(engine)
    
    with Session(engine) as db:
        today = date.today()
        
        existing_chats = db.scalars(select(Chat.chat_id).where(Chat.chat_id < 0)).all()
        
        if not existing_chats:
            print("\n📝 БД пустая. Создаю тестовые данные...")
            create_test_data(db)
            print("✅ Тестовые данные созданы")
        
        existing_chats = db.scalars(select(Chat.chat_id).where(Chat.chat_id < 0)).all()
        existing_users = db.scalars(select(User.user_id).where(User.user_id >= 100000)).all()
        
        if existing_chats and existing_users:
            chat_id = existing_chats[0]
            user_id = existing_users[0]
            
            # Тесты производительности
            test_streak_recalc_performance(db, chat_id, today)
            test_batch_streak_query(db, chat_id, today)
            test_stats_rendering(db, chat_id, user_id, today)
        
        print("\n" + "=" * 60)
        print("✅ Нагрузочный тест завершён")
    
    return 0


if __name__ == "__main__":
    exit(main())
