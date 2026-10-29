"""낙폭 방어 임계값 민감도.  python -m laoer_backtest.kospi_dd_grid"""
import itertools

import pandas as pd

from . import kospi_study as K
from .kospi_mix import sim, stats, targets


def conds():
    d1 = K.synth(1.0)
    yield "합성-4.3% 1998~", d1, K.synth(2.0, -0.0428), "1998-01-01"
    yield "합성-2% 1998~", d1, K.synth(2.0, -0.02), "1998-01-01"
    r2, r1 = K.load("ETF122630")[["Close"]], K.load("ETF069500")[["Close"]]
    idx = r2.index.intersection(r1.index)
    yield "실제ETF 2011~", r1.loc[idx], r2.loc[idx], str(idx[300].date())


def main():
    rows = []
    for name, d1, d2, st in conds():
        k = d1["Close"]; r1, r2 = k.pct_change().fillna(0), d2["Close"].pct_change().fillna(0)
        rate = K.kr_rate(k.index)
        for cap, din, dout, kk in itertools.product((2.0, 1.5), (0.10, 0.15, 0.20, 0.25), (0.05, 0.075, 0.10), (0.0, 0.25, 0.5)):
            if dout >= din:
                continue
            E = targets(k, 99, dd_in=din, dd_out=dout, dd_k=kk, cap=cap).copy()
            E[:260] = 0
            eq, _ = sim(r1, r2, rate, E)
            c, m = stats(eq, st)
            rows.append((name, cap, din, dout, kk, c, m, c / -m))
    df = pd.DataFrame(rows, columns=["조건", "상한", "진입", "해제", "축소후비율", "CAGR", "MDD", "칼마"])
    df.to_csv(f"{K.OUT}/kospi_dd_grid.csv", index=False, encoding="utf-8-sig")
    for (name, cap), g in df.groupby(["조건", "상한"], sort=False):
        print(f"[{name} / 상한 {cap}] CAGR {g.CAGR.min():.1%}~{g.CAGR.max():.1%}  MDD {g.MDD.min():.1%}~{g.MDD.max():.1%}  "
              f"칼마 {g.칼마.min():.2f}~{g.칼마.max():.2f} 중앙 {g.칼마.median():.2f} (설정 {len(g)}개)")
    p = df.pivot_table(index=["상한", "진입", "해제", "축소후비율"], columns="조건", values="칼마")
    p["평균"] = p.mean(axis=1)
    print("\n칼마 상위 8 / 하위 4 (세 조건 평균)"); print(p.sort_values("평균", ascending=False).round(2).head(8).to_string())
    print(p.sort_values("평균").round(2).head(4).to_string())
    print("\n진입선별 평균 칼마:", p.groupby("진입")["평균"].mean().round(3).to_dict(),
          "\n축소후비율별:", p.groupby("축소후비율")["평균"].mean().round(3).to_dict())


if __name__ == "__main__":
    main()
