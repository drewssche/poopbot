# Performance Audit for 1 vCPU / 2 GB RAM VPS

Date: local workspace audit

## Scope

- `docker-compose.yml`
- `Dockerfile`
- runtime loops and scheduler
- DB engine and pool settings
- background workers/processes
- startup path and restart cost

## Current architecture summary

- One `bot` container, one `postgres` container.
- No Redis, no queue broker, no Celery/RQ workers.
- Single Python process for the bot.
- Bot currently serves about 130 users with growth around 1 user per week.
- DB migrations run on every bot start before the main process.
- Main background activity comes from:
  - APScheduler tick that scans all enabled chats
  - heartbeat loop
  - heartbeat file loop
  - webhook guard loop
  - polling guard loop
  - polling connectivity guard loop
  - handled-rate logging loop

## Findings

### 1. Scheduler scanned all enabled chats every 30 seconds

File: `app/services/scheduler_service.py`

Impact:

- Constant DB wakeups even when no meaningful minute-level work is due.
- Wasteful on a weak VPS, especially with the current installed base of about 130 users and continued slow growth.

Risk level: low

Action taken:

- Made scheduler tick interval configurable.
- Defaulted runtime config to 60 seconds, which still matches minute-based business logic.

### 2. SQLAlchemy used default QueuePool sizing

Files:

- `app/db/engine.py`
- `app/core/config.py`
- `app/bot/dispatcher.py`

Impact:

- Default SQLAlchemy pool can keep more DB connections than needed for a single-process bot.
- Extra idle Postgres backends consume RAM on a small VPS.

Risk level: low

Action taken:

- Added explicit pool settings:
  - `DB_POOL_SIZE=2`
  - `DB_MAX_OVERFLOW=0`
  - `DB_POOL_TIMEOUT_SEC=10`
  - `DB_POOL_RECYCLE_SEC=1800`
  - `pool_use_lifo=True`

Expected effect:

- Lower idle connection count and lower steady RAM use in Postgres.

### 3. Extra metrics logging loop was always on

File: `app/bot/dispatcher.py`

Impact:

- Periodic wakeups and log writes with little operational value on a low-resource VPS.

Risk level: low

Action taken:

- Made handled-rate loop optional.
- Set `HANDLED_RATE_LOG_INTERVAL_SEC=0` in `.env` to disable it.

### 4. Service guard intervals were relatively aggressive

Files:

- `app/core/config.py`
- `.env`

Impact:

- More frequent timer wakeups and network/API checks than necessary.
- Small but constant background CPU usage.

Risk level: low

Action taken:

- Increased intervals in `.env`:
  - `HEARTBEAT_STALE_SEC=600`
  - `WEBHOOK_GUARD_INTERVAL_SEC=600`
  - `POLLING_GUARD_INTERVAL_SEC=180`
  - `SCHEDULER_TICK_INTERVAL_SEC=60`
- Kept guards enabled to avoid changing runtime semantics too much.

### 5. Postgres `max_connections` was oversized for this deployment

File: `docker-compose.yml`

Impact:

- Higher possible backend memory footprint with no benefit in a single-bot setup.
- For the current scale, `20` connections still leaves enough headroom while cutting idle backend cost.

Risk level: low

Action taken:

- Reduced `max_connections` from `50` to `20`.

### 6. Healthchecks were more frequent than necessary

File: `docker-compose.yml`

Impact:

- Unneeded repeated process wakeups and checks.

Risk level: low

Action taken:

- DB healthcheck interval changed from `5s` to `10s`.
- DB retries reduced from `20` to `10`.
- Bot healthcheck interval changed from `30s` to `60s`.

### 7. Container start command used a shell without `exec`

File: `docker-compose.yml`

Impact:

- Extra shell process remains as PID 1 wrapper.
- Worse signal handling and slightly more process overhead.

Risk level: low

Action taken:

- Changed bot command to:
  - `sh -c "alembic upgrade head && exec python -m app.main"`

### 8. Docker image installed `build-essential` although runtime does not obviously need it

File: `Dockerfile`

Impact:

- Larger image and more packages than required.
- Does not directly reduce runtime RAM much, but simplifies and lightens the container.

Risk level: low to medium

Action taken:

