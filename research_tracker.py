"""Durable SEPA observations and close-to-close forward research (not trades)."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import math
import os
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HORIZONS = (5, 20, 60)
GROUPS = ('TREND', 'READY', 'GO')

def packed(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def digest(obj):
    return hashlib.sha256(packed(obj).encode()).hexdigest()[:20]

def positive(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(packed(obj), encoding='utf-8')
    tmp.replace(path)

def series_prices(series):
    return {str(d)[:10]: float(v) for d, v in series.items() if positive(v)}

def export_inputs(frame, ohlcv, benchmarks, cfg, market):
    """Called in producer while original complete price frames still exist."""
    from generate_dashboard import COLUMN_MAP
    rows = json.loads(frame.rename(columns=COLUMN_MAP).to_json(orient='records', force_ascii=False))
    prices = {str(c): series_prices(df['Close']) for c, df in ohlcv.items()}
    for row in rows:
        row['code'] = str(row['code']).zfill(6) if market == 'KR' else str(row['code'])
        row['priceAsOf'] = max(prices.get(row['code'], {}), default=None)
    config = asdict(cfg)
    source = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((ROOT / 'sepa').glob('*.py'))}
    source['screening.py'] = hashlib.sha256((ROOT / 'screening.py').read_bytes()).hexdigest()
    strategy = {'config': config, 'source': source}
    payload = {'schemaVersion': 1, 'market': market.lower(), 'rows': rows,
               'prices': prices, 'benchmarks': {k: series_prices(v) for k, v in benchmarks.items()},
               'strategy': strategy, 'strategyId': digest(strategy),
               'recordedAt': datetime.now(timezone.utc).isoformat(),
               'sourceCommit': os.getenv('GITHUB_SHA'), 'runId': os.getenv('GITHUB_RUN_ID')}
    write_json(ROOT / 'output' / f'research_input_{market.lower()}.json', payload)

def outcome(signal, prices, benchmark, horizon):
    dates = sorted(d for d in benchmark if d > signal['date'])
    base = {'status': 'pending', 'observedSessions': min(len(dates), horizon)}
    if len(dates) < horizon:
        return base
    window = dates[:horizon]
    base.update(status='unavailable', targetDate=window[-1])
    start, end = prices.get(signal['date']), prices.get(window[-1])
    b0, b1 = benchmark.get(signal['date']), benchmark.get(window[-1])
    if not all(positive(v) for v in (start, end, b0, b1)):
        return dict(base, reason='기준일 또는 목표 거래일 가격 누락')
    ret, bench = (end/start-1)*100, (b1/b0-1)*100
    values = [prices.get(d) for d in window]
    complete = all(positive(v) for v in values)
    excursions = [(v/start-1)*100 for v in values] if complete else []
    return dict(base, status='complete', returnPct=ret, benchmarkPct=bench, excessPct=ret-bench,
                maxUpPct=max(0, *excursions) if complete else None,
                maxDownPct=min(0, *excursions) if complete else None,
                pathStatus='complete' if complete else 'missing', baselineClose=start,
                baselineRevised=abs(start / signal['originalClose'] - 1) > 0.0001)

def membership(row, group):
    if row.get('status') != 'OK':
        return None
    if row.get('inUniverse') is False:
        return False
    if row.get('inUniverse') is not True:
        return None
    if group == 'GO':
        entry = row.get('entryState')
        return entry in ('GO_BREAKOUT', 'GO_PULLBACK') if entry else None
    value = row.get('trendOk' if group == 'TREND' else 'setupReady')
    return value if isinstance(value, bool) else None

def session_closed(day, recorded_at, market):
    local = datetime.fromisoformat(recorded_at).astimezone(ZoneInfo('Asia/Seoul' if market == 'kr' else 'America/New_York'))
    return day < local.date().isoformat() or (day == local.date().isoformat() and (local.hour, local.minute) >= ((15, 40) if market == 'kr' else (16, 10)))

def reject_unclosed(state, market):
    bad = {k: d for k, d in state.get('days', {}).items() if not session_closed(d['date'], d['recordedAt'], market)}
    if not bad:
        return
    snapshots = {d['snapshot'] for d in bad.values()}
    state.setdefault('excludedObservations', {}).update(bad)
    state['days'] = {k: d for k, d in state['days'].items() if k not in bad}
    rejected = [s for s in state['signals'] if s['snapshot'] in snapshots]
    state['signals'] = [s for s in state['signals'] if s['snapshot'] not in snapshots]
    for signal in rejected:
        state['membership'].pop(f"{signal['strategyId']}:{signal['code']}:{signal['group']}", None)
    state['latestSession'] = max((d['date'] for d in state['days'].values()), default='')

def process(payload, state, root=ROOT):
    market, strategy = payload['market'], payload['strategyId']
    reject_unclosed(state, market)
    benchmarks, prices = payload['benchmarks'], payload['prices']
    if not benchmarks or not all(benchmarks.values()):
        raise ValueError('벤치마크 거래일 데이터 없음')
    sessions = {k: max(v) for k, v in benchmarks.items()}
    day = max(sessions.values())
    if not session_closed(day, payload['recordedAt'], market):
        state['deferredAt'] = payload['recordedAt']
        state['deferredReason'] = '정규장 마감 전 입력 제외'
        return state
    snapshot = {k: v for k, v in payload.items() if k not in ('prices', 'benchmarks', 'recordedAt', 'runId', 'sourceCommit')}
    snapshot['sessions'] = sessions
    sid = digest(snapshot)
    archive = root / 'research' / 'snapshots' / market / f'{day}-{sid}.json.gz'
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        saved = dict(snapshot, recordedAt=payload['recordedAt'], runId=payload.get('runId'), sourceCommit=payload.get('sourceCommit'))
        archive.write_bytes(gzip.compress(packed(saved).encode(), mtime=0))
    state.setdefault('schemaVersion', 1)
    days = state.setdefault('days', {})
    signals = state.setdefault('signals', [])
    members = state.setdefault('membership', {})
    versions = state.setdefault('strategies', {})
    versions.setdefault(strategy, payload['strategy'])
    # First complete observation of a session is canonical; revisions are archived only.
    key = f'{strategy}:{day}'
    if key not in days and day >= state.get('latestSession', ''):
        days[key] = {'date': day, 'snapshot': sid, 'strategyId': strategy, 'rows': len(payload['rows']),
                     'recordedAt': payload['recordedAt'], 'sessions': sessions}
        for row in payload['rows']:
            code, benchmark = row['code'], row.get('market') if market == 'kr' else 'US'
            if benchmark not in sessions or row.get('priceAsOf') != sessions[benchmark]:
                continue  # stale/missing quotes never create or terminate an episode
            for group in GROUPS:
                mk = f'{strategy}:{code}:{group}'
                flag = membership(row, group)
                if flag is None:
                    continue
                if flag and not members.get(mk) and positive(row.get('close')):
                    signal = {'id': digest([strategy, code, group, sessions[benchmark]]), 'code': code,
                              'name': row['name'], 'group': group, 'date': sessions[benchmark],
                              'benchmark': benchmark, 'strategyId': strategy, 'originalClose': row['close'],
                              'snapshot': sid, 'attributes': row, 'outcomes': {}}
                    signals.append(signal)
                members[mk] = flag
        state['latestSession'] = day
    for signal in signals:
        for horizon in HORIZONS:
            # Freeze completed 60-session observations after their price window leaves provider history.
            existing = signal['outcomes'].get(str(horizon), {})
            p = prices.get(signal['code'], {})
            b = benchmarks.get(signal['benchmark'], {})
            if existing.get('status') in ('complete', 'unavailable') and signal['date'] not in b:
                continue
            signal['outcomes'][str(horizon)] = outcome(signal, p, b, horizon)
    state['updatedAt'] = payload['recordedAt']
    state['market'] = market
    state['lastRunId'] = payload.get('runId')
    state['lastSourceCommit'] = payload.get('sourceCommit')
    return state

def supplement_prices(payload, state):
    """Continue following departures from today's universe; never omit failures."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from screening import fetch_price_history
    earliest = {}
    for s in state.get('signals', []):
        if s['code'] not in payload['prices'] and any(s.get('outcomes', {}).get(str(h), {}).get('status') != 'complete' for h in HORIZONS):
            earliest[s['code']] = min(earliest.get(s['code'], s['date']), s['date'])
    def fetch(code, start):
        frame = fetch_price_history(code, start)
        return series_prices(frame['Close']) if frame is not None and not frame.empty else {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, c, d): c for c, d in earliest.items()}
        for fut in as_completed(futures):
            try:
                payload['prices'][futures[fut]] = fut.result()
            except Exception:
                payload['prices'][futures[fut]] = {}

