"""전략군 x 파라미터 그리드 탐색.

  python -m laoer_backtest.grid [--quick]

각 설정마다
  - 롤링: 합성 TQQQ, 분기 첫 거래일 시작 10년 보유 (1986~2016 시작, 약 120개 시나리오)
  - 고정: 실제 2010~, 합성 1985~, 닷컴 2000-03~
을 돌려 results/grid.csv 에 한 줄씩 저장한다. 대기 현금에는 13주 T-bill 금리 이자를 붙인다.
"""
import itertools
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from . import data as D, sim, strategies as S
from .study import NDX_SPREAD, OUT

HORIZON = 10
_G = {}


def configs(quick=False):
    c = [(S.LumpSum, {}), (S.DCA, dict(every=21, n_buys=40)), (S.DCA, dict(every=5, n_buys=40))]
    ns, tps, bigs = ([20, 40], [0.05, 0.10, 0.20], [0.10]) if quick else \
        ([20, 30, 40, 60], [0.05, 0.075, 0.10, 0.15, 0.20], [0.05, 0.10, 0.20])
    for n, tp, big, cut, tr in itertools.product(ns, tps, bigs, ["quarter", "none", "all"], [0, 200]):
        c.append((S.LaoerX, dict(n_split=n, target=tp, big=big, cut=cut, trend=tr)))
    for ev, n, g, sell in itertools.product([5, 21], [20, 40, 80], [0.0, 0.005, 0.01, 0.02], [True, False]):
        c.append((S.ValueAvg, dict(every=ev, n=n, g=g if ev == 21 else g / 4, allow_sell=sell)))
    tiers = {"완만": ((0, 1), (0.2, 1.5), (0.4, 2), (0.6, 3)),
             "보통": ((0, 1), (0.2, 2), (0.4, 3), (0.6, 4)),
             "공격": ((0, 0.5), (0.2, 2), (0.4, 4), (0.6, 8))}
    for ev, n, t in itertools.product([5, 21], [40, 80, 160], tiers.values()):
        c.append((S.DipWeighted, dict(every=ev, n=n if ev == 5 else n // 4, tiers=t)))
    for ma, band in itertools.product([50, 100, 150, 200, 250], [0.0, 0.03, 0.05]):
        c.append((S.TrendHold, dict(ma=ma, band=band)))
    for tv, lb in itertools.product([0.3, 0.4, 0.5, 0.6], [20, 60]):
        c.append((S.VolTarget, dict(target_vol=tv, lb=lb)))
    for w, band in itertools.product([0.3, 0.5, 0.7], [0.05, 0.1, 0.2]):
        c.append((S.Rebalance, dict(w=w, band=band)))
    return c


def _init():
    syn = D.synth_tqqq("NDX", NDX_SPREAD)
    real = D.load("TQQQ")[["Open", "High", "Low", "Close"]]
    _G.update(syn=syn, real=real, rs=D.short_rate(syn.index), rr=D.short_rate(real.index))
    starts = syn.index.to_series().resample("QS").first().dropna()
    _G["starts"] = starts[starts <= syn.index[-1] - pd.DateOffset(years=HORIZON)]


def _run(df, cls, kw, rate):
    st = cls(**kw)
    res, acct = sim.backtest(df, st, cash_rate=rate.reindex(df.index))
    return st, sim.stats(res, st.cycles() if hasattr(st, "_cycles") else None)


def evaluate(cfg):
    cls, kw = cfg
    syn, rs = _G["syn"], _G["rs"]
    roll = []
    for t0 in _G["starts"]:
        _, s = _run(syn.loc[t0:t0 + pd.DateOffset(years=HORIZON)], cls, kw, rs)
        roll.append(s)
    r = pd.DataFrame(roll)
    st, full = _run(syn, cls, kw, rs)
    _, real = _run(_G["real"], cls, kw, _G["rr"])
    _, dot = _run(syn.loc["2000-03-10":], cls, kw, rs)
    return dict(
        그룹=cls.__name__, 전략=st.name, 파라미터=repr(kw),
        롤_CAGR중앙=r.CAGR.median(), 롤_CAGR하위10=r.CAGR.quantile(0.1), 롤_CAGR최악=r.CAGR.min(),
        롤_MDD중앙=r.MDD.median(), 롤_MDD하위10=r.MDD.quantile(0.1),
        롤_손실확률=(r.배수 < 1).mean(), 롤_칼마중앙=(r.CAGR / -r.MDD).median(),
        롤_투입률=r.평균투입률.mean(),
        실_CAGR=real["CAGR"], 실_MDD=real["MDD"],
        합_CAGR=full["CAGR"], 합_MDD=full["MDD"], 닷컴_CAGR=dot["CAGR"], 닷컴_MDD=dot["MDD"],
    )


def main(quick=False):
    cfgs = configs(quick)
    print(f"{len(cfgs)}개 설정", flush=True)
    rows = []
    with Pool(os.cpu_count(), initializer=_init) as p:
        for k, row in enumerate(p.imap_unordered(evaluate, cfgs, chunksize=2), 1):
            rows.append(row)
            if k % 50 == 0:
                print(f"  {k}/{len(cfgs)}", flush=True)
    df = pd.DataFrame(rows).sort_values("롤_칼마중앙", ascending=False)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "grid_quick.csv" if quick else "grid.csv"), index=False)
    return df


if __name__ == "__main__":
    main("--quick" in sys.argv)
