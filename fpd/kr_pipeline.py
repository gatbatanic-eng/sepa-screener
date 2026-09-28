"""Korean prospective consensus research, isolated from the US frozen dataset."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from .config import research_definition_hash, canonical_json
from .provider_naver import fetch_annual, fetch_close
from .storage import read_gzip_json, write_immutable_gzip_json, write_replaceable_json
from .derive import derive_snapshot
from .cross_section import score_primary_f1
from .export_site import build_view
ROOT=Path(__file__).resolve().parents[1]
REG=ROOT/'research/fpd/registry/fpd_kr_v0.1.json'
PUBLIC=ROOT/'docs/data/fpd'

def split_checks(panel,day):
    """Conservative, independent corporate-action validation; unavailable stays unknown."""
    import yfinance as yf
    covered={r['ticker']:False for r in panel};events={r['ticker']:[] for r in panel}
    start=day-timedelta(days=115)
    for offset in range(0,len(panel),40):
        chunk=panel[offset:offset+40]
        mapping={r['ticker']+('.KS' if r['exchange']=='KOSPI' else '.KQ'):r['ticker'] for r in chunk}
        try:
            data=yf.download(list(mapping),start=start.isoformat(),end=(day+timedelta(days=1)).isoformat(),actions=True,auto_adjust=False,threads=4,group_by='ticker',progress=False,timeout=12)
            for symbol,ticker in mapping.items():
                if symbol not in data.columns.get_level_values(0):continue
                frame=data[symbol]
                if 'Stock Splits' not in frame or 'Close' not in frame:continue
                valid=frame[frame['Close'].notna() & frame['Stock Splits'].notna()]
                if valid.empty or valid.index[0].date()>day-timedelta(days=100) or valid.index[-1].date()<day-timedelta(days=7):continue
                covered[ticker]=True
                events[ticker]=[{'date':idx.date().isoformat(),'ratio':float(v),'source':'YAHOO_FINANCE'} for idx,v in valid['Stock Splits'].items() if v!=0]
        except Exception:
            continue
    return {'splitCoverage':covered,'splits':events,'earningsCalendarStatus':'UNAVAILABLE'}

def history():
    return sorted([read_gzip_json(p) for p in (ROOT/'research/fpd/raw/kr').rglob('*.json.gz')],key=lambda s:s['recordedAt'])

def collect(initial=False):
    definition=json.loads(REG.read_text());now=datetime.now(ZoneInfo('Asia/Seoul'));day=now.date();phase='initial' if initial else 'close'
    if not initial and (now.hour<16 or now.weekday()>4):raise RuntimeError('KR scheduled collection requires weekday after 16:00 KST')
    path=ROOT/f'research/fpd/raw/kr/{day:%Y/%m}/{day.isoformat()}-{phase}.json.gz'
    if path.exists():return read_gzip_json(path)
    prior=history();observations={};failures={};universe={};retrieved={}
    session=requests.Session();session.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://m.stock.naver.com/'})
    for index,item in enumerate(definition['symbols'],1):
        ticker=item['ticker'];universe[ticker]={**item,'market':'KR','close':None,'priceAsOf':None}
        try:
            rows=fetch_annual(session,ticker,day)
            if rows:observations[ticker]=rows;retrieved[ticker]=datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
            else:failures[ticker]={'status':'NO_FORWARD_CONSENSUS'}
        except Exception as e:
            failures[ticker]={'status':'PROVIDER_ERROR','kind':type(e).__name__}
        if not initial:
            try:
                close=fetch_close(session,ticker,now)
                universe[ticker].update(close=close,priceAsOf=day.isoformat() if close else None)
            except Exception:
                universe[ticker]['priceStatus']='UNAVAILABLE'
        time.sleep(.25)
        if index%25==0:print(f'KR checked {index}/{len(definition["symbols"])}; consensus {len(observations)}',flush=True)
    if not observations:raise RuntimeError('Zero valid KR observations; last good snapshot retained')
    needs_splits=any((day-date.fromisoformat(h['snapshotDate'])).days>=5 for h in prior)
    events=split_checks(definition['symbols'],day) if needs_splits and not initial else {'splitCoverage':{},'splits':{},'earningsCalendarStatus':'UNAVAILABLE'}
    stamp=datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
    raw={'schemaVersion':1,'researchId':definition['researchId'],'datasetId':definition['datasetId'],'researchDefinitionHash':research_definition_hash(definition),
        'researchCohort':'OOS_PROSPECTIVE','market':'KR','provider':'NAVER_FINANCE_ANNUAL','currency':'KRW','snapshotDate':day.isoformat(),
        'recordedAt':stamp,'collectionStartedAt':now.isoformat(),'phase':phase,'sourceCommit':os.getenv('GITHUB_SHA'),'githubRunId':os.getenv('GITHUB_RUN_ID'),
        'universe':universe,'observations':observations,'providerRetrievedAt':retrieved,'failures':failures,'events':events,'benchmark':None,
        'manifest':{'symbolsExpected':len(universe),'symbolsObserved':len(observations),'symbolsFailed':len(universe)-len(observations)},
        'status':'COLLECTED' if len(observations)==len(universe) else 'PARTIAL'}
    raw['providerNormalizedPayloadHash']=hashlib.sha256(canonical_json(observations).encode()).hexdigest()
    write_immutable_gzip_json(path,raw);return raw

def publish():
    definition=json.loads(REG.read_text());all_raw=[r for r in history() if r['datasetId']==definition['datasetId']];raw=all_raw[-1]
    # For comparisons keep the latest observation per past day; never splice intraday runs.
    prior={r['snapshotDate']:r for r in all_raw if r['snapshotDate']<raw['snapshotDate']}
    derived=derive_snapshot(raw,list(prior.values()),definition)
    signal=score_primary_f1(derived)
    monthly={'datasetId':definition['datasetId'],'confirmedMonthlyCohorts':0}
    view=build_view(raw,derived,signal,raw,all_raw,monthly,{'status':'success','publicationRunId':os.getenv('GITHUB_RUN_ID')},dataset_id=definition['datasetId'],research_id=definition['researchId'])
    view.update(market='KR',currency='KRW',phase=raw['phase'],dateKind='OBSERVATION_DATE',outcomeStatus='NOT_IMPLEMENTED_KR',benchmarkStatus='NOT_CONNECTED',universePolicy=definition['selection'])
    view['collectionStatus']=raw['status']
    for row in view['rows']:
        row['market']='KR';row['exchange']=raw['universe'][row['ticker']]['exchange']
        row['recordedAt']=raw.get('providerRetrievedAt',{}).get(row['ticker'],raw['recordedAt'])
        obs=next((o for o in raw['observations'].get(row['ticker'],[]) if o['periodEnd']==row['periodEnd']),{})
        row['operatingIncome']=obs.get('operatingIncome')
    for name,payload in [('latest_kr',raw|{'observations':None}),('derived_latest_kr',derived),('signal_latest_kr',signal),('site_latest_kr',view)]:
        write_replaceable_json(PUBLIC/f'{name}.json',payload)
    print(f"KR published: {view['manifest']}, snapshots={view['rawSnapshots']}")

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--initial',action='store_true');p.add_argument('--publish-only',action='store_true');args=p.parse_args()
    if not args.publish_only:collect(args.initial)
    publish()
