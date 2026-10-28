"""후보 전략 몬테카를로 검증 (블록 부트스트랩).

  python -m laoer_backtest.montecarlo [경로수]

그리드 상위 설정은 하나뿐인 역사 경로에 맞춰졌을 수 있다. ^NDX 일봉을 1년 블록으로 섞은 10년 가상 경로를 만들고,
모든 후보를 같은 경로에서 돌려 분포를 비교한다. 결과: results/montecarlo.csv
"""
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from . import data as D, sim, strategies as S
from .study import NDX_SPREAD, OUT

CANDIDATES = [
    (S.LumpSum, {}),
    (S.DCA, dict(every=21, n_buys=40)),
    (S.LaoerX, dict(n_split=40, target=0.10, big=0.10, cut="quarter", trend=0)),    # V1 기본
    (S.LaoerX, dict(n_split=40, target=0.10, big=0.10, cut="quarter", trend=200)),
    (S.LaoerX, dict(n_split=60, target=0.10, big=0.05, cut="quarter", trend=200)),
    (S.LaoerX, dict(n_split=40, target=0.10, big=0.05, cut="quarter", trend=200)),
    (S.LaoerX, dict(n_split=40, target=0.15, big=0.20, cut="quarter", trend=200)),
    (S.LaoerX, dict(n_split=20, target=0.20, big=0.20, cut="quarter", trend=200)),
    (S.LaoerX, dict(n_split=60, target=0.10, big=0.05, cut="none", trend=200)),     # 그리드 1위
    (S.LaoerX, dict(n_split=60, target=0.10, big=0.10, cut="none", trend=200)),     # 1위 이웃
    (S.LaoerX, dict(n_split=40, target=0.075, big=0.10, cut="none", trend=200)),
    (S.ValueAvg, dict(every=21, n=80, g=0.0, allow_sell=True)),
    (S.TrendHold, dict(ma=150, band=0.05)),
    (S.TrendHold, dict(ma=200, band=0.03)),
    (S.VolTarget, dict(target_vol=0.4, lb=20)),
    (S.VolTarget, dict(target_vol=0.3, lb=60)),
    (S.Rebalance, dict(w=0.3, band=0.2)),
    (S.Rebalance, dict(w=0.5, band=0.2)),
]
_P = {}
KRW_CAPITAL = 15_000_000  # 세금 계산은 계좌 규모에 따라 달라진다 (연 250만원 공제)
FX = 1324.0
TAX = dict(rate=0.22, deduction=2.5e6, fx=FX)


def _init(n):
    _P["paths"] = D.bootstrap_paths(n, years=10, spread=NDX_SPREAD, seed=42)


def evaluate(cfg):
    """cfg = (전략 클래스, 파라미터) 또는 (클래스, 파라미터, 세금여부)"""
    cls, kw, taxed = (*cfg, False)[:3]
    rows = []
    for df, rate in _P["paths"]:
        st = cls(**kw)
        res, _ = sim.backtest(df, st, capital=KRW_CAPITAL / FX, cash_rate=rate, tax=TAX if taxed else None)
        s = sim.stats(res)
        rows.append((s["CAGR"], s["MDD"], s["배수"]))
    a = np.array(rows)
    cagr, mdd, mult = a[:, 0], a[:, 1], a[:, 2]
    return dict(그룹=cls.__name__, 전략=cls(**kw).name, 세금=taxed,
                CAGR중앙=np.median(cagr), CAGR하위10=np.quantile(cagr, 0.1), CAGR하위5=np.quantile(cagr, 0.05),
                MDD중앙=np.median(mdd), MDD하위10=np.quantile(mdd, 0.1),
                손실확률=(mult < 1).mean(), 반토막확률=(mult < 0.5).mean(),
                칼마중앙=np.median(cagr / -mdd))


def run(cands, n=300, out="montecarlo.csv"):
    with Pool(os.cpu_count(), initializer=_init, initargs=(n,)) as p:
        rows = p.map(evaluate, cands, chunksize=1)
    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, out), index=False)
    return df


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
    print(run(CANDIDATES, int(sys.argv[1]) if len(sys.argv) > 1 else 300).round(3).to_string(index=False))
