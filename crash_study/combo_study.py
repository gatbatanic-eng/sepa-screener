"""폭락 후 매수 확장 연구: ① 손절선 ② 변동성조절 규칙에 얹기 ③ QQQ(1배) 비교.

공통 가정은 tqqq_study 와 같다 (합성 TQQQ, 신호 종가 확정 → LAG 거래일 뒤 종가 체결, 수수료 0.07%, 현금은 단기금리).
- 손절: 진입가(체결가) 대비 종가가 -X% 이하이면 그날 종가에 청산 결정(체결은 LAG 뒤라 실제 손실은 더 클 수 있다).
  손절 후에는 같은 하락 구간(고점 갱신 전)에서 다시 사지 않는다.
- 변동성조절 기본 규칙은 현재 운용 규칙: 목표 변동성 40% / 20일, ±10%p 밴드, 나스닥 50일선<200일선이면 x0.5.
실행: python -m crash_study.combo_study [IXIC|NDX]
"""
import math
import os
import sys

import numpy as np
import pandas as pd

from laoer_backtest import data as lb
from crash_study.study import dd_events
from crash_study.tqqq_study import LAG, FEE, HOLD_CAP, load_base, rate_series, perf, pct

HERE = os.path.dirname(__file__)
TOL = 1e-9


def sim(asset_ret, cash_ret, T, lag=LAG, band=0.0, fee=FEE):
    """T[i]: i일 종가에 정한 목표 비중. lag일 뒤 종가에 체결(밴드 밖이거나 청산일 때만).
    반환: 일별 자산곡선, 일별 비중(그날 수익률에 적용된 비중)"""
    n = len(T)
    eq = np.empty(n)
    wt = np.empty(n)
    e, w = 1.0, 0.0
    for t in range(n):
        wt[t] = w
        r = w * asset_ret[t] + (1 - w) * cash_ret[t]
        e *= 1 + r
        w = w * (1 + asset_ret[t]) / (1 + r) if (1 + r) > 0 else 0.0
        if t - lag >= 0:
            tgt = T[t - lag]
            d = abs(tgt - w)
            if d > TOL and (d > band or (tgt == 0 and w > 0)):
                e *= 1 - d * fee
                w = tgt
        eq[t] = e
    return eq, wt


def overlay(close, px, ev, H, stop, level=1.0, lag=LAG, peak=None):
    """폭락 신호 후 보유 구간의 목표 비중 배열과 거래 목록.
    H=None 이면 신호 당시 고점을 회복할 때까지(상한 HOLD_CAP). stop=None 이면 손절 없음."""
    n = len(close)
    v, p = close.values, px.values
    peak = np.maximum.accumulate(v) if peak is None else peak
    T = np.zeros(n)
    trades = []
    for i in ev:
        entry = i + lag
        if entry >= n:
            continue
        end = i + H if H else min(n, i + HOLD_CAP)
        stopped, j = False, i
        while j < min(end, n):
            if not H and v[j] >= peak[i]:
                break
            if stop and j >= entry and p[j] / p[entry] - 1 <= -stop:
                stopped = True
                break
            T[j] = level
            j += 1
        ex = min(j + lag, n - 1)
        trades.append(dict(sig=i, entry=entry, exit=ex, ret=p[ex] / p[entry] - 1, stopped=stopped,
                           open=(j >= n or (not H and not stopped and v[min(j, n - 1)] < peak[i]))))
    return T, trades


def base_weight(index_close, px, tv=0.40, lbk=20, k=0.5):
    logr = np.log(px).diff()
    vol = logr.rolling(lbk).std() * math.sqrt(252)
    w = (tv / vol).clip(upper=1.0).fillna(0.0)
    ma50, ma200 = index_close.rolling(50).mean(), index_close.rolling(200).mean()
    down = (ma50 < ma200).fillna(False)
    return (w * np.where(down, k, 1.0)).values


def trade_summary(trades):
    r = np.array([t["ret"] for t in trades])
    if len(r) == 0:
        return None
    return dict(n=len(r), win=(r > 0).mean(), mean=r.mean(), med=np.median(r), worst=r.min(),
                stops=sum(t["stopped"] for t in trades))


EPISODES = [("닷컴", "2000-03-10", "2002-10-09"), ("금융위기", "2007-10-31", "2009-03-09"),
            ("2022", "2021-11-19", "2022-12-28")]


