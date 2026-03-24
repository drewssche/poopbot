# Тестирование Poopbot

## Быстрый старт

### 1. Запустить Docker

```bash
docker compose up -d
```

### 2. Запустить unit-тесты

```bash
docker compose exec bot python -m pytest tests/ -v
```

### 3. Запустить нагрузочный тест

```bash
docker compose exec -e PYTHONPATH=/app bot python /app/tests/load_test.py
```

---

## Типы тестов

### Unit-тесты (`tests/test_*.py`)

| Файл | Что тестирует |
|------|---------------|
| `test_stats_service.py` | Статистика, стрики, рейтинги |
| `test_scheduler_streak_recalc.py` | Пересчёт стриков (оптимизация) |
| `test_q1_restore_streak.py` | Восстановление стриков |
| `test_repo_service.py` | Репозитории, миграции чатов |
| `test_streak_restore_service.py` | Сервис восстановления стриков |
| `test_healthcheck.py` | Health check бота |
| `test_heartbeat_monitor.py` | Мониторинг пульса |

**Запуск:**
```bash
docker compose exec bot python -m pytest tests/ -v
```

---

### Нагрузочный тест (`tests/load_test.py`)

Проверяет производительность на данных, близких к продакшену:
- 10 чатов
- 118 пользователей  
- 365 дней истории

**Что измеряет:**
- Пересчёт стриков (00:06 daily)
- Batch vs N+1 запросы
- Рендеринг статистики

**Запуск:**
```bash
# Очистить БД и запустить с нуля
docker compose exec db psql -U poopbot -d poopbot -c "TRUNCATE TABLE poop_events, session_user_state, sessions, user_streaks, chat_members, users RESTART IDENTITY CASCADE;"
docker compose exec -e PYTHONPATH=/app bot python /app/tests/load_test.py
```

**Ожидаемые результаты:**
| Метрика | Ожидание |
|---------|----------|
| Пересчёт стриков | < 100ms |
| Batch-запрос стриков | < 50ms |
| Ускорение batch | > 5× |
| Рендеринг /stats | < 500ms |

---

### Интеграционные тесты (`tests/test_integration.py`)

Проверяют работу бота в реальном Telegram.

**Требования:**
- Токен бота
- Chat ID для тестов (группа или ЛС)

**Запуск:**
```bash
# Тесты в ЛС с ботом
docker compose exec -e PYTHONPATH=/app bot python /app/tests/test_integration.py \
  --bot-token YOUR_BOT_TOKEN \
  --chat-id YOUR_USER_ID

# Тесты в группе
docker compose exec -e PYTHONPATH=/app bot python /app/tests/test_integration.py \
  --bot-token YOUR_BOT_TOKEN \
  --chat-id -1001234567890 \
  --user-id YOUR_USER_ID
```

**Как получить chat_id:**
1. Добавьте бота `@devotestobot` в группу
2. Отправьте `/start`
3. Посмотрите логи: `docker compose logs bot | grep "chat_id"`

---

## Проверка оптимизаций

### 1. Логирование медленных запросов

После запуска бота проверьте логи:

```bash
docker compose logs bot | grep "Slow query"
```

Запросы медленнее 500ms будут залогированы.

### 2. Применение индексов

```bash
docker compose exec db psql -U poopbot -d poopbot -c "\di"
```

Должны быть видны:
- `ix_sessions_chat_date`
- `ix_poop_events_origin_session_user`
- `ix_session_user_state_session_poops`
- `ix_chat_members_user`

### 3. Размер пула соединений

```bash
docker compose exec bot python -c "from app.core.config import load_settings; s=load_settings(); print(f'DB_POOL_SIZE={s.db_pool_size}, MAX_OVERFLOW={s.db_max_overflow}')"
```

Ожидается: `DB_POOL_SIZE=10, MAX_OVERFLOW=10`

---

## Диагностика проблем

### Бот не запускается

```bash
# Проверить логи
docker compose logs bot --tail=100

# Проверить миграции
docker compose exec bot alembic current

# Проверить подключение к БД
docker compose exec bot python -c "from app.db.engine import make_engine; from app.core.config import load_settings; e=make_engine(load_settings().database_url); e.connect().close(); print('DB OK')"
```

### Тесты падают

```bash
# Запустить с подробным выводом
docker compose exec bot python -m pytest tests/ -v --tb=long

# Запустить конкретный тест
docker compose exec bot python -m pytest tests/test_scheduler_streak_recalc.py -v
```

### Медленные запросы

Включите логирование всех SQL (только для отладки!):

```python
# В app/db/engine.py измените порог
SLOW_QUERY_THRESHOLD_MS = 50  # вместо 500
```

---

## Добавление новых тестов

1. Создайте файл `tests/test_your_feature.py`
2. Используйте `unittest.TestCase` или `pytest`
3. Для БД используйте SQLite in-memory:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.base import Base

engine = create_engine("sqlite+pysqlite:///:memory:")
Base.metadata.create_all(engine)
db = Session(engine)
```

4. Запустите: `docker compose exec bot python -m pytest tests/test_your_feature.py -v`
