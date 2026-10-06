"""Closed-session routing and auditable collection health; no trading rules."""
import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SCHEDULES = {'0 11 * * 1-5': 'KR', '35 11 * * 1-5': 'KR', '35 12 * * 1-5': 'KR', '0 5 * * 2-6': 'US', '35 5 * * 2-6': 'US'}
PLAN_LABELS = {'KR': '평일 20:00 목표 · 보완 확인 20:35/21:35 KST', 'US': '화~토 14:00 목표 · 보완 확인 14:35 KST'}

def latest_closed_session(payload, market, now):
    zone = ZoneInfo('Asia/Seoul' if market == 'KR' else 'America/New_York')
    local = now.astimezone(zone)
    cutoff = (15, 40) if market == 'KR' else (16, 10)
    result = payload.get('chart', {}).get('result') or []
    if not result:
        raise ValueError('기준지수 응답 없음')
    row = result[0]
    closes = row.get('indicators', {}).get('quote', [{}])[0].get('close', [])
    days = []
    for stamp, close in zip(row.get('timestamp', []), closes):
        if not isinstance(close, (float, int)) or isinstance(close, bool) or not math.isfinite(close) or close <= 0:
            continue
        day = datetime.fromtimestamp(stamp, zone).date()
        if day < local.date() or day == local.date() and (local.hour, local.minute) >= cutoff:
            days.append(day.isoformat())
    if not days:
        raise ValueError('완결 지수 일봉 없음')
    return max(days)

def probe(market, now):
    symbol = '%5EKS11' if market == 'KR' else '%5EGSPC'
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + symbol + '?range=1mo&interval=1d'
    with urlopen(Request(url, headers={'User-Agent': 'GoldenCode-CollectionHealth/1.0'}), timeout=15) as response:
        return latest_closed_session(json.load(response), market, now)

def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}

def route(event, schedule, requested, message, expected, saved):
    if event == 'schedule':
        if schedule not in SCHEDULES:
            raise ValueError('등록되지 않은 예약식: 시장을 임의 선택하지 않음')
        primary = SCHEDULES[schedule]
        markets = [m for m in ('KR', 'US') if expected.get(m) and saved.get(m) != expected[m]]
        if expected.get(primary) is None and primary not in markets:
            markets.append(primary)  # unavailable source is not an exchange holiday
    else:
        primary = ('KR' if '[run-kr]' in message else 'ALL' if '[run-all]' in message else 'US') if event == 'push' else requested
        if primary not in ('KR', 'US', 'ALL'):
            raise ValueError('실행 시장 확인 필요')
        markets = ['KR', 'US'] if primary == 'ALL' else [primary]
    return {'market': 'ALL' if len(markets) == 2 else markets[0] if markets else 'NONE', 'needed': bool(markets), 'markets': markets}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['plan', 'status'])
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    at = now.isoformat()
    plan_path = ROOT / 'output' / 'collection_plan.json'
    if args.mode == 'plan':
        expected, errors = {}, {}
        for m in ('KR', 'US'):
            try:
                expected[m] = probe(m, now)
            except Exception as exc:
                expected[m] = None
                errors[m] = type(exc).__name__ + ': 기준지수 완결일 확인 실패'
        saved = {m: read(ROOT / 'docs' / 'research' / (m.lower()+'.json')).get('latestSession') for m in ('KR', 'US')}
        plan = {**route(os.getenv('COLLECTION_EVENT', 'workflow_dispatch'), os.getenv('COLLECTION_SCHEDULE', ''), os.getenv('COLLECTION_MARKET', 'ALL'), os.getenv('COLLECTION_PUSH_MESSAGE', ''), expected, saved), 'startedAt': at, 'expected': expected, 'probeErrors': errors}
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        output = os.getenv('GITHUB_OUTPUT')
        if output:
            with open(output, 'a', encoding='utf-8') as handle:
                handle.write('market='+plan['market']+'\nneeded='+str(plan['needed']).lower()+'\n')
        print(json.dumps(plan, ensure_ascii=False))
        return
    plan = read(plan_path)
    path = ROOT / 'docs' / 'research' / 'collection_status.json'
    health = read(path)
    health.update(schemaVersion=1, updatedAt=at, scheduleNotes='목표 시각이며 GitHub Actions 지연 가능. 한국/미국 실제 완결 지수일로 밀린 시장도 보완.', runId=os.getenv('GITHUB_RUN_ID'), markets=health.get('markets', {}))
    for m in ('KR', 'US'):
        previous = health['markets'].get(m, {})
        research = read(ROOT / 'docs' / 'research' / (m.lower()+'.json'))
        expected = plan.get('expected', {}).get(m)
        observed = research.get('latestSession')
        state = 'unverified' if expected is None else 'current' if observed == expected else 'pending' if m not in plan.get('markets', []) else 'stale'
        attempted = m in plan.get('markets', [])
        health['markets'][m] = {'status': state, 'expectedSession': expected, 'priceAsOf': observed, 'collectionStartedAt': plan.get('startedAt') if attempted else previous.get('collectionStartedAt'), 'checkedAt': at, 'researchUpdatedAt': research.get('updatedAt'), 'runId': os.getenv('GITHUB_RUN_ID') if attempted else previous.get('runId'), 'checkRunId': os.getenv('GITHUB_RUN_ID'), 'attempted': attempted, 'scheduleLabel': PLAN_LABELS[m], 'reason': plan.get('probeErrors', {}).get(m) or ('기준지수와 연구 기록 날짜 일치' if state == 'current' else '새 거래일 기록 미반영 · 마지막 정상 자료 보존')}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding='utf-8')
    if any(health['markets'][m]['status'] != 'current' for m in plan.get('markets', [])):
        print('::warning::수집 실행 완료와 최신 거래일 반영은 별개입니다. collection_status 확인 필요')

if __name__ == '__main__':
    main()
