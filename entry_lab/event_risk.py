"""Separate US event-risk review. No order/production approval is returned.

Two sessions before and one session after a scheduled event is a NEW research
hypothesis following the DDOG loss audit. It is not proven portfolio protection.
Only announcements already published at the decision instant can be used.
"""
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

POLICY_ID='scheduled-event-risk:1.0.0-research'


def aware(value):
    x=datetime.fromisoformat(value)
    if x.tzinfo is None or x.utcoffset() is None:raise ValueError('timezone required')
    return x


def official_url(value):
    try:
        u=urlparse(value)
        return u.scheme=='https' and bool(u.hostname) and not u.username and not u.password
    except (ValueError,TypeError):return False


def review(symbol, decision_at, sessions, events, coverage=None):
    now=aware(decision_at);day=now.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    calendar=list(sessions)
    if not calendar or calendar!=sorted(set(calendar)):raise ValueError('ordered unique sessions required')
    for s in calendar:date.fromisoformat(s)
    next_sessions=[s for s in calendar if datetime.combine(date.fromisoformat(s),time(9,30),
                                                         tzinfo=ZoneInfo('America/New_York'))>now]
    result=dict(policyId=POLICY_ID,productionEligible=False,verdict='DEFER',
                decisionAt=decision_at,nextSession=next_sessions[0] if next_sessions else None,
                reasons=[],knownEvents=[],excludedEventCount=0,calendarBasis='supplied dates/regular US open estimate')
    if not next_sessions:
        result['reasons']=['next session unavailable'];return result
    entry_pos=calendar.index(next_sessions[0]);near=[]
    for event in events:
        try:
            if (event.get('symbol')!=symbol or event.get('verified') is not True
                    or not official_url(event.get('source')) or aware(event['publishedAt'])>now):
                raise ValueError('unsupported/future event')
            event_day=date.fromisoformat(event['eventDate']).isoformat()
            if event_day not in calendar:raise ValueError('event session unavailable')
            distance=calendar.index(event_day)-entry_pos
            item=dict(eventDate=event_day,publishedAt=event['publishedAt'],source=event['source'],
                      type=event.get('type'),sessionsFromEntry=distance)
            result['knownEvents'].append(item)
            if -1<=distance<=2:near.append(item)
        except (ValueError,KeyError,TypeError):result['excludedEventCount']+=1
    if near:
        result.update(verdict='BLOCK',reasons=['scheduled event inside -1/+2 session window']);return result
    try:
        window_end=calendar[min(entry_pos+2,len(calendar)-1)]
        if entry_pos+2>=len(calendar):raise ValueError('incomplete calendar window')
        if not (isinstance(coverage,dict) and coverage.get('symbol')==symbol
                and coverage.get('complete') is True and coverage.get('verified') is True
                and official_url(coverage.get('source')) and aware(coverage['publishedAt'])<=now
                and date.fromisoformat(coverage['fromDate']).isoformat()<=calendar[max(0,entry_pos-1)]
                and date.fromisoformat(coverage['throughDate']).isoformat()>=window_end):
            raise ValueError('calendar coverage unknown')
        result.update(verdict='CLEAR_REVIEW',reasons=['no known event in covered window; research review only'])
    except (ValueError,KeyError,TypeError):result['reasons']=['complete point-in-time event coverage unavailable']
    return result
