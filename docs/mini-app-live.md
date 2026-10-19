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
localStorage contains only opaque pending-request UUIDs, partitioned by account
and chat selector. It contains no initData, tokens, mark contents or history. Logs exclude tokens, request bodies,
query strings and database parameters. Responses are `no-store`.

## Reading and writing

`POST /poopcheck-api/view`: `{}` or `{"month":"YYYY-MM"}`. Returns chat-local
PoopEvents, calendar counts, total and shared bot streak calculations. Unknown
historical timestamps/fields stay unknown. Counts stop at today in chat timezone.

`POST /poopcheck-api/events`:
`{"requestId":"UUID","date":"YYYY-MM-DD","kind":0,"feeling":0}`.
The existing UI chooses consistency, then feeling, and saves on that last choice.
The existing `+` starts a new mark for today. The pencil edits consistency and
feeling through the same two screens, preserving date/time/source. Swipe a row
left (or use ArrowLeft on its focused pencil) to reveal the existing trash button;
deletion requires a native Telegram/browser confirmation.

The same endpoint accepts `{action:"edit", requestId, eventId, version, date,
kind, feeling}` or `{action:"delete", requestId, eventId, version, date}`.
Each read event includes an opaque revision. Only the signed user's records in
the authorized chat are writable, including past/closed sessions. Future dates
and the technical window are rejected. A stale revision fails with 409 and the
UI reloads the calendar, preventing silent overwrites. UUID receipts cover edits
and deletions as well as creation. Deletion closes numbering gaps, synchronizes
SessionUserState to the remaining last event and rebuilds the historical streak
cache; counts and statistics are projected from remaining events. Ambiguous old
sessions are rejected without repairs. No migration or bulk correction is run.

Calendar yellow badges already show the actual number of the user's marks in
this chat for days with more than one mark. They are part of the expanded month
calendar, not the compact week selector. One mark has no badge.

For creation, the server allows only today's active session, outside the 23:55–00:05 technical
window, up to the bot's existing limit of 10. It uses shared Q1 logic, updates
PoopEvent and SessionUserState atomically, and locks the user across bot/app +/−
actions and bot Q2/Q3 writes. It refuses ambiguous origin/count/numbering data instead of repairing it.
No historical data migration runs.

A `mini_app_requests` receipt is committed with each action. Retrying the same
UUID and payload returns the same outcome, even after a process restart; reusing
it with different data fails. Receipts survive event deletion.

Before sending an action, the browser persists only its UUID in an account/chat
partition. If storage fails, it does not send the action and explains how to use
the bot instead. No health fields or credentials are stored locally. On reopening,
reconnection, or an uncertain response, `POST /poopcheck-api/recover` with
`{"requestId":"UUID"}` resolves the action under the same authentication and fresh
membership checks. The response is `completed` or `cancelled`. Recovery locks the
same user as mutation: if no receipt exists, it inserts a cancellation receipt.
This fences off a delayed write rather than mistaking “not found yet” for failure.
A committed edit/deletion is confirmed even when its event no longer exists.
Cross-chat receipts are inaccessible; old receipts without chat scope are not
exposed. A failed recovery retains the local marker and blocks further writes
until recovery succeeds. New login data can recover a request from an expired
login. The app refreshes its calendar after recovery; it never silently resends
stored health data. Clearing application storage loses recovery markers; server
receipts remain, but the calendar should be checked before re-entering an action.

Telegram refresh work is committed with the receipt and runs outside the HTTP
response in one background worker. The worker coalesces up to 32 receipts by
chat/user/day, refreshes existing current/affected Q1/Q2/Q3 messages, and retries failures up to
five attempts with backoff. Final failure is logged and retained in the receipt's
attempt count; mark data remains committed. Jobs survive restart. There is one
startup drain, event-driven wakeups on writes, and timed retries only while work
is pending: no idle DB polling. Existing historical statistics posts remain
snapshots; reopening statistics computes current values. No unsolicited messages
are created. Missing Telegram posts are skipped; temporary errors in one post
still allow other posts to refresh before the job retries. Closed sessions keep
closed controls. Personal questions reuse Q1, so their message returns to the
personal overview instead of showing stale event-number controls. Question text
and buttons are shared between bot and API without adding aiogram to the API
image. Group Q2/Q3 summaries use origin-scoped events like Q1 and Mini App,
not stale state counters; no ambiguous historical rows are changed.

