"""Apply an explicitly approved offline plan, atomically and at most once.

Default mode only validates current records. --apply requires a fresh backup.
No newly discovered candidates are included. Disputed groups are never changed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, date, datetime, time
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, delete, insert, select, text, update
from sqlalchemy.pool import NullPool

from app.db.models import AppSetting, Chat, PoopEvent, Session as DaySession, SessionUserState, User
from app.services.data_reconciliation_preview import build_preview
from app.services.stats_streaks import best_streak_from_days, current_streak_from_days


TABLES = [Chat.__table__, User.__table__, DaySession.__table__,
          PoopEvent.__table__, SessionUserState.__table__]


def serial(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def read_snapshot(connection):
    return {table.name: [dict((k, serial(v)) for k, v in row.items())
                         for row in connection.execute(select(table)).mappings()]
            for table in TABLES}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def event_map(snapshot):
    return {e['id']: dict(source=None, occurred_at=None) | e for e in snapshot['poop_events']}


def state_map(snapshot):
    return {(r['session_id'], r['user_id']): r for r in snapshot['session_user_state']}


def validate_records(original, current, plan):
    original_states, current_states = state_map(original), state_map(current)
    original_events, current_events = event_map(original), event_map(current)
    targets = {(r['session_id'], r['user_id'])
               for kind in ('backfill', 'deduplicate', 'time_corrections') for r in plan[kind]}
    disputed = {(r['session_id'], r['user_id'])
                for kind in ('review', 'uncertain_times') for r in plan[kind]}
    require(not targets & disputed, 'Plan overlaps disputed records')
    old_sessions = {s['session_id']: s for s in original['sessions']}
    new_sessions = {s['session_id']: s for s in current['sessions']}
    for key in targets:
        sid, uid = key
        require(current_states.get(key) == original_states.get(key), f'State changed: {key}')
        require(sid in new_sessions and all(new_sessions[sid][k] == old_sessions[sid][k]
                for k in ('chat_id', 'session_date')), f'Session changed: {sid}')
        old = {eid: e for eid, e in original_events.items() if (e['session_id'], e['user_id']) == key}
        new = {eid: e for eid, e in current_events.items() if (e['session_id'], e['user_id']) == key}
        require(old == new, f'Events changed: {key}')
    for row in plan['deduplicate']:
        for pair in row['copies']:
            eid = pair['original_id']
            require(current_events.get(eid) == original_events[eid], f'Original event changed: {eid}')
            sid = original_events[eid]['session_id']
            require(sid in new_sessions and all(new_sessions[sid][k] == old_sessions[sid][k]
                    for k in ('chat_id', 'session_date')), f'Original session changed: {sid}')


def metrics(snapshot, today):
    sessions = {s['session_id']: s for s in snapshot['sessions']}
    global_days, chat_days, totals = defaultdict(dict), defaultdict(set), defaultdict(int)
    for r in snapshot['session_user_state']:
        day = date.fromisoformat(sessions[r['session_id']]['session_date'])
        if day <= today and r['poops_n'] > 0:
            ds = global_days[r['user_id']]
            ds[day] = max(ds.get(day, 0), r['poops_n'])
    for e in snapshot['poop_events']:
        s = sessions[e['session_id']]
        day = date.fromisoformat(s['session_date'])
        if day <= today and e['origin_chat_id'] == s['chat_id']:
            chat_days[s['chat_id'], e['user_id']].add(day)
            totals[s['chat_id']] += 1
    def streaks(ds):
        return dict(current=current_streak_from_days(sorted(ds), today), best=best_streak_from_days(sorted(ds)))
    return dict(global_users={uid: dict(total=sum(ds.values()), **streaks(ds)) for uid, ds in global_days.items()},
                chat_users={key: streaks(ds) for key, ds in chat_days.items()}, chat_totals=dict(totals))


def apply_plan(connection, original, plan, plan_sha256, *, apply=False):
    """Caller owns transaction. Every failure must roll back the entire operation."""
    if connection.dialect.name == 'postgresql':
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        # Block writers during comparison and mutation; ordinary reads remain available.
        connection.execute(text('LOCK TABLE sessions, session_user_state, poop_events, app_settings IN SHARE ROW EXCLUSIVE MODE'))
    audit_key = 'data_reconcile:' + plan_sha256[:48]
    previous = connection.scalar(select(AppSetting.value).where(AppSetting.key == audit_key))
    if previous:
        receipt = json.loads(previous)
        require(receipt['plan_sha256'] == plan_sha256, 'Audit key collision')
        return dict(receipt, status='already_applied')
    before = read_snapshot(connection)
    validate_records(original, before, plan)
    if not apply:
        return dict(status='validated', plan_sha256=plan_sha256, summary=plan['summary'])

    today = date.fromisoformat(plan['as_of'])
    before_metrics = metrics(before, today)
    expected_events = event_map(before)
    expected_states = deepcopy(state_map(before))
    now = datetime.now(UTC).replace(tzinfo=None)
    added, removed, corrected = [], [], []
    events_table = PoopEvent.__table__
    states_table = SessionUserState.__table__
    for row in plan['backfill']:
        for event in row['events']:
            values = dict(event, session_id=row['session_id'], user_id=row['user_id'],
                          origin_chat_id=row['chat_id'], created_at=now, updated_at=now)
            eid = connection.scalar(insert(events_table).values(**values).returning(events_table.c.id))
            expected_events[eid] = {k: serial(v) for k, v in dict(id=eid, **values).items()}
            added.append(eid)
    for row in plan['deduplicate']:
        for pair in row['copies']:
            eid = pair['copy_id']
            connection.execute(delete(events_table).where(events_table.c.id == eid))
            del expected_events[eid]
            removed.append(eid)
        # Ascending compaction avoids unique-key collisions with remaining events.
        for change in sorted(row['renumber'], key=lambda r: r['before']):
            eid = change['id']
            connection.execute(update(events_table).where(events_table.c.id == eid)
                               .values(event_n=change['after'], updated_at=now))
            expected_events[eid].update(event_n=change['after'], updated_at=now.isoformat())
        sid, uid = row['session_id'], row['user_id']
        connection.execute(update(states_table).where(states_table.c.session_id == sid, states_table.c.user_id == uid)
                           .values(**row['after'], updated_at=now))
        expected_states[sid, uid].update(row['after'], updated_at=now.isoformat())
    for row in plan['time_corrections']:
        eid = row['event_id']
        connection.execute(update(events_table).where(events_table.c.id == eid)
                           .values(source=row['after_source'], occurred_at=None, updated_at=now))
        expected_events[eid].update(source=row['after_source'], occurred_at=None, updated_at=now.isoformat())
        corrected.append(eid)

    after = read_snapshot(connection)
    # Also proves that all disputed and unrelated events/states are untouched.
    require(event_map(after) == expected_events, 'Unexpected event changes; rolling back')
    require(state_map(after) == expected_states, 'Unexpected state changes; rolling back')
    after_metrics = metrics(after, today)
    require(before_metrics['global_users'] == after_metrics['global_users'], 'Global metrics changed; rolling back')
    empty = dict(current=0, best=0)
    before_streaks, after_streaks = before_metrics['chat_users'], after_metrics['chat_users']
    changes = []
    for cid, uid in sorted(before_streaks.keys() | after_streaks.keys()):
        old, new = before_streaks.get((cid, uid), empty), after_streaks.get((cid, uid), empty)
        require(old['current'] == new['current'], 'Current chat streak changed; rolling back')
        if old != new:
            changes.append(dict(chat_id=cid, user_id=uid, before=old, after=new))
    require(changes == plan['chat_streak_changes'], 'Streak forecast changed; rolling back')
    require((len(added), len(removed), len(corrected)) == (
        plan['summary']['backfill_events'], plan['summary']['deduplicate_events'], plan['summary']['time_corrections']),
        'Operation counts differ from approved plan')
    receipt = dict(status='applied', plan_sha256=plan_sha256, applied_at=now.isoformat(),
                   added_event_ids=added, deleted_event_ids=removed, corrected_event_ids=corrected,
                   added=len(added), deleted=len(removed), time_corrected=len(corrected),
                   disputed_events_untouched=plan['summary']['review_events'],
                   uncertain_times_untouched=plan['summary']['uncertain_times'],
                   global_total_before=sum(r['total'] for r in before_metrics['global_users'].values()),
                   global_total_after=sum(r['total'] for r in after_metrics['global_users'].values()),
                   current_streaks_changed=0, chat_best_streaks_changed=len(changes),
                   chat_totals_before=before_metrics['chat_totals'], chat_totals_after=after_metrics['chat_totals'])
    connection.execute(insert(AppSetting.__table__).values(key=audit_key, value=json.dumps(receipt), updated_at=now))
    return receipt


def load_approved(snapshot_path, plan_path):
    raw = snapshot_path.read_bytes()
    original = json.loads(raw)
    plan_raw = plan_path.read_bytes()
    plan = json.loads(plan_raw)
    require(hashlib.sha256(raw).hexdigest() == plan['snapshot_sha256'], 'Snapshot hash mismatch')
    expected = build_preview(original, today=date.fromisoformat(plan['as_of']))
    require(expected == {k: v for k, v in plan.items() if k != 'snapshot_sha256'}, 'Plan differs from reviewed snapshot')
    return original, plan, hashlib.sha256(plan_raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    original, plan, sha = load_approved(args.snapshot, args.plan)
    engine = create_engine(os.environ['DATABASE_URL'], poolclass=NullPool)
    try:
        with engine.begin() as connection:
            receipt = apply_plan(connection, original, plan, sha, apply=args.apply)
        # Printed only after successful commit. The same receipt is in app_settings.
        print(json.dumps(receipt, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
