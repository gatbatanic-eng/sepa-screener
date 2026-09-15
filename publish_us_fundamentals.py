"""SEC companyfacts for US trend stocks. Actual fiscal dates, no estimated EPS."""
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.request
from publish_fundamentals import growth, save

ROOT = Path(__file__).resolve().parent
TAGS = {
 'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax','RevenueFromContractWithCustomerIncludingAssessedTax','Revenues','SalesRevenueNet','Revenue'],
 'operatingProfit': ['OperatingIncomeLoss'],
 'netIncome': ['NetIncomeLoss','ProfitLoss'],
 'eps': ['EarningsPerShareDiluted'],
 'operatingCashFlow': ['NetCashProvidedByUsedInOperatingActivities'],
 'capex': ['PaymentsToAcquirePropertyPlantAndEquipment'],
 'cash': ['CashAndCashEquivalentsAtCarryingValue'],
 'liabilities': ['Liabilities'],
 'equity': ['StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest','StockholdersEquity'],
}
def day(s): return dt.date.fromisoformat(s)
def span(r): return (day(r['end'])-day(r['start'])).days if r.get('start') else 0

def facts_for(data,key,today):
    gaap=data.get('facts',{}).get('us-gaap',{})
    out=[]
    for rank,tag in enumerate(TAGS[key]):
        for r in gaap.get(tag,{}).get('units',{}).get('USD/shares' if key=='eps' else 'USD',[]):
            if r.get('form') not in ('10-Q','10-Q/A','10-K','10-K/A') or r.get('filed','9999')>today or r.get('end','9999')>today: continue
            if not isinstance(r.get('val'),(int,float)) or not math.isfinite(r['val']): continue
            out.append({**r,'tag':tag,'rank':rank})
    return out

def choose(rows):
    if not rows: return None
    # Prefer concept priority, then the most recent disclosed revision.
    return sorted(rows,key=lambda r:(r['rank'],-day(r['filed']).toordinal(),r.get('accn','')))[0]

def quarter_facts(rows, additive=True):
    out=[{**r,'method':'reported','sources':[r['accn']]} for r in rows if 70<=span(r)<=110]
    if not additive: return out
    # Convert cumulative 6/9/12-month facts into an individual quarter.
    for r in rows:
        if not 150<=span(r)<=380: continue
        prev=choose([p for p in rows if p.get('start')==r.get('start') and p['tag']==r['tag'] and 70<=(day(r['end'])-day(p['end'])).days<=110 and p['filed']<=r['filed']])
        if prev:
            out.append({**r,'start':(day(prev['end'])+dt.timedelta(days=1)).isoformat(),'val':r['val']-prev['val'],'method':'cumulative_difference','sources':[r['accn'],prev['accn']]})
    return out

def select_quarter(rows,start,end):
    exact=[r for r in rows if r.get('start')==start and r['end']==end]
    reported=[r for r in exact if r.get('method')=='reported']
    return choose(reported or exact)

def normalize(data,today):
    source={k:facts_for(data,k,today) for k in TAGS}
    qs={k:quarter_facts(v,k!='eps') for k,v in source.items() if k not in ('cash','liabilities','equity')}
    # Income periods anchor the calendar. CF-only periods do not create quarters.
    periods={ (r['start'],r['end']) for k in ('revenue','netIncome','operatingProfit') for r in qs[k] }
    # Alternative tags can describe different period starts; retain a single period per end.
    ends={}
    for start,end in sorted(periods):
        old=ends.get(end)
        if old is None or abs((day(end)-day(start)).days-90)<abs((day(end)-day(old)).days-90): ends[end]=start
    result=[]
    for end,start in sorted(ends.items()):
        rec={'period':end,'periodStart':start,'periodEnd':end,'basis':'US-GAAP','currency':'USD','provenance':{}}
        for k in TAGS:
            r=choose([p for p in source[k] if p['end']==end and not p.get('start')]) if k in ('cash','liabilities','equity') else select_quarter(qs[k],start,end)
            rec[k]=r['val'] if r else None
            if r: rec['provenance'][k]={'tag':r['tag'],'filedAt':r['filed'],'accessions':r.get('sources',[r['accn']]),'method':r.get('method','reported')}
        if all(rec[k] is None for k in ('revenue','operatingProfit','netIncome')): continue
        rec['filedAt']=max(p['filedAt'] for p in rec['provenance'].values())
        p=next((rec['provenance'][k] for k in ('revenue','netIncome','operatingProfit') if k in rec['provenance']),None)
        accn=p['accessions'][0]
        rec['sourceUrl']=f"https://www.sec.gov/Archives/edgar/data/{int(data['cik'])}/{accn.replace('-','')}/{accn}-index.html"
        rec['operatingMargin']=rec['operatingProfit']/rec['revenue']*100 if rec['revenue'] and rec['revenue']>0 and rec['operatingProfit'] is not None else None
        rec['debtToEquity']=rec['liabilities']/rec['equity']*100 if rec['equity'] and rec['equity']>0 and rec['liabilities'] is not None else None
        rec['freeCashFlow']=rec['operatingCashFlow']-rec['capex'] if rec['operatingCashFlow'] is not None and rec['capex'] is not None and rec['capex']>=0 else None
        previous=[p for p in result if 350<=(day(end)-day(p['periodEnd'])).days<=380 and abs((day(start)-day(p['periodStart'])).days-(day(end)-day(p['periodEnd'])).days)<=8]
        prior=min(previous,key=lambda p:abs((day(end)-day(p['periodEnd'])).days-365)) if previous else {}
        for k in ('revenue','operatingProfit','netIncome','eps'):
            same=rec['provenance'].get(k,{}).get('tag')==prior.get('provenance',{}).get(k,{}).get('tag')
            rec[k+'YoY']=growth(rec[k],prior.get(k) if same else None)
        result.append(rec)
    return result[-8:]

