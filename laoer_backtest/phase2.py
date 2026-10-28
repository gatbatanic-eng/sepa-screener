"""2차 검증: 세금·환율, 변동성조절 파라미터 안정성, 결합 전략, 실행 지연.

  python -m laoer_backtest.phase2

결과: results/phase2_*.csv
  real_krw   실제 TQQQ 2010~ 원화 기준(환율 반영), 세전/세후
  mc_tax     후보 전략 몬테카를로 세전/세후
  mc_vol     변동성조절 파라미터 이웃 (세후)
  mc_combo   결합 전략·시가 체결 (세후)
"""
import itertools
import os

import pandas as pd

from . import data as D, sim, strategies as S
from .montecarlo import KRW_CAPITAL, run
from .study import OUT

MAIN = [
    (S.LumpSum, {}),
    (S.DCA, dict(every=21, n_buys=40)),
    (S.LaoerX, dict(n_split=40, target=0.10, big=0.10, cut="quarter")),
    (S.LaoerX, dict(n_split=60, target=0.10, big=0.05, cut="quarter", trend=200)),
    (S.LaoerX, dict(n_split=60, target=0.10, big=0.05, cut="none", trend=200)),
    (S.ValueAvg, dict(every=21, n=80, g=0.0, allow_sell=True)),
    (S.TrendHold, dict(ma=150, band=0.05)),
    (S.VolTarget, dict(target_vol=0.4, lb=20)),
    (S.VolTarget, dict(target_vol=0.3, lb=60)),
    (S.Rebalance, dict(w=0.3, band=0.2)),
    (S.Rebalance, dict(w=0.5, band=0.2)),
]
VOL_GRID = [(S.VolTarget, dict(target_vol=tv, lb=lb, band=b), True)
            for tv, lb, b in itertools.product([0.3, 0.35, 0.4, 0.45, 0.5], [10, 20, 40, 60], [0.05, 0.1, 0.2])]
COMBO = [
    (S.VolTarget, dict(target_vol=0.4, lb=20, trend=200), True),
    (S.VolTarget, dict(target_vol=0.5, lb=20, trend=200), True),
    (S.VolTarget, dict(target_vol=0.6, lb=20, trend=200), True),
    (S.VolTarget, dict(target_vol=0.4, lb=20, at="open"), True),
    (S.VolTarget, dict(target_vol=0.3, lb=60, at="open"), True),
    (S.Rebalance, dict(w=0.3, band=0.2, at="open"), True),
    (S.LaoerX, dict(n_split=60, target=0.10, big=0.05, cut="quarter", trend=200, vol_target=0.6), True),
    (S.LaoerX, dict(n_split=40, target=0.10, big=0.10, cut="quarter", trend=200, vol_target=0.6), True),
    (S.LaoerX, dict(n_split=40, target=0.10, big=0.10, cut="quarter", vol_target=0.6), True),
]


def real_krw():
    df = D.load("TQQQ")[["Open", "High", "Low", "Close"]]
    fx = D.load("USDKRW")["Close"]
    fx = fx.reindex(df.index.union(fx.index)).ffill().bfill().reindex(df.index)
    cr = D.short_rate(df.index)
    rows = []
    for cls, kw in MAIN:
        for taxed in (False, True):
            st = cls(**kw)
            res, _ = sim.backtest(df, st, capital=KRW_CAPITAL / fx.iloc[0], cash_rate=cr,
                                  tax=dict(rate=0.22, deduction=2.5e6, fx=fx) if taxed else None)
            krw = res.copy()
            krw["equity"] = res["equity"] * fx
            krw.attrs["liq_tax"] = res.attrs.get("liq_tax", 0.0) * fx.iloc[-1]
            s = sim.stats(krw)
            rows.append(dict(전략=st.name, 세금=taxed, 최종원=krw["equity"].iloc[-1] - krw.attrs["liq_tax"],
                             CAGR=s["CAGR"], MDD=s["MDD"], 최악연도=s["최악연도"]))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
    r = real_krw(); r.to_csv(os.path.join(OUT, "phase2_real_krw.csv"), index=False)
    print(r.round(3).to_string(index=False), flush=True)
    m = run([(c, k, t) for c, k in MAIN for t in (False, True)], out="phase2_mc_tax.csv")
    print(m.round(3).to_string(index=False), flush=True)
    v = run(VOL_GRID, out="phase2_mc_vol.csv")
    print(v.sort_values("칼마중앙", ascending=False).round(3).to_string(index=False), flush=True)
    c = run(COMBO, out="phase2_mc_combo.csv")
    print(c.round(3).to_string(index=False), flush=True)
