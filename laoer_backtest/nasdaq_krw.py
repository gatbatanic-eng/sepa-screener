"""TQQQ 변동성조절 규칙을 국내 상장 원화 나스닥100 ETF(최대 2배)로 옮기면 어떤가.
  python -m laoer_backtest.nasdaq_krw

- 원화 나스닥 1배 = (1+^NDX 수익률)(1+원/달러 수익률)-1 - 비용(실제 TIGER 미국나스닥100 133690과 2010-10~ 누적수익이 맞게 보정)
- 2배 = 같은 방식 (일간수익률 x2 - 보수 - 금리 x1), 비환헤지 가정
- 3배 = 현재 TQQQ 운용(USD TQQQ 합성)을 원화로 환산한 것
- 노출도 E = min(상한, 목표변동성/20일변동성(원화 기준)) x 추세(나스닥 50/200일선 아래면 x0.5), 1배·2배 ETF를 섞어 구현
- 비교는 세전. 해외 TQQQ는 양도세 22%(연 250만원 공제), 국내 상장 해외주식형 ETF는 배당소득세 15.4%.
"""
import numpy as np
import pandas as pd

from . import data as D, kospi_study as K, sim as SIM, strategies as S
from .kospi_mix import sim as sim_expo
from .kospi_lab import metrics
from .study import NDX_SPREAD

START = "2004-01-01"


def base():
    ndx = D.load("NDX")["Close"]
    fx = D.load("USDKRW")["Close"]
    idx = ndx.index.intersection(fx.index)
    ndx, fx = ndx.loc[idx], fx.loc[idx]
    return ndx, fx


def krw_series(spread):
    ndx, fx = base()
    r = (1 + ndx.pct_change().fillna(0)) * (1 + fx.pct_change().fillna(0)) - 1
    rate = K.kr_rate(ndx.index)
    r1 = r - (0.0007 + spread) / 252
    r2 = 2 * r - (0.0015 + rate + spread) / 252
    return ndx, fx, r1, r2, rate


def calibrate():
    real = pd.read_csv(f"{K.DIR}/E_133690.csv", index_col=0, parse_dates=True)["Close"]
    lo, hi = -0.05, 0.05
    for _ in range(40):
        mid = (lo + hi) / 2
        _, _, r1, _, _ = krw_series(mid)
        s = (1 + r1).cumprod().reindex(real.index).dropna()
        rr = real.reindex(s.index)
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        cs, cr = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs), (rr.iloc[-1] / rr.iloc[0]) ** (1 / yrs)
        lo, hi = (mid, hi) if cs > cr else (lo, mid)
    mid = (lo + hi) / 2
    _, _, r1, _, _ = krw_series(mid)
    c = r1.reindex(real.index).dropna().corr(real.pct_change().reindex(r1.index).dropna())
    return mid, c


def tqqq_krw(rate_usd=True):
    """현재 운용(TQQQ 3배, 목표 40% + 50/200)을 원화로 환산한 자본곡선."""
    ndx_usd = D.load("NDX")
    syn = D.synth_tqqq("NDX", NDX_SPREAD)
    fx = D.load("USDKRW")["Close"]
    idx = syn.index.intersection(fx.index)
    syn = syn.loc[idx]
    return syn, fx.loc[idx]


def run_tqqq(syn, fx, **kw):
    """USD 자본곡선을 원화로 환산 (환차손익 포함)."""
    st = S.VolTarget2(**kw)
    res, _ = SIM.backtest(syn, st, capital=10000.0, fee=0.0007, cash_rate=D.short_rate(syn.index))
    eq = res["equity"] * fx / fx.iloc[0]
    return eq


def main():
    sp, corr = calibrate()
    print(f"보정 스프레드 {sp:+.4f}, 실제 133690과 일간수익률 상관 {corr:.3f}")
    ndx, fx, r1, r2, rate = krw_series(sp)
    k_krw = (1 + r1 + 0.0).cumprod()  # 원화 1배 가격 (변동성 계산용)
    nd_usd = ndx
    cross = (nd_usd.rolling(50).mean() >= nd_usd.rolling(200).mean()).astype(float).shift(1).fillna(1.0).to_numpy()
    mult = lambda k_: np.where(cross > 0, 1.0, k_)
    n = len(r1); warm = np.arange(n) > 260
    from .kospi_mix import targets
    cfg = {"원화 나스닥 1배 보유": np.where(warm, 1.0, 0.0), "원화 나스닥 2배 보유": np.where(warm, 2.0, 0.0)}
    for tv in (0.30, 0.40, 0.50, 0.60):
        E = targets(k_krw, tv).copy()
        cfg[f"2배 한도 · 변동성조절 {tv:.0%}"] = E
        cfg[f"2배 한도 · 변동성조절 {tv:.0%} + 50/200추세"] = E * mult(0.5)
    cfg["2배 보유 + 50/200추세(아래면 1/2)"] = np.where(warm, 2.0, 0.0) * mult(0.5)
    cfg["2배 보유 + 낙폭방어(−15%에 절반)"] = targets(k_krw, 99, dd_in=0.15).copy()
    cfg["1배 보유 + 50/200추세(아래면 1/2)"] = np.where(warm, 1.0, 0.0) * mult(0.5)
    curves = {}
    for name, E in cfg.items():
        E = np.asarray(E, float).copy(); E[:260] = 0
        eq, ex = sim_expo(r1, r2, rate, E)
        curves[name] = (eq, ex.loc[START:].mean())
    syn, fx2 = tqqq_krw()
    for lab, kw in (("TQQQ 3배(현재 운용: 40% + 50/200, 원화 환산)", dict(target_vol=0.40, mom="cross", k=0.5, src="own")),
                    ("TQQQ 3배 변동성조절 35%(추세 없음, 원화 환산)", dict(target_vol=0.35))):
        kw2 = dict(kw)
        eq = run_tqqq(syn, fx2, **kw2)
        curves[lab] = (eq, np.nan)
    rows = []
    for name, (eq, ex) in curves.items():
        row = {"전략": name, "평균노출": ex}
        for lab, a in (("2005~", "2005-01-03"), ("2012~", "2012-01-02"), ("2018~", "2018-01-02")):
            m = metrics(eq, a)
            row[f"{lab} CAGR"], row[f"{lab} MDD"] = m["CAGR"], m["MDD"]
        row["칼마(2005~)"] = metrics(eq, "2005-01-03")["칼마"]
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(f"{K.OUT}/nasdaq_krw.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 70)
    f = df.copy()
    for c in f.columns[1:]:
        f[c] = f[c].map(lambda v: "-" if pd.isna(v) else f"{v:.1%}" if "칼마" not in c else f"{v:.2f}")
    print(f.to_string(index=False))


if __name__ == "__main__":
    main()
