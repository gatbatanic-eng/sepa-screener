"""Frozen-input audit. Original reports/archives are read-only.

Archive final verdict replay uses recorded full-market RS/legacy checklist
inputs. Core replay uses current source plus historical configuration/inputs;
it is deliberately NOT represented as running every historical source version.
"""
import argparse
from collections import Counter
from datetime import datetime, time, date, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from screening import StockResult, compute_entry_checklist
from sepa.config import SepaConfig
from sepa.pipeline import evaluate_stock_v2
from sepa.rs import RsV2
from entry_lab.strategies import evaluate, VERSION, RULES
from entry_lab.execution import simulate, portfolio
from entry_lab.study import ROOT, CASES, dump, summary
from entry_lab.event_risk import review

MAP=dict(pass_all='passAll',market_gate_status='marketGate',rs_percentile='rsRank',
         high52w_position='high52wPosition',dryup_ratio='dryupRatio',setup_score='setupScore',
         pivot_near='pivotNear',breakout_signal='breakoutSignal')


def replay_checklist(row):
    if row.get('passAll') not in (True,False) or any(k not in row for k in MAP.values()):
        return dict(status='UNAVAILABLE',reason='missing archived checklist inputs')
    if row['passAll'] is True and row.get('entryVerdict') not in ('GO','WATCH','NO-GO'):
        return dict(status='UNAVAILABLE',reason='missing archived final verdict')
    result=StockResult(code=row['code'],name=row.get('name',row['code']),market='US',
                       **{key:row[field] for key,field in MAP.items()},
                       v2=dict(entry_state=row.get('entryState'),recent_breakout_days_ago=row.get('boDaysAgo'),
                               pivot_distance_pct=row.get('pivotDist')))
    count,verdict,_=compute_entry_checklist(result)
    return dict(status='MATCH' if verdict==row.get('entryVerdict') and count==row.get('entryChecklistCount') else 'MISMATCH',
                storedVerdict=row.get('entryVerdict'),replayedVerdict=verdict,
                storedCount=row.get('entryChecklistCount'),replayedCount=count)


def availability(recorded_at, price_date, frame):
    known=datetime.fromisoformat(recorded_at)
    if known.tzinfo is None:raise ValueError('archive recordedAt timezone missing')
    upcoming=[str(d)[:10] for d in frame.index if str(d)[:10]>price_date]
    if not upcoming:return dict(status='PENDING',reason='next observed session missing')
    opening=datetime.combine(date.fromisoformat(upcoming[0]),time(9,30),tzinfo=ZoneInfo('America/New_York'))
    return dict(status='TIMELY' if known<opening else 'LATE',recordedAt=recorded_at,
                nextObservedSession=upcoming[0],estimatedNextOpen=opening.isoformat(),
                basis='observed session dates + regular US 09:30 open; not verified exchange calendar')


def core_comparison(row, old):
    # Legacy checklist dryupRatio includes today's volume. V2 volDryup uses
    # setup_lag_bars. They are distinct fields and MUST NOT be compared as aliases.
    return dict(status='MATCH' if old['entry_state']==row.get('entryState') else 'MISMATCH',
                storedState=row.get('entryState'),replayedState=old['entry_state'],
                storedStop=row.get('structStop'),replayedStop=old.get('structural_stop_price'),
                storedAtrContraction=row.get('atrContraction'),replayedAtrContraction=old.get('atr_contraction_ratio'),
                storedDryup=row.get('volDryup'),replayedDryup=old.get('volume_dryup_ratio'),
                reproductionScope='current core on frozen provider data and recorded full-market RS/config')


def frozen_frames(source):
    frames={s:pd.DataFrame(x['values'],columns=['Open','High','Low','Close','Volume'],
                         index=pd.to_datetime(x['dates'])) for s,x in source['prices'].items()}
    observed=datetime.fromisoformat(source['observedAt']).astimezone(ZoneInfo('America/New_York'))
    today=(observed.hour,observed.minute)>=(16,10)
    return {s:f.loc[(f.index.date<observed.date())|((f.index.date==observed.date()) & today)] for s,f in frames.items()}


