# Mini App: owner-only read pilot

The live entry point is `/poopcheck/`; `/poopcheck-demo/` keeps its isolated demo.
The live page shares the existing CSS, SVGs and renderer. It never loads/saves
records in localStorage, and fails closed if its live adapter is unavailable.
The `+`, choices and pencil retain their appearance and show a read-only notice.
No event creation, editing, deletion or restoration endpoint exists.

## Scope and authentication

Only `BOT_OWNER_ID` can access records, and only in `MINI_APP_CHAT_ID`.
The user must have a `ChatMember` entry for this chat. The pilot chat and owner's
current Telegram membership are checked during deployment. This is not a
multi-user/chat selection mechanism: wider access requires a separate change.

`POST /poopcheck-api/view` accepts only `{}` or `{"month":"YYYY-MM"}`.
`Authorization: tma <Telegram.WebApp.initData>` is checked with Telegram's HMAC
scheme, constant-time comparison, duplicate-field rejection, a 1-hour maximum
age and 30-second future clock tolerance. Identity is obtained only from signed
Telegram data. Neither chat nor user IDs are accepted from the browser.
Requests require the configured exact HTTPS Origin and JSON. No CORS is enabled.
Credentials stay in memory and are never included in URLs or logs.
Expired launches require closing/reopening the app in Telegram.

## Data semantics

Queries require all three: session chat = configured chat, origin chat = that
chat, event user = authenticated owner. Other chats, members and cross-chat
copies do not enter this view. The all-time total is the sum of these events;
current/best streaks use the same functions as the bot's chat statistics.
Counts across days supply the calendar and week graph; event details are fetched
only for the selected month. Counts/streaks stop at today in the chat timezone.
Session date remains the accounting date. Known occurrence time is shown in the
chat timezone using the existing `event_occurrence_time` helper. Unknown time,
consistency and feeling stay unknown, including restored records. No old rows
are reconciled or modified. An empty month has no invented seed data.

One consistent, read-only PostgreSQL transaction supplies each response.
The API uses one pooled connection, zero overflow, a 5s statement timeout and a
10s idle-transaction timeout. SQLAlchemy sessions close with rollback. No writes
or migrations run. The API has one in-flight DB read and at most 60 reads/minute
for the authorized owner. Data refreshes on navigation and app activation;
there is no periodic polling and no Telegram API call per view.

## Deployment

1. Set `MINI_APP_CHAT_ID` and `MINI_APP_ORIGIN` in the server's private `.env`.
   Keep `BOT_TOKEN`, `BOT_OWNER_ID` and `DATABASE_URL` there as usual.
2. Push to GitHub, then `git pull --ff-only origin main` in `/opt/poopbot`.
3. `docker compose --profile mini-app up -d --no-deps --build mini-app`.
   This does not restart the bot, database or FinancialAssistant services.
4. Existing Nginx include `deploy/nginx/poopcheck-demo.conf` adds the live/static
   and API paths. Run `nginx -t`, then `systemctl reload nginx`.
5. Verify unauthorized access is rejected, authenticated read matches bot
   metrics, and DB connection settings are read-only. Save the owner's existing
   `getChatMenuButton` response in the private operations backup directory;
   change only that owner's menu URL to `/poopcheck/`. Leave default menu alone.

The API binds only host loopback `127.0.0.1:8087`, runs unprivileged with a
read-only filesystem and no Linux capabilities, with 192 MiB / 0.5 CPU limits.
Its HTTP liveness probe runs every 120s and does not query the database; real
DB availability is checked by authenticated reads. All API responses are
`no-store`, request access logging is disabled, and error logging excludes data.

Rollback: restore the saved owner menu JSON (or point it back to the demo), stop
only `mini-app`, remove/revert the added live/API Nginx locations and reload after
`nginx -t`. There are no database migrations or changes to undo.

Checks: `python -m pytest tests/test_mini_app.py tests/test_stats_consistency.py
 tests/test_stats_service.py -q`; `node --test tests/demo_model.test.cjs`.
Also exercise current/previous month, unknown fields, stats, unavailable data and
read-only actions in a browser, then check a real Telegram launch separately.
