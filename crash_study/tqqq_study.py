"""폭락 후 합성 TQQQ 매수 전략 백테스트.

- 기초지수: IXIC(1971~, 없으면 NDX). 합성 TQQQ = laoer_backtest.data.lever (일간 x3 - 보수 - 2x금리 - 스프레드)
- 금리: ^IRX(1985~). 그 이전(1971~84)은 3개월 T-bill 연평균 근사값(아래 TBILL_PRE1985)으로 채운다.
- 신호는 종가에 확정, 체결은 LAG 거래일 뒤 종가 (기본 1 = 신호 다음 날 종가). 매매 수수료 0.07%.
- 포지션이 아닌 돈은 단기금리로 불어난다.
실행: python -m crash_study.tqqq_study [IXIC|NDX]
"""
import os
import sys

import numpy as np
import pandas as pd

from laoer_backtest import data as lb
from crash_study.study import dd_events, daily_events

HERE = os.path.dirname(__file__)
LAG = 1
FEE = 0.0007
HOLD_CAP = 750  # 회복 대기 상한(거래일)
# 3개월 T-bill 연평균(%) — 근사값. 1985년 이전 금리 데이터가 저장소에 없어 사용.
TBILL_PRE1985 = {1971: 4.3, 1972: 4.1, 1973: 7.0, 1974: 7.9, 1975: 5.8, 1976: 5.0, 1977: 5.3,
                 1978: 7.2, 1979: 10.0, 1980: 11.5, 1981: 14.0, 1982: 10.7, 1983: 8.6, 1984: 9.6}


def load_base(name):
    p = os.path.join(HERE, "data", f"{name}.csv")
    if not os.path.exists(p):
        p = os.path.join(HERE, "..", "laoer_backtest", "data", f"{name}.csv")
    d = pd.read_csv(p, index_col=0, parse_dates=True).sort_index()
    d = d[~d.index.duplicated()]
    return d[["Open", "High", "Low", "Close"]].astype(float)


def rate_series(index):
    r = lb.short_rate(index)  # 1985~ 실데이터, 이전은 0
    pre = pd.Series([TBILL_PRE1985.get(y, np.nan) / 100 for y in index.year], index=index)
    return r.where(index >= "1985-01-02", pre).fillna(0.0)


def states_fixed(close, ev, H):
    n = len(close)
    s = np.zeros(n)
    for i in ev:
        s[i:min(n, i + H)] = 1.0
    return s


def states_recover(close, ev, cap=HOLD_CAP):
    v = close.values
    peak = np.maximum.accumulate(v)
    n = len(v)
    s = np.zeros(n)
    for i in ev:
        tgt = peak[i]
        j = i
        while j < n and j - i < cap and v[j] < tgt:
            s[j] = 1.0
            j += 1
    return s


def states_scale(close, levels=(-0.10, -0.20, -0.30), cap=HOLD_CAP):
    """고점 대비 각 단계에 처음 닿을 때마다 1/len(levels)씩 추가, 신고가에서 전량 청산"""
    v = close.values
    n = len(v)
    s = np.zeros(n)
    peak, k, since = v[0], 0, 0
    for i in range(1, n):
        if v[i] >= peak:
            peak, k, since = v[i], 0, 0
        else:
            dd = v[i] / peak - 1
            while k < len(levels) and dd <= levels[k]:
                k += 1
            since = since + 1 if k else 0
            if since > cap:
                k = 0
        s[i] = k / len(levels)
    return s


def equity(tq_ret, cash_ret, s, lag=LAG, fee=FEE):
    """s[i]: i일 종가에 정한 목표 비중. 체결은 lag일 뒤 종가, 그 다음 날 수익률부터 반영."""
    n = len(s)
    w = np.zeros(n)
    sh = lag + 1
    w[sh:] = s[:n - sh]
    turn = np.abs(np.diff(w, prepend=0.0))
    r = w * tq_ret + (1 - w) * cash_ret - turn * fee
    return (1 + pd.Series(r)).cumprod().values, w