def fetch(url):
    ua=os.environ.get('SEC_USER_AGENT','').strip()
    if not ua: raise RuntimeError('SEC_USER_AGENT is required')
    for i in range(3):
        time.sleep(.2)
        try:
            req=urllib.request.Request(url,headers={'User-Agent':ua,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=60) as r: return json.load(r)
        except Exception:
            if i==2: raise RuntimeError('SEC request failed') from None
            time.sleep(2*(i+1))

def main():
    now=dt.datetime.now(dt.timezone.utc)
    stocks=json.loads((ROOT/'docs/data/latest_us.json').read_text())
    selected=[r for r in stocks if r.get('status')=='OK' and r.get('inUniverse') is True and r.get('trendOk') is True]
    tickers=fetch('https://www.sec.gov/files/company_tickers.json')
    mapping={v['ticker'].replace('.','-').upper():v['cik_str'] for v in tickers.values()}
    out=ROOT/'docs/data/fundamentals/us'
    index={'schemaVersion':1,'market':'us','checkedAt':now.isoformat(),'symbols':{}}
    failed=0
    for stock in selected:
        code=stock['code']
        try:
            cik=mapping.get(code.replace('.','-').upper())
            if cik is None: raise RuntimeError('SEC ticker mapping unavailable')
            data=fetch(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json')
            if int(data['cik'])!=int(cik): raise RuntimeError('SEC company identity mismatch')
            company=fetch(f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json')
            recent=company.get('filings',{}).get('recent',{})
            report_dates=[d for d,f in zip(recent.get('reportDate',[]),recent.get('form',[])) if f in ('10-Q','10-Q/A','10-K','10-K/A') and d]
            quarters=normalize(data,now.date().isoformat())
            payload={'schemaVersion':1,'market':'us','code':code,'name':stock['name'],'cik':str(cik),'source':'SEC EDGAR','industryCode':company.get('sic'),'industrySystem':'SIC','latestReportPeriod':max(report_dates,default=None),'checkedAt':now.isoformat(),'status':'ok' if quarters else 'unavailable','quarters':quarters,'historyNote':'과거 실적은 수집 시점에 조회한 공시값입니다. 정정 공시가 반영될 수 있으며 과거 매수 시점에 알려진 값으로 간주할 수 없습니다.'}
            digest=hashlib.sha256(json.dumps(quarters,sort_keys=True).encode()).hexdigest()[:20]
            snap=ROOT/'research/fundamentals/us'/code/(digest+'.json')
            if not snap.exists(): save(snap,payload)
            payload['firstObservedAt']=json.loads(snap.read_text())['checkedAt']
            save(out/(code+'.json'),payload)
            index['symbols'][code]={'status':payload['status'],'latestPeriod':quarters[-1]['period'] if quarters else None}
            print(code,payload['status'],len(quarters),'quarters',flush=True)
        except Exception:
            failed+=1
            index['symbols'][code]={'status':'error','reason':'SEC collection failed; prior data preserved'}
            print(code,'collection failed',flush=True)
    save(out/'index.json',index)
    print(f'US fundamentals: {len(selected)} selected, {failed} failed',flush=True)
    if failed: raise RuntimeError('Some SEC fundamentals failed')
if __name__=='__main__': main()