def episode_ret(eq, idx, a, b):
    ia, ib = idx.searchsorted(pd.Timestamp(a)), idx.searchsorted(pd.Timestamp(b))
    if ia >= len(idx) or ib >= len(idx) or ia == 0:
        return float("nan")
    return eq[ib] / eq[ia - 1] - 1 if ia > 0 else float("nan")


def row(label, eq, w, idx, extra="", mkt=None):
    p = perf(eq, w, idx)
    if mkt is not None:
        p["inmkt"] = mkt
    eps = " | ".join(pct(episode_ret(eq, idx, a, b)) for _, a, b in EPISODES)
    return (f"| {label} | {p['final']:.1f}배 | {pct(p['cagr'])} | {pct(p['mdd'])} | {p['calmar']:.2f} | "
            f"{p['inmkt']*100:.0f}% | {eps}{extra} |")


HEAD = ("| 전략 | 최종 배수 | 연복리 | 최대낙폭 | 칼마 | 시장 참여 | " + " | ".join(n for n, _, _ in EPISODES) + " |\n"
        "|---|---|---|---|---|---|" + "---|" * len(EPISODES))


def main(name="IXIC", window=None):
    suf = "_1y" if window else ""
    base = load_base(name)
    idx = base.index
    rate = rate_series(idx)
    spread = lb.calibrate("NDX")
    tq = lb.lever(base, rate, spread=spread, lev=3.0)["Close"]
    qq = lb.lever(base, rate, spread=0.0, lev=1.0, expense=0.002)["Close"]
    rt_t, rt_q = tq.pct_change().fillna(0).values, qq.pct_change().fillna(0).values
    rc = (rate / 252).values
    close = base["Close"]
    ev10, ev20 = dd_events(close, -0.10, window), dd_events(close, -0.20, window)
    pk = (close.rolling(window, min_periods=1).max() if window else close.cummax()).values
    L = [f"# 폭락 후 매수 확장 연구 ({name})", "",
         f"- 기간 {idx[0].date()} ~ {idx[-1].date()}. 합성 TQQQ(3x)와 합성 QQQ(1x, 보수 0.2%) 모두 {name} 기반 가상 상품. "
         f"신호 종가 확정 → 다음 거래일 종가 체결(LAG={LAG}), 수수료 0.07%, 현금은 단기금리, 세금·환율 미반영.",
         "- 1985년 이전 금리는 T-bill 연평균 근사값. 합성 상품은 실제와 다를 수 있다.",
         "- 손절은 종가 기준으로 결정하고 다음 날 종가에 체결하므로 실제 손실은 손절선보다 크다. 손절 후 같은 하락 구간에서는 재진입하지 않는다.",
         "- 폭락 신호: " + ("최근 252거래일 고점 대비 -10%/-20% 첫 도달, 낙폭이 -5% 안쪽으로 회복하면 재신호(2008년 포함)." if window else "사상 최고가 대비 -10%/-20% 첫 도달, 신고가 전까지는 같은 구간이라 1번만(2000년 고점이 2015년에 회복돼 2008년은 별도 이벤트가 아님)."),
         "- 규칙에 따른 과거 통계이고 투자 권유가 아니다.",
         "- 표의 닷컴(2000-03-10~2002-10-09)·금융위기(2007-10-31~2009-03-09)·2022(2021-11-19~2022-12-28)는 그 구간 전략 수익률이다.", ""]

    # ① 손절선 + ③ QQQ 비교
    stops = [None, 0.20, 0.30, 0.40]
    holds = [("120일 보유", 120), ("250일 보유", 250), ("고점 회복까지", None)]
    for asset, px, ret, lab in (("TQQQ(3x)", tq, rt_t, "TQQQ"), ("QQQ(1x)", qq, rt_q, "QQQ")):
        L += [f"## {'①' if lab == 'TQQQ' else '③'} 평소 현금, 폭락 신호 때만 {asset} 매수 — 손절선별", "", HEAD + " 손절 |", ]
        L[-1] = HEAD.split("\n")[0] + " 손절 횟수 |\n" + HEAD.split("\n")[1] + "---|"
        for tname, ev in (("-10% 도달", ev10), ("-20% 도달", ev20)):
            for hname, H in holds:
                for st in stops:
                    T, tr = overlay(close, px, ev, H, st, peak=pk)
                    eq, w = sim(ret, rc, T)
                    ts = trade_summary(tr)
                    L.append(row(f"{tname} → {hname}, 손절 {'없음' if st is None else f'-{int(st*100)}%'}", eq, w, idx,
                                 f" | {ts['stops']}/{ts['n']}"))
        L.append("")
    # 기준선
    L += ["### 기준선", "", HEAD.split("\n")[0] + " |\n" + HEAD.split("\n")[1] + "---|"]
    cash_eq = np.cumprod(1 + rc)
    L.append(row("현금만", cash_eq, np.zeros(len(idx)), idx, " | -"))
    for lab, ret in (("합성 TQQQ 매수보유", rt_t), ("합성 QQQ 매수보유", rt_q)):
        eq, w = sim(ret, rc, np.ones(len(idx)), lag=0, fee=0.0)
        L.append(row(lab, eq, w, idx, " | -"))
    L.append("")

    # 거래 단위: 손절이 큰 손실을 막았나
    L += ["## ① 거래 단위 — 고점 회복까지 보유 (TQQQ), 손절 유무에 따른 거래별 결과", "",
          "| 신호 | 손절 없음 | 손절 -30% | 손절 -40% | 손절 -20% |", "|---|---|---|---|---|"]
    res = {}
    for st in stops:
        _, tr = overlay(close, tq, ev10, None, st, peak=pk)
        res[st] = tr
    for k, t0 in enumerate(res[None]):
        cells = []
        for st in (None, 0.30, 0.40, 0.20):
            t = res[st][k]
            cells.append(pct(t["ret"]) + ("(손절)" if t["stopped"] else "") + ("*" if t["open"] else ""))
        L.append(f"| {idx[t0['sig']].date()} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("| 손절 | 거래 수 | 수익 비율 | 평균 | 중앙값 | 최악 | 손절 횟수 |")
    L.append("|---|---|---|---|---|---|---|")
    for st in stops:
        s = trade_summary(res[st])
        L.append(f"| {'없음' if st is None else f'-{int(st*100)}%'} | {s['n']} | {s['win']*100:.0f}% | {pct(s['mean'])} | "
                 f"{pct(s['med'])} | {pct(s['worst'])} | {s['stops']} |")
    L += ["", "\\* 고점 회복 전에 상한 또는 데이터 끝에서 청산한 거래.", ""]

    # ② 변동성조절에 얹기
    bw = base_weight(close, tq)
    L += ["## ② 현재 운용 규칙(변동성조절 40% + 50/200 추세)에 폭락 후 비중 확대를 얹기 (TQQQ)", "",
          "목표 비중 = max(기본 규칙 비중, 폭락 후 비중). 폭락 후 비중이 꺼지면 기본 규칙으로 돌아간다. 밴드 ±10%p.", "",
          HEAD.replace("시장 참여", "평균 비중") ]
    eq, w = sim(rt_t, rc, bw, band=0.10)
    L.append(row("기본 규칙만", eq, w, idx, mkt=w.mean()))
    combos = []
    for tname, ev in (("-10%", ev10), ("-20%", ev20)):
        for lvl in (0.6, 1.0):
            for hname, H in (("120일", 120), ("회복까지", None)):
                for st in (None, 0.30):
                    combos.append((tname, ev, lvl, hname, H, st))
    for tname, ev, lvl, hname, H, st in combos:
        ov, _ = overlay(close, tq, ev, H, st, level=lvl, peak=pk)
        eq, w = sim(rt_t, rc, np.maximum(bw, ov), band=0.10)
        L.append(row(f"{tname} 도달 → 비중 {int(lvl*100)}% 이상, {hname}, 손절 {'없음' if st is None else '-30%'}", eq, w, idx, mkt=w.mean()))
    eq, w = sim(rt_t, rc, np.ones(len(idx)), lag=0, fee=0.0)
    L.append(row("합성 TQQQ 매수보유", eq, w, idx, mkt=1.0))
    L.append("")

    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    path = os.path.join(HERE, "results", f"COMBO_{name}{suf}.md")
    open(path, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    print("저장:", path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "IXIC", 252 if "1y" in sys.argv[2:] else None)
