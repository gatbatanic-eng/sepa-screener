"""Experimental long-only range rebound screen. No orders or trade-P&L simulation."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd

CONFIG = {'version': 1, 'adxPeriod': 14, 'adxMax': 20, 'smaPeriod': 50,
          'smaSlopeDays': 10, 'smaSlopeMaxPct': 1, 'boxDays': 20,
          'bottomFraction': .20, 'minHistory': 220, 'minBoxAtr': 2,
          'krValue20Min': 500_000_000, 'usValue20Min': 5_000_000,
          'stopAtrBuffer': .5, 'eventData': 'not_connected'}

def wilder(series, period):
    result = pd.Series(np.nan, index=series.index, dtype=float)
    valid = series.dropna()
    if len(valid) < period:
        return result
    value = float(valid.iloc[:period].mean())
    result.loc[valid.index[period-1]] = value
    for date, item in valid.iloc[period:].items():
        value = (value * (period-1) + float(item)) / period
        result.loc[date] = value
    return result

def indicators(frame):
    df = frame.sort_index().loc[lambda x: ~x.index.duplicated(keep='last')].copy()
    if len(df) < CONFIG['minHistory']:
        raise ValueError('지표 계산 이력 부족')
    for key in ('Open','High','Low','Close'):
        if key not in df or (~np.isfinite(df[key].tail(220))).any() or (df[key].tail(220) <= 0).any():
            raise ValueError('OHLC 가격 누락')
    h, l, c = df.High, df.Low, df.Close
    if ((h < l) | (h < c) | (l > c)).tail(220).any():
        raise ValueError('OHLC 가격 불일치')
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr = wilder(tr,14)
    up, down = h.diff(), -l.diff()
    plus = wilder(up.where((up>down)&(up>0),0),14)
    minus = wilder(down.where((down>up)&(down>0),0),14)
    den = plus + minus
    dx = (100*(plus-minus).abs()/den).where(den!=0,0)
    df['adx'] = wilder(dx,14)
    df['atr'] = atr
    df['sma50'] = c.rolling(50).mean()
    df['slope50Pct'] = (df.sma50/df.sma50.shift(10)-1)*100
    df['sma200'] = c.rolling(200).mean()
    change = c.diff();gain=wilder(change.clip(lower=0),2);loss=wilder((-change).clip(lower=0),2)
    df['rsi2'] = (100-100/(1+gain/loss)).where(loss!=0,100).where((gain+loss)!=0,50)
    mean=c.rolling(20).mean();sd=c.rolling(20).std(ddof=0)
    df['bbPosition'] = ((c-(mean-2*sd))/(4*sd)).where(sd!=0,.5)
    df['value20'] = (c*df['Volume']).rolling(20).mean() if 'Volume' in df else np.nan
    return df

def market_gate(df):
    row=df.iloc[-1]
    return bool(row.adx < CONFIG['adxMax'] and abs(row.slope50Pct) <= CONFIG['smaSlopeMaxPct'])

def evaluate(row, frame, index_frame, market):
    out={k:row.get(k) for k in ('code','name','market','close','priceAsOf')}
    out.update(status='UNKNOWN',inUniverse=row.get('inUniverse'),rangeWatch=None,rangeGo=None,
               eventStatus='미연결 · 실적/공시 확인 필요')
    try:
        if frame is None or index_frame is None:
            raise ValueError('종목 또는 지수 OHLC 누락')
        df=indicators(frame);idx=indicators(index_frame)
        if df.index[-1].date()!=idx.index[-1].date() or df.index[-2].date()!=idx.index[-2].date():
            raise ValueError('종목과 지수 거래일 불일치')
        today, prior=df.iloc[-1],df.iloc[-2]
        if not math.isfinite(today.value20) or not math.isfinite(today.Volume):
            raise ValueError('거래량/거래대금 누락')
        out.update(priceAsOf=str(df.index[-1])[:10],close=float(today.Close))
        if row.get('status')!='OK' or not isinstance(row.get('inUniverse'),bool):
            raise ValueError('원본 데이터 확인불가')
        low,high=float(df.Low.iloc[-21:-1].min()),float(df.High.iloc[-21:-1].max())
        oldlow,oldhigh=float(df.Low.iloc[-22:-2].min()),float(df.High.iloc[-22:-2].max())
        if high<=low or oldhigh<=oldlow or today.atr<=0:
            raise ValueError('가격 범위 부족')
        pos=(today.Close-low)/(high-low)
        oldpos=(prior.Close-oldlow)/(oldhigh-oldlow)
        gate=market_gate(idx)
        liquid=bool(today.value20 >= CONFIG['krValue20Min' if market=='kr' else 'usValue20Min'] and today.Volume>0)
        trend=bool(today.Close>today.sma200 and abs(today.slope50Pct)<=CONFIG['smaSlopeMaxPct'])
        universe=row.get('inUniverse') is True
        watch=bool(gate and liquid and trend and universe and 0<=pos<=CONFIG['bottomFraction'] and high-low>=CONFIG['minBoxAtr']*today.atr)
        recovery=bool(today.Close>prior.High)
        go=bool(gate and liquid and trend and universe and 0<=oldpos<=CONFIG['bottomFraction'] and recovery and oldhigh-oldlow>=CONFIG['minBoxAtr']*prior.atr and today.Close<(oldhigh+oldlow)/2)
        if go: low,high=oldlow,oldhigh
        out.update(status='OK',rangeWatch=watch,rangeGo=go,marketRange=gate,liquidityOk=liquid,
                   longTrendOk=trend,recoveryConfirmed=recovery,adx=float(today.adx),
                   indexAdx=float(idx.adx.iloc[-1]),indexSlope50Pct=float(idx.slope50Pct.iloc[-1]),
                   slope50Pct=float(today.slope50Pct),sma200=float(today.sma200),atr=float(today.atr),
                   rsi2=float(today.rsi2),bbPosition=float(today.bbPosition),value20=float(today.value20),
                   boxLow=low,boxHigh=high,boxMid=(low+high)/2,boxPosition=float(pos),
                   previousBoxPosition=float(oldpos),referenceStop=low-CONFIG['stopAtrBuffer']*float(today.atr),
                   reason='반등 확인' if go else '하단 관찰' if watch else '조건 미충족',
                   checks={'market':gate,'liquidity':liquid,'longTrend':trend,'universe':universe,
                           'nearBottom':bool(0<=pos<=CONFIG['bottomFraction']),'recovery':recovery})
    except (ValueError,KeyError,IndexError,TypeError) as exc:
        out['reason']=str(exc)
    return out

def export_range(payload, frames):
    from screening import fetch_price_history, KOSPI_INDEX_CODE, KOSDAQ_INDEX_CODE, US_INDEX_CODE
    from research_tracker import write_json, digest, strategy_series_id
    market=payload['market'];dates=[d for b in payload['benchmarks'].values() for d in b]
    codes={'KOSPI':KOSPI_INDEX_CODE,'KOSDAQ':KOSDAQ_INDEX_CODE} if market=='kr' else {'US':US_INDEX_CODE}
    indices={}
    for name,code in codes.items():
        try: indices[name]=fetch_price_history(code,min(dates))
        except Exception: indices[name]=None
    rows=[evaluate(r,frames.get(r['code']),indices.get(r.get('market') if market=='kr' else 'US'),market) for r in payload['rows']]
    strategy={'name':'박스권 하단 반등','experimental':True,'config':CONFIG,
              'sourceHash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'universe':payload['strategy']['config']['universe']}
    result=dict(payload,rows=rows,strategy=strategy,strategyId=digest(strategy),
                strategySeriesId=strategy_series_id(strategy),
                groups=['RANGE_WATCH','RANGE_GO'],horizons=[1,3,5,10,20,60])
    # Stock OHLC already came from the source screener. Extra index OHLC feeds the market gate.
    write_json(Path(__file__).parent/'output'/f'research_input_range_{market}.json',result)
