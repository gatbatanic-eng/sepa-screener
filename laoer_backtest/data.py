"""백테스트 데이터 로더와 합성 TQQQ.

합성 TQQQ: 기초지수(^NDX 또는 QQQ) 일간수익률 x3 에서 비용을 뺀다.
  일간 비용 = (운용보수 + 2 x 단기금리 + 스프레드) / 252
  - 3배 노출 중 2배분을 차입(스왑)으로 만든다고 보고 단기금리(^IRX) 2배를 비용으로 둔다.
  - 스프레드는 실제 TQQQ와 겹치는 구간에 맞춰 calibrate()로 추정한다.
시가·고가·저가는 전일 종가 대비 변화율을 3배 해서 만든다 (비용은 종가에만 반영).
"""
import os

import numpy as np
import pandas as pd

DIR = os.path.join(os.path.dirname(__file__), "data")
EXPENSE = 0.0095  # TQQQ 운용보수


def load(name):
    df = pd.read_csv(os.path.join(DIR, f"{name}.csv"), index_col=0, parse_dates=True)
    return df[~df.index.duplicated()].sort_index()


def short_rate(index):
    """연율 단기금리(소수). ^IRX 없으면 0."""
    try:
        r = load("IRX")["Close"] / 100
    except FileNotFoundError:
        return pd.Series(0.0, index=index)
    return r.reindex(index.union(r.index)).ffill().reindex(index).fillna(0.0)


def synth_tqqq(base="NDX", spread=0.0, lev=3.0):
    src = load(base)
    px = src["Adj Close"] if base == "QQQ" else src["Close"]
    adj = px / src["Close"]  # QQQ 배당 반영분을 OHLC에 같이 적용
    o, h, l, c = (src[k] * adj for k in ("Open", "High", "Low", "Close"))
    prev = c.shift()
    drag = (EXPENSE + (lev - 1) * short_rate(src.index) + spread) / 252
    rc = (lev * (c / prev - 1) - drag).clip(lower=-0.99)
    out = pd.DataFrame(index=src.index)
    out["Close"] = 100 * (1 + rc.fillna(0)).cumprod()
    pc = out["Close"].shift().fillna(100.0)
    for k, s in (("Open", o), ("High", h), ("Low", l)):
        out[k] = pc * (1 + (lev * (s / prev - 1)).clip(lower=-0.99)).fillna(0)
    out.loc[out.index[0], ["Open", "High", "Low"]] = 100.0
    # 고가/저가가 시가·종가를 감싸도록 보정
    out["High"] = out[["High", "Open", "Close"]].max(axis=1)
    out["Low"] = out[["Low", "Open", "Close"]].min(axis=1)
    return out[["Open", "High", "Low", "Close"]]


def tracking(synth, real=None):
    """실제 TQQQ와 겹치는 구간 비교: 일간수익률 상관, 연율 추적오차, 누적수익 차이."""
    real = load("TQQQ") if real is None else real
    idx = synth.index.intersection(real.index)
    a = synth["Close"].reindex(idx).pct_change().dropna()
    b = real["Adj Close"].reindex(idx).pct_change().dropna()
    yrs = (idx[-1] - idx[0]).days / 365.25
    ca, cb = (1 + a).prod(), (1 + b).prod()
    return dict(corr=a.corr(b), te=(a - b).std() * np.sqrt(252),
                cagr_synth=ca ** (1 / yrs) - 1, cagr_real=cb ** (1 / yrs) - 1)


def calibrate(base="NDX"):
    """누적 CAGR 차이가 0이 되는 스프레드를 이분법으로 찾는다."""
    lo, hi = -0.05, 0.05
    for _ in range(40):
        mid = (lo + hi) / 2
        t = tracking(synth_tqqq(base, mid))
        lo, hi = (mid, hi) if t["cagr_synth"] > t["cagr_real"] else (lo, mid)
    return (lo + hi) / 2
