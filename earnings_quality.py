"""Observed earnings improvement cohorts; research returns, not executions."""
import calendar
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo
from publish_fundamentals import save
ROOT=Path(__file__).resolve().parent
PROFILES={'relaxed':(5,10),'base':(10,15),'strict':(15,25)}
VERSION='earnings-improvement-v1'
HORIZONS=(20,60,120)
def valid(x): return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def date(s): return dt.date.fromisoformat(s)
def end(q,market):
 if market=='us': return q['periodEnd']
 y,m=int(q['year']),int(q['quarter'])*3
 return f'{y:04d}-{m:02d}-{calendar.monthrange(y,m)[1]:02d}'
def eligible_industry(d):
 s=str(d.get('industryCode') or '')
 if not s.isdigit(): return None
 if d['market']=='kr': return not (s[:2] in ('64','65','66') or '리츠' in d.get('name',''))
 n=int(s);return not(6000<=n<6500 or 6700<=n<6800)
def assess(d,today,profile='base'):
 sales,profit=PROFILES[profile];market=d['market'];qs=d.get('quarters',[])
 out={'profile':profile,'status':'hold','checks':[],'reasons':[],'latestPeriod':None}
 if len(qs)<6: out['reasons']=['연속 분기 및 전년 비교자료 부족'];return out
 qs=sorted(qs,key=lambda q:end(q,market));latest=qs[-1];out['latestPeriod']=end(latest,market)
 industry=eligible_industry(d)
 if industry is not True: out['reasons'].append('업종 정보 미확인' if industry is None else '금융·보험·리츠 등 별도 업종')
 if (date(today)-date(end(latest,market))).days>150:out['reasons'].append('실적 종료일 150일 경과')
 if d.get('latestReportPeriod') and d['latestReportPeriod']>end(latest,market):out['reasons'].append('최신 공시의 실적 미반영')
 try:
  checked=dt.datetime.fromisoformat(d['checkedAt']).date()
  if (date(today)-checked).days>3:out['reasons'].append('재무 수집 확인 지연')
 except (KeyError,ValueError):out['reasons'].append('재무 수집일 미확인')
 last=qs[-6:]
 if any(not 70<=(date(end(b,market))-date(end(a,market))).days<=110 for a,b in zip(last,last[1:])):out['reasons'].append('분기 이력 불연속')
 if len({q.get('basis') for q in last})!=1:out['reasons'].append('연결·별도 또는 회계 기준 불일치')
 def check(key,label,values,threshold,op='ge'):
  vals=values if isinstance(values,list) else [values]
  passed=None if not all(valid(v) for v in vals) else all(v+1e-8>=threshold if op=='ge' else v>threshold for v in vals)
  out['checks'].append({'key':key,'label':label,'values':vals,'threshold':threshold,'passed':passed})
 changes={k:[] for k in ('revenue','operatingProfit')}
 turnaround=False
 for cur in qs[-2:]:
  old=next((p for p in qs if 350<=(date(end(cur,market))-date(end(p,market))).days<=380),{})
  for k in changes:
   a,b=cur.get(k),old.get(k)
   same=(market=='kr' or cur.get('provenance',{}).get(k,{}).get('tag')==old.get('provenance',{}).get(k,{}).get('tag'))
   if k=='operatingProfit' and valid(a) and valid(b) and b<0<a:turnaround=True
   changes[k].append((a/b-1)*100 if valid(a) and valid(b) and b>0 and same else None)
 check('sales','2개 분기 매출 YoY',changes['revenue'],sales)
 check('profit','2개 분기 영업이익 YoY',changes['operatingProfit'],profit)
 old=next((p for p in qs if 350<=(date(end(latest,market))-date(end(p,market))).days<=380),{})
 margins=[latest.get('operatingMargin'),old.get('operatingMargin')]
 check('margin','영업이익률 전년 대비 변화(%p)',margins[0]-margins[1] if all(valid(v) for v in margins) else None,0)
 op=[q.get('operatingProfit') for q in qs[-4:]]
 check('ttmProfit','최근 12개월 영업이익',sum(op) if all(valid(v) for v in op) else None,0,'gt')
 cfs=[]
 for q in qs[-4:]:
  v=q.get('operatingCashFlow')
  if market=='kr' and q['quarter']!=1:
   prev=next((p for p in qs if p['year']==q['year'] and p['quarter']==q['quarter']-1 and p['basis']==q['basis']),{})
   p=prev.get('operatingCashFlow');v=v-p if valid(v) and valid(p) else None
  cfs.append(v)
 check('cashFlow','최근 12개월 영업현금흐름',sum(cfs) if all(valid(v) for v in cfs) else None,0,'gt')
 check('equity','자본총계',latest.get('equity'),0,'gt')
 out['turnaround']=turnaround
 if turnaround:out['reasons'].append('흑자전환 별도 관찰')
 if any(c['passed'] is None for c in out['checks']):out['reasons'].append('필수 계정 또는 비교자료 미확인')
 out['status']='hold' if out['reasons'] else 'pass' if all(c['passed'] for c in out['checks']) else 'fail'
 return out

def stamp(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
def closed_prices(code,start,market):
 from screening import fetch_price_history
 frame=fetch_price_history(code,start)
 if frame is None or frame.empty:return {}
 now=dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo('Asia/Seoul' if market=='kr' else 'America/New_York'))
 cutoff=now.date() if (now.hour,now.minute)>=((15,40) if market=='kr' else (16,10)) else now.date()-dt.timedelta(days=1)
 return {str(d)[:10]:float(v) for d,v in frame['Close'].items() if str(d)[:10]<=cutoff.isoformat() and valid(v) and v>0}