def run(input_path,baseline_path,episodes_path,output_path):
    source=json.loads(input_path.read_text(encoding='utf-8'))
    baseline=json.loads(baseline_path.read_text(encoding='utf-8'))
    episodes=json.loads(episodes_path.read_text(encoding='utf-8'))
    input_hash=hashlib.sha256(input_path.read_bytes()).hexdigest()
    if input_hash!=baseline['inputSHA256'] or input_hash!=episodes['inputSHA256']:
        raise ValueError('frozen-input hash conflict')
    frames=frozen_frames(source);benchmark=frames.pop('^GSPC').Close
    changes=[];corrected=[]
    for trade in episodes['signals']:
        e=simulate(frames[trade['symbol']],trade)
        differences={k:dict(before=trade['execution'].get(k),after=e.get(k))
                     for k in ('status','entry','exit','exitDate','returnPct','realizedR','horizons')
                     if trade['execution'].get(k)!=e.get(k)}
        if differences:changes.append(dict(symbol=trade['symbol'],kind=trade['kind'],date=trade['signalDate'],differences=differences))
        corrected.append(dict(trade,execution=e))
    archives=[];diagnostics={};all_checks=Counter();core_checks=Counter();rejections=Counter();cache={}
    for symbol in CASES:
        f=frames[symbol];bench=benchmark
        observations=[]
        for d in f.index:
            day=str(d)[:10]
            if day<'2026-08-01':continue
            x=evaluate(f,bench,as_of=day)
            rejections.update(x['reasons'])
            # Candidate with solely RR removed is a diagnostic, NEVER a new signal.
            observations.append(dict(date=day,verdict=x['verdict'],reasons=x['reasons'],riskPct=x.get('riskPct'),
                                     pivot=x.get('pivot'),entry=x['entry'],stop=x['stop'],rr=x['rr'],
                                     passesOtherGates=x['reasons']==['observed resistance lacks 2R room']))
        diagnostics[symbol]=observations
    for path in sorted((ROOT/'research/snapshots/us').glob('*.json.gz')):
        data=path.read_bytes();snap=json.loads(gzip.decompress(data));config=snap.get('strategy',{}).get('config',{})
        if not all(k in config for k in ('trend','setup','pivot','swing','rs','entry')):continue
        cfg=SepaConfig().with_overrides({f'{g}.{k}':v for g,fields in config.items() for k,v in fields.items()})
        for row in snap.get('rows',[]):
            if row.get('code') not in CASES:continue
            symbol=row['code'];day=row.get('priceAsOf');f=frames[symbol]
            item=dict(snapshot=str(path.relative_to(ROOT)),snapshotSHA256=hashlib.sha256(data).hexdigest(),
                      symbol=symbol,priceAsOf=day,recordedAt=snap['recordedAt'],sourceCommit=snap.get('sourceCommit'),
                      strategySeriesId=snap.get('strategySeriesId'),checklist=replay_checklist(row))
            all_checks.update([item['checklist']['status']])
            if day not in f.index or row.get('status')!='OK':
                item['core']=dict(status='UNAVAILABLE',reason='missing price/source status')
            else:
                item['availability']=availability(snap['recordedAt'],day,f)
                prefix=f.loc[:day]
                same=abs(float(prefix.Close.iloc[-1])-row['close'])<=max(.01,row['close']*.0001)
                item['priceMatchesArchive']=same
                item['volumeMatchesArchive']=abs(float(prefix.Volume.iloc[-1])-row.get('volume',0))<=1
                if not same:
                    item['core']=dict(status='UNAVAILABLE',reason='price mismatch')
                else:
                    key=(symbol,day,json.dumps(config,sort_keys=True),row.get('rsScore'),row.get('rsChange20d'))
                    if key not in cache:
                        rs=RsV2(rs_score=row.get('rsScore'),rs_score_20d_ago=row.get('rsScorePrev'),
                                rs_change_20d=row.get('rsChange20d'),rs_line_new_high=row.get('rsLineHigh'))
                        old=evaluate_stock_v2(prefix,cfg,cond_1_7=[row.get(f'c{i}') for i in range(1,8)],
                                              close_today=row.get('close'),high_52w=row.get('high52w'),rs=rs)
                        cache[key]=old
                    old=cache[key]
                    item['core']=core_comparison(row,old)
            core_checks.update([item['core']['status']]);archives.append(item)
    evidence_path=ROOT/'docs/entry_lab/scheduled-event-evidence.json'
    evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
    ddog=next(t for t in episodes['signals'] if t['symbol']=='DDOG' and t['signalDate']=='2026-08-04' and t['kind']=='aggressive')
    event_review=review('DDOG','2026-08-04T20:10:00Z',[str(d)[:10] for d in frames['DDOG'].index],evidence['events'])
    first_records={}
    metrics={k:dict(comparable=0,matches=0) for k in ('Stop','AtrContraction','Dryup')}
    for item in archives:
        if item.get('availability'):
            key=(item['symbol'],item['priceAsOf'],item['strategySeriesId'])
            if key not in first_records or item['recordedAt']<first_records[key]['recordedAt']:
                first_records[key]=item
        for key in metrics:
            old=item['core'].get('stored'+key);new=item['core'].get('replayed'+key)
            if all(isinstance(v,(int,float)) and math.isfinite(v) for v in (old,new)):
                metrics[key]['comparable']+=1
                metrics[key]['matches']+=abs(old-new)<=max(.0001,abs(old)*.0001)
    sessions=benchmark.index[benchmark.index>=pd.Timestamp('2022-01-01')]
    accounts={k:portfolio([t for t in corrected if t['kind']==k],frames,sessions)
              for k in ('sepa','aggressive','early','catalyst')}
    result=dict(auditVersion='3.0.1-validation',strategyVersion=VERSION,generatedAt=datetime.now(timezone.utc).isoformat(),
                inputSHA256=input_hash,baselineSHA256=hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                sourceEpisodeSHA256=hashlib.sha256(episodes_path.read_bytes()).hexdigest(),productionEligible=False,improvementProven=False,
                unchangedRules=RULES==baseline['rules'],executionReplay=dict(n=len(corrected),changed=len(changes),changes=changes,
                    summaries={k:summary([t for t in corrected if t['kind']==k]) for k in ('sepa','aggressive','early','catalyst')}),
                archivedReplay=dict(n=len(archives),checklist=dict(all_checks),core=dict(core_checks),
                    numericMetricMatches=metrics,firstArchivedRecordCount=len(first_records),
                    firstArchivedAvailability=dict(Counter(x['availability']['status'] for x in first_records.values())),
                    availabilityScope='archive timestamps do not prove actual generation/delivery timestamps',observations=archives),
                caseDiagnostics=diagnostics,caseRejections=dict(rejections),eventReview=dict(evidence=evidence,
                     review=event_review,baselineTrade=ddog,claim='retrospective safety-case illustration; not validated loss reduction across universe'),
                accountReplay={k:dict(status=v['status'],endingEquity=v.get('endingEquity'),mddPct=v.get('mddPct'),
                    matchesBaseline=v.get('endingEquity')==baseline['strategies'][k]['portfolio'].get('endingEquity')) for k,v in accounts.items()},
                implementationHashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted((ROOT/'entry_lab').glob('*.py'))},
                limitations=['Historical source versions and original full OHLCV were not all replayed.',
                             'Recorded full-market RS used only on archived dates; historical universe still missing.',
                             'Regular US opening is an availability estimate; no verified exchange calendar.',
                             'Manual DDOG announcement is partial event coverage; other unknown events stay DEFER.',
                             'No split/action verification or strategy threshold relaxation in this audit.'])
    dump(output_path,result)
    print(json.dumps(dict(executionChanged=len(changes),checklist=dict(all_checks),core=dict(core_checks),
                         eventVerdict=event_review['verdict'],caseOnlyRR=sum(x['passesOtherGates'] for xs in diagnostics.values() for x in xs))))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--episodes',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.input,a.baseline,a.episodes,a.output)
