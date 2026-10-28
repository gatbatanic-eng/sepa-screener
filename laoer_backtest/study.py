"""전략 비교 실행기.

  python -m laoer_backtest.study   → 고정 구간 비교 + 시작일 롤링 비교를 results/ 에 CSV로 저장

구간:
  real      실제 TQQQ 2010-02 ~
  synth     합성 TQQQ(^NDX 기반, 비용 보정) 1985-10 ~
  dotcom    합성, 2000-03 닷컴 고점 매수 시작
  gfc       합성, 2007-10 금융위기 직전 시작
롤링: 합성 데이터에서 매월 첫 거래일 시작, horizon 년 보유 → 분포(중앙값, 하위 10%, 손실확률)
"""
import os

import pandas as pd

from . import data as D, sim, strategies as S

OUT = os.path.join(os.path.dirname(__file__), "results")
NDX_SPREAD = -0.0154  # calibrate("NDX") 결과: ^NDX 가 가격지수라 빠진 배당만큼 음수


def baseline_set():
    return [S.LumpSum, lambda: S.DCA(21, 40), lambda: S.DCA(5, 40), lambda: S.DCA(1, 40), S.LaoerV1]


def windows():
    real = D.load("TQQQ")[["Open", "High", "Low", "Close"]]
    syn = D.synth_tqqq("NDX", NDX_SPREAD)
    return {"real_2010": real, "synth_1985": syn,
            "dotcom_2000": syn.loc["2000-03-10":], "gfc_2007": syn.loc["2007-10-31":]}


def run_one(df, make):
    st = make()
    res, acct = sim.backtest(df, st)
    return st.name, sim.stats(res, st.cycles() if hasattr(st, "_cycles") else None)


def fixed(makers):
    rows = []
    for w, df in windows().items():
        for mk in makers:
            name, s = run_one(df, mk)
            rows.append(dict(구간=w, 전략=name, **s))
    return pd.DataFrame(rows)


def rolling(makers, horizon=10, step="MS"):
    syn = D.synth_tqqq("NDX", NDX_SPREAD)
    starts = syn.index.to_series().resample(step).first().dropna()
    starts = starts[starts <= syn.index[-1] - pd.DateOffset(years=horizon)]
    rows = []
    for t0 in starts:
        df = syn.loc[t0:t0 + pd.DateOffset(years=horizon)]
        for mk in makers:
            name, s = run_one(df, mk)
            rows.append(dict(시작=t0, 전략=name, 배수=s["배수"], CAGR=s["CAGR"], MDD=s["MDD"]))
    return pd.DataFrame(rows)


def summarize_rolling(r):
    g = r.groupby("전략", sort=False)
    return pd.DataFrame({
        "시나리오": g.size(),
        "CAGR중앙": g.CAGR.median(), "CAGR하위10%": g.CAGR.quantile(0.1), "CAGR최악": g.CAGR.min(),
        "MDD중앙": g.MDD.median(), "MDD최악": g.MDD.min(),
        "손실확률": g.배수.apply(lambda x: (x < 1).mean()),
    })


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    pd.set_option("display.width", 200); pd.set_option("display.max_columns", 20)
    mk = baseline_set()
    f = fixed(mk); f.to_csv(os.path.join(OUT, "baseline_fixed.csv"), index=False)
    print(f.round(3).to_string(index=False))
    r = rolling(mk); r.to_csv(os.path.join(OUT, "baseline_rolling10y.csv"), index=False)
    print(summarize_rolling(r).round(3).to_string())
