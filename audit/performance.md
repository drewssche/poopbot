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

### 2. SQLAlchemy pool needed explicit right-sizing for real production concurrency

Files:

- `app/db/engine.py`
- `app/core/config.py`
- `app/bot/dispatcher.py`

Impact:

- Without explicit limits, SQLAlchemy can keep more connections than needed.
- But in real production, the first aggressive low-RAM profile `pool_size=2` / `max_overflow=0` also turned out too tight:
  - scheduler
  - polling handlers
  - callback/command flows
  could contend and hit `QueuePool limit ... timed out`.

Risk level: low

Action taken:

- Added explicit pool settings, then adjusted them after observing real contention:
  - `DB_POOL_SIZE=4`
  - `DB_MAX_OVERFLOW=2`
  - `DB_POOL_TIMEOUT_SEC=10`
  - `DB_POOL_RECYCLE_SEC=1800`
  - `pool_use_lifo=True`

Expected effect:

- Still modest DB footprint on a 2 GB VPS, but enough headroom to avoid handler starvation under normal concurrent bot activity.

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

### 10. Scheduler loaded full `Chat` rows and then loaded the same chat again

File: `app/services/scheduler_service.py`

Impact:

- Each scheduler tick first loaded full `Chat` ORM objects for all enabled chats.
- `_process_chat()` then opened a new session and loaded the same chat again with `db.get(Chat, chat_id)`.
- For a single bot this is not catastrophic, but it is unnecessary DB and ORM work on every tick.

Risk level: low

Action taken:

- Changed the tick scan to load only enabled `chat_id` values.
- Left the per-chat `db.get()` inside `_process_chat()` intact, because that is the correct fresh read for current chat settings.

### 11. Some hot render paths loaded more rows than they needed

Files:

- `app/services/q1_service.py`
- `app/services/q2_q3_service.py`

Impact:

- Q1/Q2/Q3 renderers are in the hot path for daily posts, updates after button presses, and help/debug refresh flows.
- They previously loaded:
  - full `ChatMember` ORM rows when only `user_id` ordering was needed
  - all `SessionUserState` rows for a session, even if only current chat members mattered

Risk level: low

Action taken:

- Switched member reads to `ChatMember.user_id` only.
- Restricted `SessionUserState` queries to the member ids of the current chat.

Expected effect:

- Lower row materialization cost and less wasted Python/ORM work during Q1/Q2/Q3 rendering.

### 12. Scheduler checked member count with `count(*)` where only existence mattered

File: `app/services/scheduler_service.py`

Impact:

- `_post_q1()` only needed a boolean `has_any_members`, but queried full `count(*)`.
- On a weak VPS this is a small but unnecessary cost on an often-used path.

Risk level: low

Action taken:

- Replaced `count(*)` with `select(ChatMember.user_id).limit(1)` and boolean conversion.

### 13. Stats paths loaded zero-value session states that were not used

File: `app/services/stats_service.py`

Impact:

- "My" and global stats paths loaded `SessionUserState` rows with `poops_n = 0`.
- These rows do not affect totals, active days, or effective event distributions, but still cost query bandwidth and ORM work.

Risk level: low

Action taken:

- Restricted those reads to `SessionUserState.poops_n > 0`.

### 14. Recap paths did repeat holiday-day count queries that were already implied by loaded yearly events

File: `app/services/recap_cards.py`

Impact:

- Personal yearly recap and chat yearly recap already loaded yearly event rows and built per-day totals.
- They then made extra count queries for February 9 and November 19.

Risk level: low

Action taken:

- Reused `day_totals` from the already-loaded yearly dataset for those holiday counters.

Expected effect:

- Fewer redundant DB round-trips on yearly recap generation.

### 15. Polling self-recovery check used the wrong interval source

File: `app/bot/dispatcher.py`

Impact:

- Polling guard existed, but it was scheduled using `WEBHOOK_GUARD_INTERVAL_SEC` instead of `POLLING_GUARD_INTERVAL_SEC`.
- In a stalled polling situation this delayed self-restart and let the bot sit in a "sending works, commands do not" state longer than intended.

Risk level: low

Action taken:

- Switched polling guard scheduling to `POLLING_GUARD_INTERVAL_SEC`.

### 16. Scheduler did not auto-disable stale chats on `chat not found`

File: `app/services/scheduler_service.py`

Impact:

- If a chat was deleted, lost, or otherwise unreachable, scheduler retried it every tick.
- That created repeated errors, wasted Telegram calls, and background CPU/network churn.

Risk level: low

Action taken:

- Added auto-disable behavior for unreachable chat errors like:
  - `chat not found`
  - `group chat was deleted`
  - `group is deactivated`
  - `bot was kicked from the group chat`

### 17. Q1 catch-up could fire many hours late after a reboot or long outage

File: `app/services/scheduler_service.py`

Impact:

- After long downtime, scheduler could try to "catch up" Q1 absurdly late in the same day.
- This is operationally noisy and can be user-hostile.

Risk level: low

Action taken:

- Added `SCHEDULER_Q1_CATCHUP_MAX_DELAY_MIN`.
- Defaulted it to `180` minutes.
- If the delay is larger, scheduler logs once and skips that stale Q1 catch-up for the session date.

## What was checked and not found

- No multiple worker processes like Gunicorn/Uvicorn workers.
- No Redis.
- No task queues.
- No cron sidecars.
- No obvious tight busy loops; loops are sleep-based.
- No obvious API hammering besides service guards and scheduler wakeups.
- No obvious N+1 loop explosion in the newly split handlers after refactor.

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
- `app/services/q1_service.py`
- `app/services/q2_q3_service.py`
- `.env`
- `pytest.ini`

## Verification status

- Static audit completed.
- Local `.venv` created.
- Dependencies installed from `requirements.txt`.
- `pytest` installed into local `.venv`.
- Test run result after refactor/performance work: `5 passed` in `tests/test_stats_service.py` and `tests/test_repo_service.py`.

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
2. DB pool needed right-sizing: too loose is wasteful, but too small causes handler starvation.
3. Production logging was noisier than necessary.
4. Service guard loops and healthchecks were more frequent than required.
5. Extra handled-rate loop consumed periodic CPU/logging with limited value.
6. Scheduler and message render hot paths did some avoidable ORM/SQL work.
7. Stats and recap paths had a few redundant or overly broad reads.
8. Polling recovery and stale-chat handling were not defensive enough for reboot/outage scenarios.

For the current load of about 130 users, these are still low-risk optimizations. Nothing in the audit indicates an immediate need for Redis, queues, or process sharding yet.

## What was changed

1. Added explicit SQLAlchemy pool sizing and lifo reuse, then adjusted it to `4 + overflow 2` after observing real production contention.
2. Made scheduler tick configurable and set it to 60 seconds.
3. Disabled handled-rate periodic logging.
4. Relaxed guard and healthcheck intervals.
5. Reduced Postgres `max_connections` to 20.
6. Switched bot startup command to use `exec`.
7. Removed `build-essential` from the image.
8. Switched production log level from `DEBUG` to `INFO`.
9. Changed scheduler scan to load only enabled `chat_id` values.
10. Restricted Q1/Q2/Q3 render queries to only the current chat members where possible.
11. Replaced one `count(*)` member check with an existence query.
12. Filtered zero-value `SessionUserState` rows out of stats hot paths.
13. Removed redundant holiday count queries from yearly recap generation.
14. Fixed polling guard to use its own interval setting.
15. Added auto-disable for unreachable stale chats in scheduler.
16. Added a max-delay cap for Q1 catch-up after long downtime.

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
