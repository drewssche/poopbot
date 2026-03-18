# Refactor Plan

## Goal

Reduce maintenance risk in oversized modules by splitting them into smaller, context-specific files without changing runtime behavior.

This is a code health and change-safety effort first. It is not expected to produce a large direct RAM reduction by itself.

## Status

### In Progress

- Performance follow-up
  - profile SQL/query hotspots and scheduler paths after structural cleanup

### Pending

- `app/bot/handlers/callbacks_debug.py`
  - optionally split recap/debug action execution from callback routing if the file grows again
- `app/bot/handlers/callbacks_help.py`
  - optionally split delete-confirm flows into dedicated helpers if the file grows again

### Completed

- Added low-risk runtime performance optimizations and documented them in `/Users/bitriks24/Downloads/poopbot/audit/performance.md`
- Added local test harness support with `.venv` and `pytest.ini`
- Added migrated-chat handling so scheduler stops retrying stale Telegram group ids
- Split `app/services/scheduler_service.py` into:
  - `/Users/bitriks24/Downloads/poopbot/app/services/scheduler_service.py`
  - `/Users/bitriks24/Downloads/poopbot/app/services/scheduler_reports.py`
  - `/Users/bitriks24/Downloads/poopbot/app/services/scheduler_telegram.py`
- Reduced `scheduler_service.py` from `735` lines to `477` lines
- Re-pointed debug report import to `/Users/bitriks24/Downloads/poopbot/app/services/scheduler_reports.py`
- Verified refactor with tests: `5 passed`
- Added `/Users/bitriks24/Downloads/poopbot/app/services/stats_common.py`
- Extracted `Range`, period helpers, waste estimation, and basic formatting from `stats_service.py`
- Reduced `stats_service.py` from `1368` lines to `1296` lines
- Added `/Users/bitriks24/Downloads/poopbot/app/services/stats_rankings.py`
- Extracted visible-group ranking and among-chats snapshot logic from `stats_service.py`
- Reduced `stats_service.py` further from `1296` lines to `1104` lines
- Added `/Users/bitriks24/Downloads/poopbot/app/services/stats_streaks.py`
- Extracted streak computations and raw debug text helpers from `stats_service.py`
- Reduced `stats_service.py` further from `1104` lines to `744` lines
- Added `/Users/bitriks24/Downloads/poopbot/app/services/stats_metrics.py`
- Extracted chat metrics, event collection, distribution helpers, legends, and participant/ranking helper data from `stats_service.py`
- Reduced `stats_service.py` further from `744` lines to `523` lines
- Split recap logic into:
  - `/Users/bitriks24/Downloads/poopbot/app/services/recap_service.py`
  - `/Users/bitriks24/Downloads/poopbot/app/services/recap_common.py`
  - `/Users/bitriks24/Downloads/poopbot/app/services/recap_cards.py`
- Reduced `recap_service.py` from `631` lines to `12` lines by turning it into a compatibility facade
- Verified recap refactor by importing:
  - `/Users/bitriks24/Downloads/poopbot/app/bot/handlers/callbacks_recap.py`
  - `/Users/bitriks24/Downloads/poopbot/app/bot/handlers/callbacks_debug.py`
- Added `/Users/bitriks24/Downloads/poopbot/app/bot/handlers/debug_content.py`
- Extracted debug action texts, explanations, and labels from `/Users/bitriks24/Downloads/poopbot/app/bot/handlers/callbacks_debug.py`
- Reduced `callbacks_debug.py` from `504` lines to `391` lines
- Verified debug handler refactor with tests: `5 passed`
- Added `/Users/bitriks24/Downloads/poopbot/app/bot/handlers/help_content.py`
- Extracted help/about/settings/notifications/global-visibility texts from `/Users/bitriks24/Downloads/poopbot/app/bot/handlers/callbacks_help.py`
- Reduced `callbacks_help.py` from `514` lines to `415` lines
- Verified help handler refactor with tests: `5 passed`
- Added `/Users/bitriks24/Downloads/poopbot/app/bot/handlers/help_actions.py`
- Extracted notification/global-visibility panel rendering and Q1/Q2Q3 refresh side effects from `/Users/bitriks24/Downloads/poopbot/app/bot/handlers/callbacks_help.py`
- Reduced `callbacks_help.py` further from `415` lines to `275` lines
- Verified second help handler refactor with tests and imports: `5 passed`

## Expected payoff

### High

- Safer edits in scheduler and stats logic
- Easier targeted tests
- Faster bug isolation

### Medium

- Lower chance of accidental regressions
- Smaller import surfaces per file

### Low

- Direct steady-state RAM reduction
- Direct idle CPU reduction unless logic itself is also simplified