def public_view(state):
    return {k: v for k, v in state.items() if k != 'membership'}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', choices=['KR', 'US', 'ALL'], default='ALL')
    args = parser.parse_args()
    # Repair any earlier noncanonical intraday observation while retaining its audit archive.
    for existing_market in ('kr', 'us'):
        existing_path = ROOT / 'research' / f'{existing_market}.json'
        if existing_path.exists():
            saved = json.loads(existing_path.read_text())
            reject_unclosed(saved, existing_market)
            write_json(existing_path, saved)
            write_json(ROOT / 'docs' / 'research' / f'{existing_market}.json', public_view(saved))
    for market in (['kr', 'us'] if args.market == 'ALL' else [args.market.lower()]):
        path = ROOT / 'output' / f'research_input_{market}.json'
        if not path.exists():
            raise FileNotFoundError(f'이번 실행의 연구 입력 없음: {market}')
        payload = json.loads(path.read_text())
        target = ROOT / 'research' / f'{market}.json'
        state = json.loads(target.read_text()) if target.exists() else {}
        supplement_prices(payload, state)
        process(payload, state)
        if not state.get('days'):
            print(f'{market}: 정규장 마감 후 첫 기록을 기다립니다.')
            continue
        write_json(target, state)
        write_json(ROOT / 'docs' / 'research' / f'{market}.json', public_view(state))
        print(f'{market}: {len(state["days"])} daily observations, {len(state["signals"])} signal episodes; {state["latestSession"]}')

if __name__ == '__main__':
    main()
