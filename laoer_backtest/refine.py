"""변동성조절 정교화: 변동성 원천·계산 방식, 모멘텀 결합 비교.

  python -m laoer_backtest.refine            # 1단계: 56개 설정 x (몬테카를로 300 + 역사 롤링 분기 시작)
  python -m laoer_backtest.refine --final    # 2단계: 후보를 verify 와 같은 다수 조건에서 재검증

변동성 추정 방식마다 같은 목표치에서도 평균 비중이 다르므로, 목표 변동성 30~50%를 같이 훑어
'같은 위험(낙폭)에서 수익이 더 높은가'로 비교한다. 결과: results/refine_stage1.csv, refine_final.csv
"""
import itertools
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from . import data as D, sim, strategies as S
from .montecarlo import FX, KRW_CAPITAL, TAX
from .study import NDX_SPREAD, OUT

V = S.VolTarget2
TVS = [0.30, 0.35, 0.40, 0.45, 0.50]
EST = [dict(), dict(src="index"), dict(est="ewma", lam=0.94), dict(est="ewma", lam=0.97),
       dict(est="max", lb=20, lb_long=60), dict(est="park"), dict(est="down")]
MOM = [dict(mom="ma", ma=200, k=0.0), dict(mom="ma", ma=200, k=0.5), dict(mom="ma", ma=100, k=0.5),
       dict(mom="ret", mom_lb=126, k=0.5), dict(mom="ret", mom_lb=252, k=0.5),
       dict(mom="cross", k=0.5), dict(mom="scale", mom_lb=126, k=0.25)]


def stage1_configs():
    c = [dict(target_vol=tv, **e) for e, tv in itertools.product(EST, TVS)]
    c += [dict(target_vol=tv, **m) for m, tv in itertools.product(MOM, [0.35, 0.40, 0.50])]
    return c


_G = {}


def _init(spec):
    years, block, seed, n = spec
    _G["paths"] = D.bootstrap_paths(n, years + 1, block, spread=NDX_SPREAD, seed=seed)  # +1년: 워밍업
    syn = D.synth_tqqq("NDX", NDX_SPREAD)
    _G["syn"], _G["rs"] = syn, D.short_rate(syn.index)


def _stats_after_warmup(res):
    r = res.iloc[V.WARMUP + 1:]  # 투자 시작일부터 평가
    out = r.copy(); out.attrs = res.attrs
    return sim.stats(out)


def _eval(args):
    kw, opt = args
    rows = []
    for df, rate in _G["paths"]:
        res, _ = sim.backtest(df, V(**kw), capital=KRW_CAPITAL / FX, cash_rate=0.0 if opt.get("nocash") else rate,
                              fee=opt.get("fee", 0.0007), tax=TAX if opt.get("tax") else None)
        s = _stats_after_warmup(res)
        rows.append((s["CAGR"], s["MDD"], s["배수"], s["평균투입률"]))
    mc = np.array(rows)
    out = dict(설정=V(**kw).name, 파라미터=repr(kw), **_summ("mc", mc))
    if opt.get("hist", True):
        syn, rs = _G["syn"], _G["rs"]
        starts = syn.index.to_series().resample(opt.get("step", "QS")).first().dropna()
        starts = starts[(starts >= syn.index[V.WARMUP + 1]) & (starts <= syn.index[-1] - pd.DateOffset(years=10))]
        starts = starts[(starts >= opt.get("hist_from", "1900")) & (starts <= opt.get("hist_to", "2100"))]
        h = []
        for t0 in starts:
            i0 = syn.index.get_loc(t0)
            df = syn.iloc[i0 - V.WARMUP - 1:].loc[:t0 + pd.DateOffset(years=10)]
            res, _ = sim.backtest(df, V(**kw), capital=KRW_CAPITAL / FX, cash_rate=rs.reindex(df.index))
            s = _stats_after_warmup(res)
            h.append((s["CAGR"], s["MDD"], s["배수"], s["평균투입률"]))
        out.update(_summ("hist", np.array(h)))
    return out


def _summ(p, a):
    c, m, x, e = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    return {f"{p}_CAGR중앙": np.median(c), f"{p}_CAGR하위10": np.quantile(c, .1), f"{p}_MDD중앙": np.median(m),
            f"{p}_MDD하위10": np.quantile(m, .1), f"{p}_손실확률": (x < 1).mean(),
            f"{p}_칼마중앙": np.median(c / np.maximum(-m, 1e-9)), f"{p}_평균비중": e.mean()}


def run(cfgs, spec=(10, 252, 42, 300), opt=None, out="refine_stage1.csv"):
    with Pool(os.cpu_count(), initializer=_init, initargs=(spec,)) as p:
        rows = p.map(_eval, [(k, opt or {}) for k in cfgs], chunksize=1)
    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    if out:
        df.to_csv(os.path.join(OUT, out), index=False)
    return df


FINAL_SCEN = {
    # 모멘텀은 몇 달~1년 넘게 이어지는 추세에 기대므로, 블록이 짧으면 경로를 섞는 과정에서 추세가 끊겨 불리하다.
    # 그래서 블록 1·2·3년을 모두 본다.
    "블록1년": ((10, 252, 42, 300), {"step": "MS"}),
    "블록2년": ((10, 504, 42, 300), {"hist": False}),
    "블록3년": ((10, 756, 42, 300), {"hist": False}),
    "블록2년_다른난수": ((10, 504, 7, 300), {"hist": False}),
    "블록2년_20년": ((20, 504, 42, 300), {"hist": False}),
    "블록2년_슬리피지": ((10, 504, 42, 300), {"hist": False, "fee": 0.0027}),
    "블록2년_세후": ((10, 504, 42, 300), {"hist": False, "tax": True}),
    "블록2년_현금이자0": ((10, 504, 42, 300), {"hist": False, "nocash": True}),
    "역사_전반(1986~2000시작)": ((10, 252, 42, 1), {"step": "MS", "hist_to": "2000-12-31"}),
    "역사_후반(2001~2016시작)": ((10, 252, 42, 1), {"step": "MS", "hist_from": "2001-01-01"}),
}
FINAL_CANDS = [
    dict(target_vol=0.35), dict(target_vol=0.40),
    dict(target_vol=0.40, mom="ma", ma=100, k=0.5), dict(target_vol=0.40, mom="ma", ma=200, k=0.5),
    dict(target_vol=0.35, mom="cross", k=0.5), dict(target_vol=0.40, mom="cross", k=0.5),
    dict(target_vol=0.50, mom="cross", k=0.5), dict(target_vol=0.40, mom="ret", mom_lb=252, k=0.5),
]


def final(cfgs):
    frames = []
    for name, (spec, opt) in FINAL_SCEN.items():
        print(f"  {name}", flush=True)
        d = run(cfgs, spec, opt, out=None); d.insert(0, "시나리오", name); frames.append(d)
    df = pd.concat(frames)
    df.to_csv(os.path.join(OUT, "refine_final.csv"), index=False)
    return df


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 300)
    if "--final" in sys.argv:
        d = final(FINAL_CANDS)
        print(d.drop(columns=["파라미터"]).round(3).to_string(index=False))
    else:
        d = run(stage1_configs())
        print(d.drop(columns=["파라미터"]).round(3).to_string(index=False))
