from copy import deepcopy
from datetime import date

from app.services.data_reconciliation_preview import build_preview


def snapshot():
    return dict(captured_at='2026-10-19T09:00:00+00:00', chats=[dict(chat_id=cid,timezone='Europe/Minsk',is_enabled=True,show_in_global=True) for cid in [-1,-2]],
                users=[dict(user_id=1,username='example')],sessions=[dict(session_id=1,chat_id=-1,session_date='2026-02-11'),dict(session_id=2,chat_id=-2,session_date='2026-02-11')],
                session_user_state=[],poop_events=[])


def state(sid,n,marker=None):
    return dict(session_id=sid,user_id=1,poops_n=n,bristol=4,feeling='ok',achievement_text=marker)


def event(eid,sid,origin,n=1):
    return dict(id=eid,session_id=sid,user_id=1,event_n=n,origin_chat_id=origin,bristol=4,feeling='ok',created_at='2026-02-11T12:00:00')


def test_legacy_positive_counter_is_preserved_and_preview_is_read_only():
    p=snapshot();p['session_user_state']=[state(1,2)];before=deepcopy(p)
    result=build_preview(p,today=date(2026,10,19))
    assert p==before
    assert result==build_preview(p,today=date(2026,10,19))
    assert result['summary']['backfill_events']==2
    assert result['summary']['global_total_before']==result['summary']['global_total_after']==2
    events=result['backfill'][0]['events']
    assert events[0]['bristol'] is None and events[1]['bristol']==4
    assert all(e['occurred_at'] is None for e in events)
    assert result['chat_streak_changes'][0]['after']==dict(current=0,best=1)


def test_exact_copy_is_removed_only_in_plan_and_remaining_events_are_renumbered():
    p=snapshot();p['session_user_state']=[state(1,2),state(2,1)]
    p['poop_events']=[event(1,1,-2),event(2,1,-1,2),event(3,2,-2)]
    r=build_preview(p,today=date(2026,10,19))
    row=r['deduplicate'][0]
    assert row['copies']==[dict(copy_id=1,original_id=3)]
    assert row['after']['poops_n']==1
    assert row['renumber']==[dict(id=2,before=2,after=1)]
    assert r['global_changes'][0]['before']['total']==2
    assert r['global_changes'][0]['after']['total']==1
    assert len(p['poop_events'])==3


def test_missing_original_or_different_details_keeps_entire_actor_day():
    p=snapshot();p['session_user_state']=[state(1,2),state(2,1)]
    p['poop_events']=[event(1,1,-2),event(2,1,-2,2),event(3,2,-2)]
    r=build_preview(p,today=date(2026,10,19))
    assert r['deduplicate']==[] and r['summary']['review_events']==2
    p['poop_events']=p['poop_events'][:1]+p['poop_events'][2:]
    p['poop_events'][0]['feeling']='bad';p['session_user_state'][0]['poops_n']=1
    r=build_preview(p,today=date(2026,10,19))
    assert r['deduplicate']==[] and 'уточнения' in r['review'][0]['reason']


def test_only_explicit_legacy_restoration_marker_allows_time_correction():
    p=snapshot();p['session_user_state']=[state(1,1),state(2,1,'__streak_restore_recent__')]
    p['poop_events']=[event(1,1,-1),event(2,2,-2)]
    for e in p['poop_events']: e['created_at']='2026-02-12T12:00:00'
    r=build_preview(p,today=date(2026,10,19))
    assert [x['event_id'] for x in r['time_corrections']]==[2]
    assert [x['event_id'] for x in r['uncertain_times']]==[1]
    assert not r['global_changes']