def perf(eq, w, index):
    yrs = (index[-1] - index[0]).days / 365.25
    cagr = eq[-1] ** (1 / yrs) - 1
    mdd = (eq / np.maximum.accumulate(eq) - 1).min()
    d = np.diff(eq) / eq[:-1]
    return dict(final=eq[-1], cagr=cagr, mdd=mdd, calmar=cagr / abs(mdd) if mdd else np.nan,
                inmkt=(w > 0).mean(), full=(w >= 0.99).mean())


def trade_stats(tq, ev, H, rng, nboot=5000):
    """이벤트마다 (신호 다음 날 종가 매수 → H거래일 보유) TQQQ 수익률 vs 아무 날이나 샀을 때"""
    v = tq.values
    ent = [i + LAG for i in ev if i + LAG + H < len(v)]
    r = np.array([v[i + H] / v[i] - 1 for i in ent])
    allr = (tq.shift(-H) / tq - 1).dropna().values
    if len(r) == 0:
        return None
    k = len(r)
    boots = np.array([rng.choice(allr, k, replace=False).mean() for _ in range(nboot)])
    return dict(n=k, mean=r.mean(), med=np.median(r), win=(r > 0).mean(), worst=r.min(), best=r.max(),
                base_mean=allr.mean(), base_win=(allr > 0).mean(), p=float((boots >= r.mean()).mean()))


def pct(x):
    return f"{x*100:+.1f}%"