- Removed `build-essential` installation.

Note:

- This assumes current dependencies continue installing from wheels or pure Python packages.
- I could not run a full image rebuild here, so this should be validated on the target host.

### 9. Log level was set to `DEBUG` in production env

File: `.env`

Impact:

- More log formatting, more disk writes, and noisier runtime.

Risk level: low

Action taken:

- Changed `LOG_LEVEL=INFO`.

## What was checked and not found

- No multiple worker processes like Gunicorn/Uvicorn workers.
- No Redis.
- No task queues.
- No cron sidecars.
- No obvious tight busy loops; loops are sleep-based.
- No obvious API hammering besides service guards and scheduler wakeups.

## Heavy restart operations

### Alembic on each container restart

File: `docker-compose.yml`

Observation:

- `alembic upgrade head` runs on every bot start.

Assessment:

- Usually acceptable and operationally safe.
- Cost is mostly startup latency, not steady-state RAM.
- I did not change this because disabling or splitting migration execution is more operationally sensitive.

## Files changed

- `docker-compose.yml`
- `Dockerfile`
- `app/core/config.py`
- `app/db/engine.py`
- `app/bot/dispatcher.py`
- `app/services/scheduler_service.py`
- `.env`
- `pytest.ini`

## Verification status

- Static audit completed.
- Local `.venv` created.
- Dependencies installed from `requirements.txt`.
- `pytest` installed into local `.venv`.
- Test run result: `3 passed` in `tests/test_stats_service.py`.

## Server `.env` provided by user

The server environment you shared is still on the older, more expensive runtime profile:

- `HEARTBEAT_STALE_SEC=300`
- `WEBHOOK_GUARD_INTERVAL_SEC=180`
- `HANDLED_RATE_LOG_INTERVAL_SEC=300`
- `POLLING_GUARD_INTERVAL_SEC=60`
- `LOG_LEVEL=DEBUG`
- no explicit DB pool limits
- no explicit scheduler tick interval

If you want the low-resource profile from this audit on the VPS, those values should be updated on the server too, not only locally.

## What was problematic

1. Scheduler tick was too frequent for minute-level logic.
2. DB pool and Postgres connection limits were too loose for a single small bot.
3. Production logging was noisier than necessary.
4. Service guard loops and healthchecks were more frequent than required.
5. Extra handled-rate loop consumed periodic CPU/logging with limited value.

For the current load of about 130 users, these are still low-risk optimizations. Nothing in the audit indicates an immediate need for Redis, queues, or process sharding yet.

## What was changed

1. Added small explicit SQLAlchemy pool limits and lifo reuse.
2. Made scheduler tick configurable and set it to 60 seconds.
3. Disabled handled-rate periodic logging.
4. Relaxed guard and healthcheck intervals.
5. Reduced Postgres `max_connections` to 20.
6. Switched bot startup command to use `exec`.
7. Removed `build-essential` from the image.
8. Switched production log level from `DEBUG` to `INFO`.

## What to check on the server

Run these after deploy:

```bash
docker compose ps
docker stats --no-stream
docker compose logs --tail=200 bot
docker compose logs --tail=100 db
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "show max_connections;"
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select state, count(*) from pg_stat_activity group by state order by state;"
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select now(), count(*) filter (where state = 'active') as active, count(*) filter (where state = 'idle') as idle from pg_stat_activity;"
docker compose exec bot sh -c "ps -o pid,ppid,comm,rss,%cpu -ax"
docker compose exec bot sh -c "grep -E 'VmRSS|VmSize' /proc/1/status"
docker compose exec bot sh -c "python - <<'PY'\nfrom app.core.config import load_settings\ns = load_settings()\nprint('pool_size', s.db_pool_size)\nprint('max_overflow', s.db_max_overflow)\nprint('scheduler_tick_interval_sec', s.scheduler_tick_interval_sec)\nprint('handled_rate_log_interval_sec', s.handled_rate_log_interval_sec)\nPY"
```

Also watch:

- steady bot RSS after 10-15 minutes
- Postgres RSS after warmup
- count of idle DB sessions
- restart duration caused by `alembic upgrade head`
- whether Docker image still builds cleanly after removing compiler packages
- scheduler runtime if active chats grow noticeably beyond the current user base
