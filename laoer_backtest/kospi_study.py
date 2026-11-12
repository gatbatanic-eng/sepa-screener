"""코스피 2배 레버리지(KODEX 레버리지 근사) 변동성조절 백테스트.

  python -m laoer_backtest.kospi_study

- 기초: 코스피 지수(^KS11, 1996-12~, 가격지수). 국내에는 3배 ETF가 없어 2배(KODEX 레버리지, 122630)로 합성한다.
- 합성 2배 = 일간수익률 x2 - (운용보수 + 단기금리 + 스프레드)/252. 단기금리는 연평균 CD91일 근사치(아래 KR_RATE).
- 실제 122630(2010-02~)과 겹치는 구간으로 추적오차를 확인하고 스프레드를 보정한다.
- 전략: TQQQ 쪽과 같은 VolTarget2 (목표변동성 / 20일 실현변동성, 밴드 ±10%p, 추세 규칙은 코스피 50/200 또는 200일선).
- 결과: results/kospi_*.csv
"""
import os

import numpy as np
import pandas as pd

from . import data as D, sim, strategies as S

DIR = os.path.join(os.path.dirname(__file__), "data_kr")
OUT = os.path.join(os.path.dirname(__file__), "results")
FEE = 0.0005          # 편도 수수료+호가 비용 근사
EXPENSE_2X = 0.0064   # KODEX 레버리지 총보수 근사
EXPENSE_1X = 0.0015
TAX = dict(rate=0.154, deduction=0.0, fx=1.0)  # 레버리지 ETF 매매차익은 배당소득 15.4% (ISA·연금계좌면 다름)

# 연평균 CD 91일 금리(%) 근사치 — 정확한 값이 아니라 대략적 수준. 현금 이자와 레버리지 차입비용 계산에만 쓴다.
KR_RATE = {1996: 12.5, 1997: 13.5, 1998: 15.0, 1999: 7.5, 2000: 7.1, 2001: 5.3, 2002: 4.8, 2003: 4.2, 2004: 3.9,
           2005: 3.7, 2006: 4.5, 2007: 5.0, 2008: 5.4, 2009: 2.9, 2010: 2.7, 2011: 3.5, 2012: 3.2, 2013: 2.7,
           2014: 2.4, 2015: 1.7, 2016: 1.4, 2017: 1.4, 2018: 1.7, 2019: 1.6, 2020: 0.8, 2021: 0.9, 2022: 2.6,
           2023: 3.7, 2024: 3.5, 2025: 2.6, 2026: 2.5}


def load(name):
    d = pd.read_csv(os.path.join(DIR, f"{name}.csv"), index_col=0, parse_dates=True)
    return d[~d.index.duplicated()].sort_index().dropna(subset=["Close"])


def kr_rate(index):
    return pd.Series([KR_RATE[y] / 100 for y in index.year], index=index)


def synth(lev=2.0, spread=0.0):
    k = load("KS11")[["Open", "High", "Low", "Close"]]
    k = k[(k > 0).all(axis=1)]
    return D.lever(k, kr_rate(k.index), spread, lev, EXPENSE_2X if lev > 1 else EXPENSE_1X)


def tracking():
    real = load("ETF122630")
    s = synth(2.0)
    return D.tracking(s, real)


def calibrate():
    lo, hi = -0.05, 0.05
    real = load("ETF122630")
    for _ in range(40):
        mid = (lo + hi) / 2
        t = D.tracking(synth(2.0, mid), real)
        lo, hi = (mid, hi) if t["cagr_synth"] > t["cagr_real"] else (lo, mid)
    return (lo + hi) / 2


def makers():
    m = {"코스피 1배 보유": None, "2배 보유": None}
    for tv in (0.20, 0.25, 0.30, 0.35, 0.40):
        for tag, kw in (("추세없음", dict()), ("50/200x0.5", dict(mom="cross", k=0.5)),
                        ("200일선 이탈시 현금", dict(mom="ma", ma=200, k=0.0))):
            m[f"변동성조절 {tv:.0%} {tag}"] = (lambda tv=tv, kw=kw: S.VolTarget2(target_vol=tv, **kw))
    return m


def run(df1, df2, name, mk, tax=False):
    df, st = (df1, S.LumpSum()) if name == "코스피 1배 보유" else (df2, S.LumpSum()) if name == "2배 보유" else (df2, mk())
    d = df2 if mk is not None else df
    if mk is not None:
        d = d.copy(); d["U"] = df1["Close"].reindex(d.index)  # 추세 규칙은 코스피 지수로 판단
    res, _ = sim.backtest(d, st, capital=10000.0, fee=FEE, cash_rate=kr_rate(d.index),
                          tax=TAX if tax else None)
    return res


def trim(res, start):
    r = res.loc[start:]
    out = r.copy(); out.attrs = res.attrs
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    print("추적오차(합성 2배 vs 실제 122630, 보정 전):", {k: round(v, 4) for k, v in tracking().items()})
    sp = calibrate()
    print(f"보정 스프레드 {sp:+.4f} →", {k: round(v, 4) for k, v in D.tracking(synth(2.0, sp), load('ETF122630')).items()})
    df1, df2 = synth(1.0), synth(2.0, sp)
    wins = {"전체 1997~": "1997-01-01", "IMF 직전(1997-01)": "1997-01-01", "닷컴 후(2000-03)": "2000-03-01",
            "금융위기 직전(2007-10)": "2007-10-01", "박스권 시작(2011-05)": "2011-05-02", "최근 10년(2016~)": "2016-10-01"}
    rows = []
    for name, mk in makers().items():
        full = run(df1, df2, name, mk)
        full_tax = run(df1, df2, name, mk, tax=True)
        row = {"전략": name}
        for wn, ws in wins.items():
            if wn == "IMF 직전(1997-01)":
                continue
            s = sim.stats(trim(full, ws))
            row[f"{wn} CAGR"], row[f"{wn} MDD"] = s["CAGR"], s["MDD"]
        s = sim.stats(trim(full, "1998-01-01"))  # 워밍업 1년 뒤부터
        st = sim.stats(trim(full_tax, "1998-01-01"))
        row.update(칼마=s["칼마"], 평균투입률=s["평균투입률"], 최장수면일=s["최장수면일"], 세후CAGR=st["CAGR"])
        # 10년 롤링 (매월 시작)
        eq = full["equity"]; c = []
        for d0 in eq.resample("MS").first().index:
            d1 = d0 + pd.DateOffset(years=10)
            if d1 > eq.index[-1] or d0 < pd.Timestamp("1998-01-01"):
                continue
            a, b = eq.loc[d0:].iloc[0], eq.loc[:d1].iloc[-1]
            seg = eq.loc[d0:d1]
            c.append(((b / a) ** 0.1 - 1, (seg / seg.cummax() - 1).min()))
        c = np.array(c)
        row.update(롤링10년_CAGR중앙=np.median(c[:, 0]), 롤링10년_CAGR하위10=np.percentile(c[:, 0], 10),
                   롤링10년_MDD중앙=np.median(c[:, 1]), 롤링10년_최악MDD=c[:, 1].min(),
                   롤링10년_손실확률=(c[:, 0] < 0).mean())
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "kospi_results.csv"), index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_rows", 100)
    fmt = df.copy()
    for col in fmt.columns[1:]:
        fmt[col] = fmt[col].map(lambda v: f"{v:.1%}" if abs(v) < 5 else f"{v:.0f}")
    print(fmt.to_string(index=False))


if __name__ == "__main__":
    main()
