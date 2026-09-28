"""Public annual consensus adapter. Actual results never become estimates."""
import calendar
import math
import re
import requests
from datetime import date

def number(v):
    if v is None or isinstance(v,bool):return None
    try:
        n=float(str(v).replace(',','').strip())
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None

def parse_annual(payload,ticker,observed_date):
    if payload.get('itemCode')!=ticker or payload.get('financePeriodType')!='annual':
        raise ValueError('Provider symbol or period mismatch')
    fin=payload['financeInfo']
    rows={r['title']:r.get('columns',{}) for r in fin['rowList']}
    def cell(name,key):
        v=rows.get(name,{}).get(key)
        return number(v.get('value') if isinstance(v,dict) else v)
    output=[]
    for col in fin['trTitleList']:
        key=col.get('key','')
        if col.get('isConsensus')!='Y' or not re.fullmatch(r'\d{6}',key):continue
        y,m=int(key[:4]),int(key[4:]);end=date(y,m,calendar.monthrange(y,m)[1])
        if end<=observed_date:continue
        eps,rev,op=cell('EPS',key),cell('매출액',key),cell('영업이익',key)
        if eps is None and rev is None:continue
        output.append({'ticker':ticker,'periodType':'FY','periodEnd':end.isoformat(),'periodPrecision':'MONTH',
            'eps':{'avg':eps,'analysts':None,'low':None,'high':None},
            'revenue':{'avg':rev*1e8 if rev is not None else None,'analysts':None,'low':None,'high':None},
            'operatingIncome':op*1e8 if op is not None else None,'currency':'KRW'})
    return output

def fetch_annual(session,ticker,observed_date):
    r=session.get(f'https://m.stock.naver.com/api/stock/{ticker}/finance/annual',timeout=12)
    r.raise_for_status();return parse_annual(r.json(),ticker,observed_date)

def fetch_close(session,ticker,observed_at):
    r=session.get(f'https://m.stock.naver.com/api/stock/{ticker}/price',params={'page':1,'pageSize':3},timeout=12)
    r.raise_for_status();rows=r.json()
    if not isinstance(rows,list):raise ValueError('Invalid price schema')
    today=observed_at.date().isoformat()
    # A same-day quote before the close is never recorded as a closing price.
    closed=[x for x in rows if x.get('localTradedAt','')<today or (x.get('localTradedAt')==today and observed_at.hour>=16)]
    current=next((x for x in closed if x.get('localTradedAt')==today),None)
    return number(current.get('closePrice')) if current else None