The app revalidates on calendar/statistics/home navigation, selecting a date,
opening an edit, returning to the visible app and reconnecting. There is no
interval polling. Reads block actions against stale UI; an edit starts from the
latest revision or explains that the record was deleted. An unfinished form
survives resume/read-error retry, retaining its original revision so concurrent
bot edits still produce a conflict instead of being overwritten. A form also
retains its date across midnight; an idle today view follows the server day and
month. Existing loading/error UI is reused; CSS, fonts, icons and layout are
unchanged. Historic statistics messages remain snapshots.


Stage 4 keeps the menu and statistics placeholder unchanged. Telegram Back is
visible on the first editing step: feeling → consistency → calendar cancels the
edit without a write. Back from statistics restores the prior screen and draft;
cancelling a new draft returns to a clean first step. Back is hidden during
loading, confirmation, saving, recovery uncertainty and access errors.

401/403 responses replace the content with a reopen instruction in the existing
error area; they do not offer a retry with the same expired/denied credentials.
No automatic reads or writes resume in that webview. A pending request UUID is
retained for recovery after a fresh Telegram opening. Missing/malformed Telegram
identity is handled the same way. Network/response errors use readable messages;
invalid calendar responses cannot masquerade as an empty history, and invalid
save responses go through recovery instead of being treated as success.

Explicit date selection, opening/cancelling an edit and showing a saved mark keep
the selected day/month across midnight. A failed read retry retains that choice;
an idle today view still follows the server day. Calendar navigation stops at the
API's January 2000 lower bound and the current month. Native delete confirmation
blocks duplicate actions/navigation until answered. Empty history and zero
statistics use the existing layout; recovery does not claim that the calendar
updated when the subsequent read failed. CSS, fonts, icons, menu behavior and the
statistics placeholder are unchanged. These changes are static frontend assets;
no database migration or container rebuild is needed for this stage.

The API permits two active operations plus eight queued operations, with a
six-second queue wait limit, per-user 60/minute and global 600/minute limits.
Excess traffic still receives 429. A disconnected client cannot free an active
slot while its transaction runs. Shutdown drains admitted operations. The DB pool
has three connections (two request workers plus the notification worker), no
unbounded overflow, and 5s statement/lock timeouts. Membership cache is bounded.
Reads use REPEATABLE READ projections after onboarding; writes use READ COMMITTED
with row locks. Filesystem remains read-only/unprivileged. No layout, CSS, icons,
fonts or navigation design changed for this reliability stage.

## Deployment

1. Set `MINI_APP_ORIGIN` alongside existing `BOT_TOKEN`/`DATABASE_URL` in private
   `.env`. The former owner-only `MINI_APP_CHAT_ID` is no longer used.
2. Take a local backup. Push GitHub, then `git pull --ff-only origin main` on VPS.
3. Build bot and mini-app images. Run `alembic upgrade head` with the bot image.
   The additive `mini_app_recovery_queue` migration adds scope and notification
   columns plus a partial pending-job index to the receipt table; it does not
   rewrite events or historical receipts. Run it before starting the new API. Backup verification now includes the table and can also
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

Checks: `.venv/bin/python -m pytest -q`; `node --test tests/demo_model.test.cjs tests/live_recovery.test.cjs tests/live_navigation.test.cjs`.
The optional `tests/test_mini_app_postgres.py` tests concurrent retries, recovery/write races and bot/app
writes in a uniquely named disposable schema of `MINI_APP_TEST_DATABASE_URL`.
It removes only that schema afterward. No GitHub CI is configured.
