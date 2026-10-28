"""분할 진입(즉시 vs 5주 vs 10주) 비교. python -m laoer_backtest.ramp_study"""
import numpy as np
import pandas as pd
from multiprocessing import Pool
from . import data as D, sim, strategies as S
from .study import NDX_SPREAD

VARIANTS = [0, 5, 10]  # 즉시, 5주, 10주
HZ = [(126, "6개월"), (252, "1년"), (2520, "10년")]

def run_path(args):
    df, rate = args
    out = {}
    for wk in VARIANTS:
        df2 = df.iloc[:max(h for h, _ in HZ) + 1]
        res, _ = sim.backtest(df2, S.VolTargetRamp(weeks=wk), capital=11330, cash_rate=rate.reindex(df2.index).ffill())
        e = res["equity"].to_numpy()
        out[wk] = {lab: e[min(h, len(e) - 1)] / e[0] for h, lab in HZ}
        out[wk]["MDD1y"] = (res["equity"].iloc[:253] / res["equity"].iloc[:253].cummax() - 1).min()
    return out

def summarize(results, title):
    print(f"\n== {title} ({len(results)}개)")
    rows = []
    for wk in VARIANTS:
        for h, lab in HZ:
            r = np.array([x[wk][lab] for x in results]) - 1
            d = np.array([x[wk][lab] / x[0][lab] for x in results]) - 1
            rows.append(dict(방식="즉시" if wk == 0 else f"{wk}주분할", 기간=lab, 수익중앙=np.median(r), 하위10=np.quantile(r, .1),
                             상위90=np.quantile(r, .9), 즉시대비중앙=np.median(d), 즉시보다나음=(d > 0).mean()))
        m = np.array([x[wk]["MDD1y"] for x in results])
        rows.append(dict(방식="즉시" if wk == 0 else f"{wk}주분할", 기간="1년내MDD", 수익중앙=np.median(m), 하위10=np.quantile(m, .1)))
    print(pd.DataFrame(rows).round(3).to_string(index=False))

if __name__ == "__main__":
    paths = D.bootstrap_paths(300, years=10, spread=NDX_SPREAD, seed=42)
    syn = D.synth_tqqq("NDX", NDX_SPREAD); rs = D.short_rate(syn.index)
    starts = syn.index.to_series().resample("MS").first().dropna()
    starts = starts[starts <= syn.index[-1] - pd.DateOffset(years=10)]
    hist = [(syn.loc[t:], rs) for t in starts]
    with Pool(4) as p:
        summarize(p.map(run_path, hist), "역사 경로: 1986~2016 매월 시작")
        summarize(p.map(run_path, paths), "몬테카를로 300개")
