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
    src = pd.DataFrame({k: src[k] * adj for k in ("Open", "High", "Low", "Close")})
    return lever(src, short_rate(src.index), spread, lev)


def lever(src, rate, spread=0.0, lev=3.0, expense=EXPENSE):
    """기초자산 OHLC(src)와 연율 단기금리(rate, 같은 인덱스)로 레버리지 OHLC를 만든다.
    lev=1, expense=0.002 이면 QQQ 근사."""
    o, h, l, c = (src[k] for k in ("Open", "High", "Low", "Close"))
    prev = c.shift()
    drag = (expense + (lev - 1) * rate + spread) / 252
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


def bootstrap_paths(n_paths, years=10, block=252, base="NDX", spread=0.0, seed=0, lev=3.0, expense=EXPENSE):
    """기초지수 일봉을 block 거래일 단위로 무작위 복원추출해 이어 붙인 가상 경로를 만든다.
    각 날의 (시가·고가·저가·종가 / 전일종가)와 그날 금리를 한 묶음으로 옮겨 변동성 군집과 금리 환경을 유지한다.
    반환: [(레버리지 OHLC df, 금리 Series), ...]"""
    src = load(base)
    rate = short_rate(src.index)
    prev = src["Close"].shift()
    rel = pd.DataFrame({k: src[k] / prev for k in ("Open", "High", "Low", "Close")}).iloc[1:]
    rel["rate"] = rate.iloc[1:]
    arr = rel.to_numpy()
    rng = np.random.default_rng(seed)
    n_days = int(years * 252)
    idx = pd.bdate_range("2000-01-03", periods=n_days + 1)
    out = []
    for _ in range(n_paths):
        starts = rng.integers(0, len(arr) - block, size=n_days // block + 1)
        seq = np.concatenate([arr[s:s + block] for s in starts])[:n_days]
        close = np.concatenate([[100.0], 100 * np.cumprod(seq[:, 3])])
        pc = close[:-1]
        df = pd.DataFrame(index=idx)
        df["Close"] = close
        for j, k in enumerate(("Open", "High", "Low")):
            df[k] = np.concatenate([[100.0], pc * seq[:, j]])
        r = pd.Series(np.concatenate([[seq[0, 4]], seq[:, 4]]), index=idx)
        out.append((lever(df, r, spread, lev, expense), r))
    return out


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