def main(name="IXIC"):
    base = load_base(name)
    rate = rate_series(base.index)
    spread = lb.calibrate("NDX")
    tq_df = lb.lever(base, rate, spread=spread, lev=3.0)
    tq = tq_df["Close"]
    tq_ret = tq.pct_change().fillna(0).values
    cash_ret = (rate / 252).values
    close = base["Close"]
    idx = base.index
    rng = np.random.default_rng(7)

    ev10, ev20 = dd_events(close, -0.10), dd_events(close, -0.20)
    ev5d = daily_events(close, -0.05)
    strategies = {}
    for H in (60, 120, 250):
        strategies[f"-10% 도달 → {H}일 보유"] = states_fixed(close, ev10, H)
    for H in (60, 120, 250):
        strategies[f"-20% 도달 → {H}일 보유"] = states_fixed(close, ev20, H)
    strategies["-10% 도달 → 고점 회복까지"] = states_recover(close, ev10)
    strategies["-20% 도달 → 고점 회복까지"] = states_recover(close, ev20)
    strategies["분할 -10/-20/-30% 각 1/3 → 고점 회복까지"] = states_scale(close)
    strategies["일간 -5% → 60일 보유"] = states_fixed(close, ev5d, 60)

    L = [f"# 폭락 후 합성 TQQQ 매수 전략 ({name})", "",
         f"- 기간: {idx[0].date()} ~ {idx[-1].date()} ({len(idx):,}거래일). 기초지수 {name}, 합성 TQQQ = 일간수익률 x3 − 보수 0.95% − 2x단기금리 − 스프레드({spread*100:+.2f}%/년, 실제 TQQQ에 맞춰 보정).",
         "- 금리: 1985년 이후는 `^IRX` 실데이터, **1971~84년은 3개월 T-bill 연평균 근사값**(저장소에 데이터 없음). 이 구간 레버리지 비용은 근사다.",
         f"- 신호는 종가에 확정하고 **다음 거래일 종가**에 체결(LAG={LAG}). 수수료 0.07%/회. 투자하지 않는 돈은 단기금리로 불어난다. 세금·환율·슬리피지 미반영.",
         "- 합성 TQQQ는 가상이다. 실제 TQQQ는 2010년에 나왔고, 1987·2000·2008년 같은 급락장에서 스왑·유동성 제약으로 다르게 움직일 수 있다.",
         "- 규칙에 따른 과거 통계이고 투자 권유가 아니다.", ""]

    # 1. 이벤트 단위: 신호 후 사면 평소보다 나았나
    L += ["## 1. 이벤트 단위 — 신호 다음 날 종가에 TQQQ 매수, H거래일 후 수익률", "",
          "| 신호 | 보유 | 표본 | 평균 | 중앙값 | 승률 | 최악 | 최고 | 아무 날이나 산 평균 | 승률 | p값* |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for label, ev in (("-10% 도달", ev10), ("-20% 도달", ev20), ("일간 -5%", ev5d)):
        for H in (20, 60, 120, 250):
            s = trade_stats(tq, ev, H, rng)
            if s:
                L.append(f"| {label} | {H}일 | {s['n']} | {pct(s['mean'])} | {pct(s['med'])} | {s['win']*100:.0f}% | "
                         f"{pct(s['worst'])} | {pct(s['best'])} | {pct(s['base_mean'])} | {s['base_win']*100:.0f}% | {s['p']:.3f} |")
    L += ["", "\\*p값: 같은 개수의 무작위 진입일 평균 수익률이 폭락 후 평균 이상일 확률.", ""]

    # 2. 포트폴리오
    rows = []
    for k, s in strategies.items():
        eq, w = equity(tq_ret, cash_ret, s)
        rows.append((k, perf(eq, w, idx)))
    eq_cash = np.cumprod(1 + cash_ret)
    rows.append(("현금(단기금리)만", perf(eq_cash, np.zeros(len(idx)), idx)))
    eq_i = (close / close.iloc[0]).values
    rows.append((f"{name} 지수 매수보유(배당 제외)", perf(eq_i, np.ones(len(idx)), idx)))
    eq_t, _ = equity(tq_ret, cash_ret, np.ones(len(idx)), lag=0, fee=0.0)
    rows.append(("합성 TQQQ 매수보유", perf(eq_t, np.ones(len(idx)), idx)))
    L += ["## 2. 포트폴리오 — 평소엔 현금, 신호 때만 TQQQ", "",
          "| 전략 | 최종 배수 | 연복리 | 최대낙폭 | 칼마 | 시장 참여 | 풀 투자 |", "|---|---|---|---|---|---|---|"]
    for k, p in rows:
        L.append(f"| {k} | {p['final']:.1f}배 | {pct(p['cagr'])} | {pct(p['mdd'])} | {p['calmar']:.2f} | "
                 f"{p['inmkt']*100:.0f}% | {p['full']*100:.0f}% |")
    L += ["", "시장 참여는 TQQQ를 조금이라도 들고 있던 날의 비율이다. 현금 비중이 큰 전략은 연복리가 낮을 수밖에 없으니, "
          "참여율 대비 효율은 칼마와 아래 3번 표로 본다.", ""]

    # 3. 시장 참여 중인 날만: TQQQ 노출 대비 효율
    L += ["## 3. 개별 구간 성과 — 구간별 TQQQ 손익 (고점 회복까지 보유, -10% 도달 기준)", "",
          "| 신호일 | 진입 | 청산 | 거래일 | TQQQ 수익률 | 보유 중 최악 낙폭 |", "|---|---|---|---|---|---|"]
    v = close.values
    peak = np.maximum.accumulate(v)
    tv = tq.values
    trades = []
    for i in ev10:
        a = i + LAG
        if a >= len(v):
            continue
        j = a
        while j < len(v) - 1 and j - a < HOLD_CAP and v[j] < peak[i]:
            j += 1
        seg = tv[a:j + 1] / tv[a]
        done = v[j] >= peak[i]
        trades.append((idx[i].date(), idx[a].date(), idx[j].date() if done else f"{idx[j].date()}*", j - a,
                       tv[j] / tv[a] - 1, seg.min() - 1))
    for t in trades:
        L.append(f"| {t[0]} | {t[1]} | {t[2]} | {t[3]} | {pct(t[4])} | {pct(t[5])} |")
    tr = np.array([t[4] for t in trades])
    L += ["", f"\\* 미회복 상태에서 상한({HOLD_CAP}거래일) 또는 데이터 끝에 도달해 청산(미청산 포함). "
          f"{len(tr)}번 중 수익 {int((tr > 0).sum())}번, 평균 {pct(tr.mean())}, 중앙값 {pct(np.median(tr))}, 최악 {pct(tr.min())}.", ""]

    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    path = os.path.join(HERE, "results", f"TQQQ_{name}.md")
    open(path, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    print("저장:", path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "IXIC")
