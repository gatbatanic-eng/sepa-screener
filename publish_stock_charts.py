"""Daily, per-symbol chart files for SEPA trend candidates; no trading rules changed."""
import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd

ROOT=Path(__file__).resolve().parent

def rsi14(close):
    change=close.diff();values=[None]*len(close)
    if len(close)<15: return values
    gain=float(change.iloc[1:15].clip(lower=0).mean())
    loss=float((-change.iloc[1:15]).clip(lower=0).mean())
    for i in range(14,len(close)):
        if i>14:
            delta=float(change.iloc[i]);gain=(gain*13+max(delta,0))/14;loss=(loss*13+max(-delta,0))/14
        values[i]=50. if gain+loss==0 else 100. if loss==0 else 100-100/(1+gain/loss)
    return values

def make_chart(frame,code,name,market,benchmark_date,now=None):
    now=now or datetime.now(timezone.utc)
    local=now.astimezone(ZoneInfo('Asia/Seoul' if market=='kr' else 'America/New_York'))
    cutoff=local.date()
    if (local.hour,local.minute)<((15,40) if market=='kr' else (16,10)): cutoff-=timedelta(days=1)
    df=frame.sort_index().loc[lambda x:~x.index.duplicated(keep='last')].copy()
    df=df[[d.date()<=cutoff for d in df.index]]
    if 'Close' not in df: raise ValueError('종가 데이터 없음')
    df=df[pd.to_numeric(df.Close,errors='coerce').map(lambda x:math.isfinite(x) and x>0)]
    if len(df)<20: raise ValueError('차트 이력 부족')
    close=df.Close.astype(float)
    data={'dates':[str(d)[:10] for d in df.index]}
    for key,col in [('open','Open'),('high','High'),('low','Low'),('close','Close'),('volume','Volume')]:
        data[key]=df[col].tolist() if col in df else [None]*len(df)
    for n in [20,50,150,200]: data[f'sma{n}']=close.rolling(n).mean().tolist()
    data['rsi']=rsi14(close)
    for k,arr in data.items():
        data[k]=arr[-252:] if k=='dates' else [round(float(v),6) if v is not None and math.isfinite(float(v)) else None for v in arr[-252:]]
    return dict(schemaVersion=1,code=code,name=name,market=market,currency='KRW' if market=='kr' else 'USD',
                priceAsOf=data['dates'][-1],benchmarkAsOf=benchmark_date,generatedAt=now.isoformat(),
                source='FinanceDataReader · 장중 일봉 제외',rsiPeriod=14,**data)

def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':'),allow_nan=False));tmp.replace(path)

def publish(market):
    from screening import fetch_price_history, KOSPI_INDEX_CODE, KOSDAQ_INDEX_CODE, US_INDEX_CODE
    rows=json.loads((ROOT/'docs'/'data'/f'latest_{market}.json').read_text())
    selected=[r for r in rows if r.get('status')=='OK' and r.get('inUniverse') is True and (r.get('trendOk') is True or r.get('setupReady') is True or r.get('entryState') in ('GO_BREAKOUT','GO_PULLBACK'))]
    start=(datetime.now(timezone.utc)-timedelta(days=900)).date().isoformat()
    benchmarks={}
    for name,code in ({'KOSPI':KOSPI_INDEX_CODE,'KOSDAQ':KOSDAQ_INDEX_CODE} if market=='kr' else {'US':US_INDEX_CODE}).items():
        frame=fetch_price_history(code,start)
        benchmarks[name]=make_chart(frame,code,name,market,None)['priceAsOf']
    target=ROOT/'docs'/'data'/'stock_charts'/market
    def fetch(row):
        code=str(row['code']).zfill(6) if market=='kr' else str(row['code'])
        chart=make_chart(fetch_price_history(code,start),code,row['name'],market,benchmarks.get(row['market'] if market=='kr' else 'US'))
        save(target/f'{code}.json',chart)
        return code,{'priceAsOf':chart['priceAsOf'],'benchmarkAsOf':chart['benchmarkAsOf'],'status':'ok'}
    results={}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(fetch,r):r for r in selected}
        for future in as_completed(futures):
            row=futures[future];code=str(row['code']).zfill(6) if market=='kr' else str(row['code'])
            try: key,result=future.result();results[key]=result
            except Exception as exc: results[code]={'status':'unavailable','reason':str(exc)[:200]}
    save(target/'index.json',{'schemaVersion':1,'market':market,'generatedAt':datetime.now(timezone.utc).isoformat(),'symbols':results})
    print(market, 'selected',len(selected),'available',sum(x['status']=='ok' for x in results.values()),'unavailable',sum(x['status']!='ok' for x in results.values()))
    if selected and not any(x['status']=='ok' for x in results.values()): raise RuntimeError(f'{market}: 모든 차트 수집 실패')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--market',choices=['KR','US','ALL'],default='ALL');args=parser.parse_args()
    for m in (['kr','us'] if args.market=='ALL' else [args.market.lower()]): publish(m)
