# Mini App: personal and group scopes

`/poopcheck/` is the authenticated live app; `/poopcheck-demo/` remains an isolated
demo. Both use the same unchanged design, SVGs, fonts and responsive CSS.

## Opening

- The private menu button and `/app` in the bot's private chat open personal
  records: session chat = origin chat = authenticated Telegram user ID.
- `/app` in a group sends an inline **URL** button to
  `https://t.me/<bot>?startapp=<signed-group-selector>`. Configure the bot's Main
  Mini App URL in BotFather to `MINI_APP_ORIGIN/poopcheck/` first.
- Each group link stays bound to the group that issued it, including when
  forwarded. It never guesses a numeric chat ID from Telegram's opaque
  `chat_instance`. A bare launch from a group without a selector is rejected;
  the user is directed to `/app` in that group.
- Everyone sees their own history and metrics within the selected scope, not
  other participants' detailed records. There is no cross-chat aggregation here.
- Onboarding creates a user/membership if needed. New personal chats start with
  notifications and late reminders off. Existing settings remain unchanged.

## Authentication and authorization

Identity comes only from HMAC-verified Telegram initData, with duplicate field
rejection, a maximum age of one hour and a 30-second future tolerance. Group
selectors carry a separate domain-separated 96-bit HMAC. A link is a selector,
not an access credential: current Telegram membership is checked via
`getChatMember`, independently of historical membership rows. Read checks can
be cached for 30 seconds; writes always recheck. Telegram errors fail closed.
The bot should be a group administrator for reliable membership checks.
Disabled/unknown groups are rejected. Private scope always equals the signed
user ID. No API accepts another user ID or an arbitrary chat ID.

JSON requests require exact `MINI_APP_ORIGIN`, with no CORS, no query overrides,
and a 1 KiB body limit. Credentials and real records stay in browser memory;
there is no localStorage for live data. Logs exclude tokens, request bodies,
query strings and database parameters. Responses are `no-store`.

## Reading and writing

`POST /poopcheck-api/view`: `{}` or `{"month":"YYYY-MM"}`. Returns chat-local
PoopEvents, calendar counts, total and shared bot streak calculations. Unknown
historical timestamps/fields stay unknown. Counts stop at today in chat timezone.

`POST /poopcheck-api/events`:
`{"requestId":"UUID","date":"YYYY-MM-DD","kind":0,"feeling":0}`.
The existing UI chooses consistency, then feeling, and saves on that last choice.
The existing `+` starts a new mark for today. Editing/deleting old rows through
the app is still unavailable; their controls explain this without changing design.

The server allows only today's active session, outside the 23:55–00:05 technical
window, up to the bot's existing limit of 10. It uses shared Q1 logic, updates
PoopEvent and SessionUserState atomically, and locks the user across bot/app +/−
actions. It refuses ambiguous origin/count/numbering data instead of repairing it.
No historical data migration runs.

A `mini_app_requests` receipt is committed with each mark. Retrying the same UUID
and payload returns the same outcome, even after a process restart; reusing the
UUID with different data fails. A receipt survives deletion of its event so a
retry cannot resurrect a removed mark. The browser holds an uncertain request
in memory, blocks navigation and offers the same request again after network
failure. Closing/reloading the app discards that in-memory pending action; check
the calendar before entering it again. While saving, controls are disabled.

After commit the API attempts to refresh an existing active Q1 message. It never
creates unsolicited posts. A Telegram edit failure cannot undo the saved mark;
the UI reports that only the message refresh failed. Stats read the same records.

The API has one in-flight request and one DB connection, per-user 60/minute and
global 600/minute limits, bounded membership cache, 5s Telegram/DB/lock timeouts.
There is no periodic polling. Reads use a consistent REPEATABLE READ projection
after onboarding; writes use READ COMMITTED with explicit row locks. The DB
connection must allow writes now. OS filesystem remains read-only/unprivileged.

## Deployment

1. Set `MINI_APP_ORIGIN` alongside existing `BOT_TOKEN`/`DATABASE_URL` in private
   `.env`. The former owner-only `MINI_APP_CHAT_ID` is no longer used.
2. Take a local backup. Push GitHub, then `git pull --ff-only origin main` on VPS.
3. Build bot and mini-app images. Run `alembic upgrade head` with the bot image.
   The additive migration creates only the receipt table; do not restore/modify
   historical rows. Backup verification now includes the table and can also
   verify manifests created before this migration.
4. `docker compose --profile mini-app up -d --no-deps bot mini-app`.
   Database and other applications need no restart or Nginx changes.
5. Save the existing menu/command JSON, then add `/app` to the bot command
   list while preserving other commands. After setting commands, set the default
   private menu and the owner's prior override to a WebApp button named `Открыть`
   at `/poopcheck/`. Set commands first: Telegram can reset the default menu to
   `commands` during that operation. Read both menus back afterward.
6. Verify service health, missing auth/outsider rejection, personal/group
   projections, and a Telegram launch. Use isolated test databases for synthetic
   marks; do not contaminate real histories with test entries.

Rollback: revert application commit/menu settings and rebuild bot/API. Keep the
additive receipt table and already saved real marks; do not drop user data.

Checks: `.venv/bin/python -m pytest -q`; `node --test tests/demo_model.test.cjs`.
The optional `tests/test_mini_app_postgres.py` tests concurrent retries and bot/app
writes in a uniquely named disposable schema of `MINI_APP_TEST_DATABASE_URL`.
It removes only that schema afterward. No GitHub CI is configured.