def follow(signal,prices,bm,horizon):
 sessions=sorted(d for d in bm if d>signal['observationDate'])
 if not sessions:return {'status':'pending','observedSessions':0}
 entry=sessions[0];window=sessions[1:]
 result={'status':'pending','entryDate':entry,'observedSessions':min(len(window),horizon)}
 if len(window)<horizon:return result
 target=window[horizon-1];result.update(status='unavailable',targetDate=target)
 a,b,c,d=prices.get(entry),prices.get(target),bm.get(entry),bm.get(target)
 if not all(valid(v) and v>0 for v in (a,b,c,d)):return result
 ret=(b/a-1)*100;br=(d/c-1)*100
 return dict(result,status='complete',returnPct=ret,benchmarkPct=br,excessPct=ret-br)
def publish(market):
 now=dt.datetime.now(dt.timezone.utc);today=now.astimezone(ZoneInfo('Asia/Seoul' if market=='kr' else 'America/New_York')).date().isoformat()
 path=ROOT/f'docs/research/earnings_{market}.json'
 state=json.loads(path.read_text()) if path.exists() else {'schemaVersion':1,'market':market,'strategyId':VERSION,'signals':[],'active':{},'days':{}}
 allrows=json.loads((ROOT/f'docs/data/latest_{market}.json').read_text())
 selected=[r for r in allrows if r.get('status')=='OK' and r.get('inUniverse') is True and r.get('trendOk') is True]
 index=json.loads((ROOT/f'docs/data/fundamentals/{market}/index.json').read_text())
 rows=[];active={}
 for stock in selected:
  code=str(stock['code']).zfill(6) if market=='kr' else stock['code']
  file=ROOT/f'docs/data/fundamentals/{market}/{code}.json'
  d=json.loads(file.read_text()) if file.exists() else {'market':market,'quarters':[]}
  checks={p:assess(d,today,p) for p in PROFILES}
  if index.get('symbols',{}).get(code,{}).get('status')!='ok':
   for a in checks.values():a['status']='hold';a['reasons'].append('이번 재무 수집 미확인')
  row={'code':code,'name':stock['name'],'exchange':stock.get('market'),'assessments':checks,'financialCheckedAt':d.get('checkedAt')}
  rows.append(row)
  fingerprint=stamp(checks);active[code]=fingerprint
  if state.get('active',{}).get(code)!=fingerprint:
   sid=stamp([VERSION,market,code,today,fingerprint])
   if not any(s['id']==sid for s in state['signals']):state['signals'].append({'id':sid,'code':code,'name':stock['name'],'observationDate':today,'observedAt':now.isoformat(),'exchange':stock.get('market'),'statuses':{p:a['status'] for p,a in checks.items()},'outcomes':{str(h):{'status':'pending'} for h in HORIZONS}})
 # Snapshot each run, retaining the first daily observation as the daily reference.
 snapshot={'strategyId':VERSION,'observedAt':now.isoformat(),'market':market,'rows':rows}
 digest=stamp(rows);sp=ROOT/f'research/earnings/{market}/{today}-{digest}.json'
 if not sp.exists():save(sp,snapshot)
 state['days'].setdefault(today,{'date':today,'snapshot':str(sp.relative_to(ROOT)),'counts':{p:{s:sum(r['assessments'][p]['status']==s for r in rows) for s in ('pass','fail','hold')} for p in PROFILES}})
 state.update(active=active,latestRows=rows,updatedAt=now.isoformat(),profiles={k:{'sales':v[0],'profit':v[1]} for k,v in PROFILES.items()},horizons=list(HORIZONS),method='관찰일 다음 거래일 종가를 기준으로 20·60·120거래일 뒤 종가 비교. 비용 전 가격 연구이며 실제 체결 수익이 아닙니다.')
 # All active and departed cohorts continue receiving price updates.
 from screening import KOSPI_INDEX_CODE,KOSDAQ_INDEX_CODE,US_INDEX_CODE
 pending=[s for s in state['signals'] if any(o.get('status')!='complete' for o in s['outcomes'].values()) and s['observationDate']<today]
 if pending:
  first=min(s['observationDate'] for s in pending);bench={};prices={}
  for s in pending:
   b=US_INDEX_CODE if market=='us' else KOSDAQ_INDEX_CODE if s.get('exchange')=='KOSDAQ' else KOSPI_INDEX_CODE
   if b not in bench:
    try:bench[b]=closed_prices(b,first,market)
    except Exception:bench[b]={}
   if s['code'] not in prices:
    try:prices[s['code']]=closed_prices(s['code'],first,market)
    except Exception:prices[s['code']]={}
   for h in HORIZONS:
    if s['outcomes'].get(str(h),{}).get('status')!='complete':
     if not bench[b]:s['outcomes'][str(h)]={'status':'unavailable','reason':'거래일 확인용 지수 데이터 연결 실패'}
     else:s['outcomes'][str(h)]=follow(s,prices[s['code']],bench[b],h)
 save(path,state)
 print(market,{s:sum(r['assessments']['base']['status']==s for r in rows) for s in ('pass','fail','hold')},flush=True)
if __name__=='__main__':
 for market in ('kr','us'):publish(market)
