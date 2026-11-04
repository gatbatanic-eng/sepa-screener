"""레버리지 비중 비교 연구(고정 레버리지 1~3배 vs 현재 변동성조절 규칙). 설계는 2026-11-04에 결과를 보기 전에 고정했다. python -m laoer_backtest.leverage_study
- 기초: 나스닥100 가격지수(^NDX, 1985-10~) 일간 수익률 + 단기금리(^IRX). 배당·세금·환율 미반영.
- TQQQ 대용: lever(lev=3, 운용보수 0.95%, 스프레드 calibrate()), QQQ 대용: lever(lev=1, 보수 0.2%).
- 고정 레버리지 L: TQQQ 비중 w=(L-1)/2, QQQ 비중 1-w 를 매일 재조정(L=1은 QQQ, L=3은 TQQQ). 거래비용 0.
- 변동성조절(현재 규칙): TQQQ 목표 비중 = min(1, 0.40/20일 변동성), 나스닥100 50일선 < 200일선이면 x0.5, 현재 비중이 목표와 10%p 넘게 다를 때만 조정, 나머지는 단기금리 현금.
- 평가: 전체 기간, 하락 구간(닷컴 1999-03~2002-10, 금융위기 2007-10~2009-03, 2021-11~2022-12), 롤링 5·10년 CAGR 분포, 블록 부트스트랩 10년 경로 2,000개(블록 252일).
한계: 합성 TQQQ(실제 추적오차·스왑 비용 차이), 가격지수라 절대 수익은 낮게 나옴, 과거 구간 편향, 세금·환율 미반영. 투자 권유가 아니다."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data as D

LEVS = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0)
EPISODES = {"닷컴 1999-03~2002-10": ("1999-03-10", "2002-10-09"), "금융위기 2007-10~2009-03": ("2007-10-09", "2009-03-09"), "2021-11~2022-12": ("2021-11-19", "2022-12-28")}
VT = {"target": 0.40, "band": 0.10, "trend_mult": 0.5, "win": 20}


def build(spread: float | None = None):
    src = D.load("NDX")
    rate = D.short_rate(src.index)
    if spread is None:
        spread = D.calibrate()
    t3 = D.lever(src, rate, spread, lev=3.0)
    q1 = D.lever(src, rate, 0.0, lev=1.0, expense=0.002)
    r = pd.DataFrame({"t": t3["Close"].pct_change(), "q": q1["Close"].pct_change(), "ndx": src["Close"], "rate": rate}).dropna()
    return r, spread


def const_returns(r: pd.DataFrame, lev: float) -> pd.Series:
    w = (lev - 1) / 2
    return (1 - w) * r["q"] + w * r["t"]


def vol_target_returns(t: np.ndarray, ndx: np.ndarray, rate: np.ndarray, p: dict = VT) -> np.ndarray:
    """일별 수익률. 신호는 t일 종가로 계산해 t+1일 수익부터 반영."""
    n = len(t)
    out = np.zeros(n)
    lr = np.log1p(t)
    sma50 = pd.Series(ndx).rolling(50).mean().to_numpy()
    sma200 = pd.Series(ndx).rolling(200).mean().to_numpy()
    w = 0.0
    for i in range(1, n):
        out[i] = w * t[i] + (1 - w) * rate[i] / 252
        w = w * (1 + t[i]) / (1 + out[i])                      # 하루 지난 뒤의 비중(드리프트)
        if i >= p["win"] and not np.isnan(sma200[i]):
            vol = lr[i - p["win"] + 1:i + 1].std(ddof=1) * np.sqrt(252)
            target = min(1.0, p["target"] / vol) if vol > 0 else 1.0
            if sma50[i] < sma200[i]:
                target *= p["trend_mult"]
            if abs(w - target) > p["band"]:
                w = target
    return out


def stats(ret: pd.Series) -> dict:
    eq = (1 + ret).cumprod()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    dd = (eq / eq.cummax() - 1)
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    yearly = eq.groupby(eq.index.year).last().pct_change()
    yearly.iloc[0] = eq.groupby(eq.index.year).last().iloc[0] / 1 - 1
    return {"cagr": cagr, "mdd": dd.min(), "calmar": cagr / -dd.min() if dd.min() < 0 else np.nan, "worstYear": yearly.min(), "multiple": eq.iloc[-1], "vol": ret.std() * np.sqrt(252)}


def episode_drawdown(ret: pd.Series, lo: str, hi: str) -> float:
    sl = ret[(ret.index >= lo) & (ret.index <= hi)]
    eq = (1 + sl).cumprod()
    return float(eq.iloc[-1] - 1)


def rolling_cagr(ret: pd.Series, years: int) -> pd.Series:
    eq = np.log1p(ret).cumsum()
    n = 252 * years
    return np.exp((eq - eq.shift(n)) / years) - 1


def bootstrap(r: pd.DataFrame, n_paths=2000, years=10, block=252, seed=0) -> dict:
    rng = np.random.default_rng(seed)
    arr = r[["t", "q", "rate"]].to_numpy()
    ndx_ret = r["ndx"].pct_change().fillna(0).to_numpy()
    nd = years * 252
    res = {f"L{l}": [] for l in LEVS}
    res["VT"] = []
    for _ in range(n_paths):
        starts = rng.integers(0, len(arr) - block, size=nd // block + 1)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:nd]
        seq, nr = arr[idx], ndx_ret[idx]
        level = 100 * np.cumprod(1 + nr)
        for l in LEVS:
            w = (l - 1) / 2
            rr = (1 - w) * seq[:, 1] + w * seq[:, 0]
            eq = np.cumprod(1 + rr)
            res[f"L{l}"].append((eq[-1] ** (1 / years) - 1, (eq / np.maximum.accumulate(eq) - 1).min()))
        rr = vol_target_returns(seq[:, 0], level, seq[:, 2])
        eq = np.cumprod(1 + rr)
        res["VT"].append((eq[-1] ** (1 / years) - 1, (eq / np.maximum.accumulate(eq) - 1).min()))
    out = {}
    for k, v in res.items():
        a = np.array(v)
        out[k] = {"medianCagr": float(np.median(a[:, 0])), "p5Cagr": float(np.percentile(a[:, 0], 5)), "probLoss": float((a[:, 0] < 0).mean()),
                  "medianMdd": float(np.median(a[:, 1])), "probMdd60": float((a[:, 1] < -0.6).mean()), "probMdd80": float((a[:, 1] < -0.8).mean())}
    return out


def main() -> None:
    r, spread = build()
    rets = {f"L{l}": const_returns(r, l) for l in LEVS}
    rets["VT"] = pd.Series(vol_target_returns(r["t"].to_numpy(), r["ndx"].to_numpy(), r["rate"].to_numpy()), index=r.index)
    names = {f"L{l}": f"고정 {l:g}배" for l in LEVS} | {"VT": "변동성조절(현재 규칙)"}
    o = ["# 레버리지 비중 비교 (고정 1~3배 vs 변동성조절)", "", "> 설계 2026-11-04 고정 · 합성 TQQQ · 가격지수 · 세금·환율 미반영 · 투자 권유가 아닙니다.", "",
         f"데이터: ^NDX {r.index[0].date()} ~ {r.index[-1].date()} ({len(r):,}거래일), 합성 TQQQ 스프레드 {spread * 100:.2f}%p/년", "", "```", __doc__.strip(), "```", ""]
    full = {k: stats(v) for k, v in rets.items()}
    o += ["## 전체 기간", "", "| 전략 | 연환산 수익 | 최대낙폭 | 칼마 | 최악의 해 | 배수 | 연 변동성 |", "|---|---|---|---|---|---|---|"]
    for k, s in full.items():
        o.append(f"| {names[k]} | {s['cagr'] * 100:.1f}% | {s['mdd'] * 100:.1f}% | {s['calmar']:.2f} | {s['worstYear'] * 100:.0f}% | {s['multiple']:.0f}배 | {s['vol'] * 100:.0f}% |")
    best_c = max((k for k in full if k != "VT"), key=lambda k: full[k]["cagr"])
    best_k = max((k for k in full if k != "VT"), key=lambda k: full[k]["calmar"])
    o += ["", f"- 고정 레버리지 중 연환산 수익 최고: **{names[best_c]}**, 칼마 최고: **{names[best_k]}**", ""]
    o += ["## 하락 구간 누적 수익(구간 시작~끝)", "", "| 전략 | " + " | ".join(EPISODES) + " |", "|---|" + "---|" * len(EPISODES)]
    for k, v in rets.items():
        o.append(f"| {names[k]} | " + " | ".join(f"{episode_drawdown(v, *ep) * 100:.0f}%" for ep in EPISODES.values()) + " |")
    o += ["", "## 롤링 보유 기간별 연환산 수익 분포 (모든 시작일)", "", "| 전략 | 5년 중앙값 | 5년 하위5% | 5년 손실확률 | 10년 중앙값 | 10년 하위5% | 10년 손실확률 |", "|---|---|---|---|---|---|---|"]
    for k, v in rets.items():
        a, b = rolling_cagr(v, 5).dropna(), rolling_cagr(v, 10).dropna()
        o.append(f"| {names[k]} | {a.median() * 100:.1f}% | {a.quantile(.05) * 100:.1f}% | {(a < 0).mean() * 100:.0f}% | {b.median() * 100:.1f}% | {b.quantile(.05) * 100:.1f}% | {(b < 0).mean() * 100:.0f}% |")
    bs = bootstrap(r)
    o += ["", "## 블록 부트스트랩 10년 경로 2,000개 (과거 일봉을 1년 단위 블록으로 섞은 가상 경로)", "", "| 전략 | 중앙값 연환산 | 하위5% 연환산 | 10년 손실확률 | 중앙값 최대낙폭 | 낙폭 -60% 초과 확률 | 낙폭 -80% 초과 확률 |", "|---|---|---|---|---|---|---|"]
    for k, s in bs.items():
        o.append(f"| {names[k]} | {s['medianCagr'] * 100:.1f}% | {s['p5Cagr'] * 100:.1f}% | {s['probLoss'] * 100:.1f}% | {s['medianMdd'] * 100:.0f}% | {s['probMdd60'] * 100:.0f}% | {s['probMdd80'] * 100:.0f}% |")
    path = D.os.path.join(D.os.path.dirname(__file__), "LEVERAGE.md")
    open(path, "w", encoding="utf-8").write("\n".join(o) + "\n")
    print("\n".join(o))


if __name__ == "__main__":
    main()
