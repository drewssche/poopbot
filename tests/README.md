# Тестирование Poopbot

Дата актуализации: 2026-03-27

## Быстрый старт

### 1. Запустить Docker-окружение

```bash
docker compose up -d
```

### 2. Запустить тесты в Docker

```bash
docker compose exec bot python -m pytest tests/ -v
```

### 3. При необходимости запустить тесты локально из `.venv`

```bash
./.venv/bin/pytest -q tests
```

По умолчанию будут запущены обычные тесты, а `load_test.py` будет пропущен.

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
| `test_time_slots.py` | Временные слоты, титулы, popups |

**Запуск:**
```bash
docker compose exec bot python -m pytest tests/ -v
# или локально
./.venv/bin/pytest -q tests
```

---

### Нагрузочный тест (`tests/load_test.py`)

Проверяет производительность на данных, близких к продакшену:
- 10 чатов
- 118 пользователей  
- 365 дней истории

В обычный `pytest` не входит и помечен как `slow`.

**Что измеряет:**
- Пересчёт стриков (00:06 daily)
- Batch vs N+1 запросы
- Рендеринг статистики

**Запуск:**
```bash
# Docker/VPS-сценарий
docker compose exec db psql -U poopbot -d poopbot -c "TRUNCATE TABLE poop_events, session_user_state, sessions, user_streaks, chat_members, users RESTART IDENTITY CASCADE;"
docker compose exec -e PYTHONPATH=/app bot python /app/tests/load_test.py

# Локально через pytest
RUN_LOAD_TESTS=1 ./.venv/bin/pytest -q tests/load_test.py
```

**Ожидаемые результаты:**
| Метрика | Ожидание |
|---------|----------|
| Пересчёт стриков | < 100ms |
| Batch-запрос стриков | < 50ms |
| Ускорение batch | > 5× |
| Рендеринг /stats | < 500ms |

---

### Интеграционные Telegram-тесты

Исторические файлы `tests/test_integration.py` и `tests/test_integration_auto.py` удалены как неподдерживаемые.

Если понадобятся реальные Telegram e2e-проверки, их лучше добавлять заново как отдельный opt-in suite, а не как часть обычного `pytest`.

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

Ожидается: `DB_POOL_SIZE=6, MAX_OVERFLOW=4`

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
# Docker/VPS
docker compose exec bot python -m pytest tests/ -v --tb=long

# Локально с подробным выводом
./.venv/bin/pytest -q tests -vv --tb=long

# Запустить конкретный тест
./.venv/bin/pytest -q tests/test_scheduler_streak_recalc.py -vv
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

engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
Base.metadata.create_all(engine)
db = Session(engine)
```

4. Запустите:

```bash
./.venv/bin/pytest -q tests/test_your_feature.py
```
