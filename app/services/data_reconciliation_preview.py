"""Read-only reconciliation planning. Never writes to a database."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, UTC
from zoneinfo import ZoneInfo

from app.services.stats_streaks import best_streak_from_days, current_streak_from_days


def build_preview(snapshot: dict, *, today: date) -> dict:
    sessions = {s['session_id']: s for s in snapshot['sessions']}
    chats = {c['chat_id']: c for c in snapshot['chats']}
    users = {u['user_id']: u for u in snapshot['users']}
    states = {(s['session_id'], s['user_id']): s for s in snapshot['session_user_state']}
    grouped = defaultdict(list)
    originals = {}
    for e in snapshot['poop_events']:
        s = sessions[e['session_id']]
        grouped[e['session_id'], e['user_id']].append(e)
        if e['origin_chat_id'] == s['chat_id']:
            originals[s['chat_id'], s['session_date'], e['user_id'], e['event_n']] = e

    def context(sid, uid):
        s, u = sessions[sid], users.get(uid, {})
        return dict(session_id=sid, user_id=uid, chat_id=s['chat_id'], date=s['session_date'],
                    user=('@' + u['username']) if u.get('username') else str(uid))

    backfill, deduplicate, review, time_corrections = [], [], [], []
    next_counts = {key: int(s['poops_n']) for key, s in states.items()}
    local_before = {key: sum(e['origin_chat_id'] == sessions[key[0]]['chat_id'] for e in es) for key, es in grouped.items()}
    local_after = dict(local_before)
    for key, state in states.items():
        es = grouped.get(key, [])
        n = int(state['poops_n'])
        if n > 0 and not es:
            # Preserve a historical positive counter, do not turn absence of
            # detailed events into proof that the user's marks never existed.
            backfill.append(dict(**context(*key), count=n, before=state,
                events=[dict(event_n=i, source='legacy_import', occurred_at=None,
                             bristol=state['bristol'] if i == n else None,
                             feeling=state['feeling'] if i == n else None) for i in range(1, n + 1)]))
            local_after[key] = n

    matched_original = 0
    foreign_total = 0
    deleted_ids = set()
    for key, es in grouped.items():
        sid, uid = key
        s = sessions[sid]
        foreign = [e for e in es if e['origin_chat_id'] != s['chat_id']]
        if foreign:
            foreign_total += len(foreign)
            pairs, reasons = [], []
            for e in foreign:
                original = originals.get((e['origin_chat_id'], s['session_date'], uid, e['event_n']))
                if original:
                    matched_original += 1
                if original is None:
                    reasons.append('Нет исходного события с той же датой, участником и номером')
                elif (e['bristol'], e['feeling']) != (original['bristol'], original['feeling']):
                    reasons.append('У копии и исходного события различаются уточнения')
                else:
                    pairs.append(dict(copy_id=e['id'], original_id=original['id']))
            state = states.get(key)
            if state is None or int(state['poops_n']) != len(es):
                reasons.append('Счётчик требует отдельной сверки')
            if reasons:
                review.append(dict(**context(*key), reason='; '.join(sorted(set(reasons))), events=foreign))
            else:
                kept = sorted((e for e in es if e['origin_chat_id'] == s['chat_id']), key=lambda e: e['event_n'])
                n = len(kept)
                after = dict(poops_n=n, bristol=kept[-1]['bristol'] if kept else None,
                             feeling=kept[-1]['feeling'] if kept else None,
                             achievement_text=state['achievement_text'] if kept else None)
                deduplicate.append(dict(**context(*key), before=state, after=after, copies=pairs,
                    renumber=[dict(id=e['id'], before=e['event_n'], after=i) for i, e in enumerate(kept, 1) if e['event_n'] != i]))
                next_counts[key] = n
                deleted_ids.update(e['id'] for e in foreign)

        marker = (states.get(key) or {}).get('achievement_text') or ''
        if marker.startswith(('__restore_period__:', '__streak_restore_recent__')) and len(es) == 1:
            e = es[0]
            if e['origin_chat_id'] == s['chat_id'] and not e.get('source'):
                time_corrections.append(dict(**context(*key), event_id=e['id'], before_source=e.get('source'),
                    created_at=e['created_at'], after_source='legacy_restore', after_occurred_at=None))

    corrected_ids = {r['event_id'] for r in time_corrections}
    uncertain_times = []
    for e in snapshot['poop_events']:
        if e['id'] in deleted_ids or e['id'] in corrected_ids or e.get('source'):
            continue
        s = sessions[e['session_id']]
        if e['origin_chat_id'] != s['chat_id'] or not e.get('created_at'):
            continue
        dt = datetime.fromisoformat(e['created_at'])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        tz = ZoneInfo(chats[s['chat_id']]['timezone'])
        if dt.astimezone(tz).date().isoformat() != s['session_date']:
            uncertain_times.append(dict(**context(e['session_id'], e['user_id']), event_id=e['id'], created_at=e['created_at']))

    def global_metrics(counts):
        days = defaultdict(dict)
        for (sid, uid), n in counts.items():
            d = sessions[sid]['session_date']
            if d <= today.isoformat() and n > 0:
                days[uid][d] = max(days[uid].get(d, 0), n)
        return {uid: dict(total=sum(ds.values()), current=current_streak_from_days([date.fromisoformat(d) for d in sorted(ds)], today),
                         best=best_streak_from_days([date.fromisoformat(d) for d in sorted(ds)])) for uid, ds in days.items()}

    before_global = global_metrics({key: int(s['poops_n']) for key, s in states.items()})
    after_global = global_metrics(next_counts)
    empty = dict(total=0, current=0, best=0)
    global_changes = [dict(user_id=uid, user=('@'+users[uid]['username']) if users.get(uid, {}).get('username') else str(uid),
                           before=before_global.get(uid, empty), after=after_global.get(uid, empty))
                      for uid in sorted(before_global.keys() | after_global.keys()) if before_global.get(uid, empty) != after_global.get(uid, empty)]

    def chat_metrics(counts):
        totals, days = defaultdict(int), defaultdict(set)
        for (sid, uid), n in counts.items():
            s = sessions[sid]
            if s['session_date'] > today.isoformat() or n <= 0:
                continue
            totals[s['chat_id']] += n
            days[s['chat_id'], uid].add(date.fromisoformat(s['session_date']))
        streaks = {key: dict(current=current_streak_from_days(sorted(ds), today), best=best_streak_from_days(sorted(ds))) for key, ds in days.items()}
        return totals, streaks

    old_totals, _ = chat_metrics({key: int(s['poops_n']) for key, s in states.items()})
    event_totals, before_streaks = chat_metrics(local_before)
    final_totals, after_streaks = chat_metrics(local_after)
    streak_changes = [dict(chat_id=cid, user_id=uid, before=before_streaks.get((cid, uid), dict(current=0,best=0)),
                           after=after_streaks.get((cid, uid), dict(current=0,best=0)))
        for cid, uid in sorted(before_streaks.keys() | after_streaks.keys())
        if before_streaks.get((cid, uid), dict(current=0,best=0)) != after_streaks.get((cid, uid), dict(current=0,best=0))]
    visible = {cid for cid, c in chats.items() if cid < 0 and c['is_enabled'] and c['show_in_global']}
    def ranks(totals):
        return {cid: i for i, (cid, n) in enumerate(sorted(((cid, totals.get(cid,0)) for cid in visible if totals.get(cid,0)>0), key=lambda x: (-x[1],x[0])), 1)}
    rank_old, rank_code, rank_final = ranks(old_totals), ranks(event_totals), ranks(final_totals)
    chat_changes = [dict(chat_id=cid, visible=cid in visible, old_ranking_total=old_totals.get(cid,0),
                         code_total=event_totals.get(cid,0), after_repair_total=final_totals.get(cid,0),
                         old_rank=rank_old.get(cid), code_rank=rank_code.get(cid), after_repair_rank=rank_final.get(cid))
                    for cid in sorted(chats) if len({old_totals.get(cid,0),event_totals.get(cid,0),final_totals.get(cid,0)})>1
                    or len({rank_old.get(cid),rank_code.get(cid),rank_final.get(cid)})>1]
    return dict(captured_at=snapshot.get('captured_at'), as_of=today.isoformat(),
        summary=dict(backfill_rows=len(backfill), backfill_events=sum(r['count'] for r in backfill),
                     foreign_events=foreign_total, matching_original=matched_original,
                     deduplicate_rows=len(deduplicate), deduplicate_events=len(deleted_ids),
                     review_rows=len(review), review_events=sum(len(r['events']) for r in review),
                     time_corrections=len(time_corrections), uncertain_times=len(uncertain_times),
                     global_total_before=sum(r['total'] for r in before_global.values()),
                     global_total_after=sum(r['total'] for r in after_global.values()),
                     global_users_changed=len(global_changes), chat_streaks_changed=len(streak_changes)),
        backfill=backfill, deduplicate=deduplicate, review=review, time_corrections=time_corrections,
        uncertain_times=uncertain_times, chat_changes=chat_changes, global_changes=global_changes, chat_streak_changes=streak_changes)
