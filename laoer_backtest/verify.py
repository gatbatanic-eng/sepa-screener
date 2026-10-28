"""최종 추천(변동성조절) 재검증: 조건을 바꿔도 순위가 유지되는지 본다.

  python -m laoer_backtest.verify

시나리오 (모두 같은 후보 9개를 같은 경로에서 비교):
  mc_base      블록 1년, 10년, 경로 300개, seed 42 (기존 검증과 동일 조건)
  mc_seed7     다른 난수 seed
  mc_block3m   블록 3개월 (짧은 블록: 추세가 덜 이어짐)
  mc_block2y   블록 2년 (긴 블록: 긴 강세/약세장이 그대로 이어짐)
  mc_5y/mc_20y 보유 기간 5년 / 20년
  mc_nocash    대기 현금 이자 0 (현금 비중 큰 전략에 불리)
  mc_slip      거래마다 비용 0.27% (수수료 0.07% + 슬리피지 0.2%)
  mc_tax       해외주식 양도세 반영 (계좌 1,500만원)
  hist_*       역사 경로: 합성 1985~2009, 실제 TQQQ 2010~, 10년 롤링(매월 시작)
결과: results/verify.csv
"""
import os
from multiprocessing import Pool

import numpy as np
import pandas as pd

from . import data as D, sim, strategies as S
from .montecarlo import FX, KRW_CAPITAL, TAX
from .study import NDX_SPREAD, OUT

QQQ_SPREAD = -0.007  # 1배는 배당(약 0.7%)만 보정
CANDS = [
    ("QQQ 보유(1배)", "1x", S.LumpSum, {}),
    ("TQQQ 보유", "3x", S.LumpSum, {}),
    ("TQQQ 월적립 40개월", "3x", S.DCA, dict(every=21, n_buys=40)),
    ("라오어 V1 기본", "3x", S.LaoerX, dict(n_split=40, target=0.10, big=0.10, cut="quarter")),
    ("라오어 개선(n60,MA200)", "3x", S.LaoerX, dict(n_split=60, target=0.10, big=0.05, cut="quarter", trend=200)),
    ("추세보유 MA150", "3x", S.TrendHold, dict(ma=150, band=0.05)),
    ("리밸런싱 30%", "3x", S.Rebalance, dict(w=0.3, band=0.2)),
    ("변동성조절 35% ★", "3x", S.VolTarget, dict(target_vol=0.35, lb=20)),
    ("변동성조절 40%", "3x", S.VolTarget, dict(target_vol=0.40, lb=20)),
]
MC = {
    "mc_base": dict(), "mc_seed7": dict(seed=7), "mc_block3m": dict(block=63), "mc_block2y": dict(block=504),
    "mc_5y": dict(years=5), "mc_20y": dict(years=20),
    "mc_nocash": dict(nocash=True), "mc_slip": dict(fee=0.0027), "mc_tax": dict(tax=True),
}


def _paths(years=10, block=252, seed=42, n=300):
    p3 = D.bootstrap_paths(n, years, block, spread=NDX_SPREAD, seed=seed)
    p1 = D.bootstrap_paths(n, years, block, spread=QQQ_SPREAD, seed=seed, lev=1.0, expense=0.002)
    return p3, p1


def _one(args):
    """한 시나리오 x 한 후보 → 경로별 (CAGR, MDD, 배수)"""
    scen, ci = args
    opt = MC[scen]
    p3, p1 = _paths(opt.get("years", 10), opt.get("block", 252), opt.get("seed", 42))
    label, asset, cls, kw = CANDS[ci]
    rows = []
    for (df, rate) in (p1 if asset == "1x" else p3):
        res, _ = sim.backtest(df, cls(**kw), capital=KRW_CAPITAL / FX, fee=opt.get("fee", 0.0007),
                              cash_rate=0.0 if opt.get("nocash") else rate, tax=TAX if opt.get("tax") else None)
        s = sim.stats(res)
        rows.append((s["CAGR"], s["MDD"], s["배수"]))
    return scen, label, np.array(rows)


def _hist():
    syn3 = D.synth_tqqq("NDX", NDX_SPREAD)
    ndx = D.load("NDX")
    syn1 = D.lever(ndx[["Open", "High", "Low", "Close"]], D.short_rate(ndx.index), QQQ_SPREAD, 1.0, 0.002)
    real3 = D.load("TQQQ")[["Open", "High", "Low", "Close"]]
    real1 = D.load("QQQ")
    real1 = pd.DataFrame({k: real1[k] * real1["Adj Close"] / real1["Close"] for k in ("Open", "High", "Low", "Close")})
    real1 = real1.loc[real3.index[0]:]
    rs = D.short_rate(syn3.index)
    out = []
    for scen, d3, d1 in [("hist_1985_2009", syn3.loc[:"2009-12-31"], syn1.loc[:"2009-12-31"]),
                         ("hist_2010_real", real3, real1)]:
        for label, asset, cls, kw in CANDS:
            df = d1 if asset == "1x" else d3
            res, _ = sim.backtest(df, cls(**kw), capital=KRW_CAPITAL / FX, cash_rate=rs.reindex(df.index).ffill())
            s = sim.stats(res)
            out.append((scen, label, np.array([[s["CAGR"], s["MDD"], s["배수"]]])))
    starts = syn3.index.to_series().resample("MS").first().dropna()
    starts = starts[starts <= syn3.index[-1] - pd.DateOffset(years=10)]
    for label, asset, cls, kw in CANDS:
        base = syn1 if asset == "1x" else syn3
        rows = []
        for t0 in starts:
            df = base.loc[t0:t0 + pd.DateOffset(years=10)]
            res, _ = sim.backtest(df, cls(**kw), capital=KRW_CAPITAL / FX, cash_rate=rs.reindex(df.index).ffill())
            s = sim.stats(res)
            rows.append((s["CAGR"], s["MDD"], s["배수"]))
        out.append(("hist_rolling10y", label, np.array(rows)))
    return out


def _summ(scen, label, a):
    c, m, x = a[:, 0], a[:, 1], a[:, 2]
    return dict(시나리오=scen, 전략=label, n=len(a), CAGR중앙=np.median(c), CAGR하위10=np.quantile(c, 0.1),
                MDD중앙=np.median(m), MDD하위10=np.quantile(m, 0.1), 손실확률=(x < 1).mean(),
                칼마중앙=np.median(c / np.maximum(-m, 1e-9)))


def main():
    jobs = [(s, i) for s in MC for i in range(len(CANDS))]
    with Pool(os.cpu_count()) as p:
        res = p.map(_one, jobs, chunksize=1)
    res += _hist()
    df = pd.DataFrame([_summ(*r) for r in res])
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "verify.csv"), index=False)
    return df


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20); pd.set_option("display.max_rows", 200)
    print(main().round(3).to_string(index=False))
