"""나스닥 폭락 이후 거래일 흐름 백테스트.

폭락 정의 (둘 다 본다)
  A. 일간 낙폭: 종가 기준 하루 -3% 이하 / -5% 이하
  B. 누적 낙폭: 직전 최고가(종가) 대비 -10% / -20% 를 처음 넘는 날
이벤트 중복 처리
  A. 첫 신호 후 COOLDOWN 거래일 안의 신호는 버린다
  B. 고점 갱신 전까지는 같은 하락 구간이므로 구간당 1번만 센다
관찰 기간: 1,5,10,20,60,120 거래일. 기준은 신호일 종가.
비교 기준: 모든 날(무조건부) + 같은 개수 무작위 날짜 부트스트랩(평균수익률 p값)

실행: python -m crash_study.study [IXIC|NDX]
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
HORIZONS = [1, 5, 10, 20, 60, 120]
COOLDOWN = 20
NBOOT = 5000
SEED = 7


def load(name):
    for p in (os.path.join(HERE, "data", f"{name}.csv"),
              os.path.join(HERE, "..", "laoer_backtest", "data", f"{name}.csv")):
        if os.path.exists(p):
            d = pd.read_csv(p, index_col=0, parse_dates=True).sort_index()
            return d["Close"].astype(float).dropna(), os.path.relpath(p, os.path.join(HERE, ".."))
    sys.exit(f"{name}.csv 없음")


def daily_events(close, thr):
    r = close.pct_change()
    idx, last = [], -10**9
    for i, v in enumerate(r.values):
        if v <= thr and i - last > COOLDOWN:
            idx.append(i)
            last = i
        elif v <= thr:
            pass
    return idx


def dd_events(close, thr, window=None, rearm=-0.05):
    """고점 대비 thr 를 처음 넘는 날.
    window=None : 사상 최고가 기준, 신고가를 낼 때까지 같은 구간(구간당 1번)
    window=N    : 최근 N거래일 최고가 기준, 낙폭이 rearm(-5%) 안쪽으로 회복하면 새 신호 허용.
                  (2000년 고점이 2015년에야 회복돼, 사상 최고가 기준에서는 2008년이 별도 이벤트로 잡히지 않는 문제를 피한다)"""
    peak = close.rolling(window, min_periods=1).max() if window else close.cummax()
    dd = close / peak - 1
    idx, armed = [], True
    for i, (v, p, pv) in enumerate(zip(dd.values, peak.values, close.values)):
        if (window and v > rearm) or (not window and pv >= p):
            armed = True
        if armed and v <= thr:
            idx.append(i)
            armed = False
    return idx


def fwd(close, idx, h):
    v = close.values
    return np.array([v[i + h] / v[i] - 1 for i in idx if i + h < len(v)])


def stats(rets, base_all, rng):
    if len(rets) == 0:
        return None
    k = len(rets)
    boots = np.array([rng.choice(base_all, k, replace=False).mean() for _ in range(NBOOT)]) if len(base_all) > k else None
    p = float((boots >= rets.mean()).mean()) if boots is not None else float("nan")
    return dict(n=k, mean=rets.mean(), med=np.median(rets), win=(rets > 0).mean(),
                worst=rets.min(), base_mean=base_all.mean(), base_med=np.median(base_all),
                base_win=(base_all > 0).mean(), p=p)


def pct(x):
    return f"{x*100:+.1f}%"


def table(close, idx, rng):
    rows = []
    for h in HORIZONS:
        base = (close.shift(-h) / close - 1).dropna().values
        s = stats(fwd(close, idx, h), base, rng)
        if s:
            rows.append((h, s))
    out = ["| 이후 | 표본 | 평균 | 중앙값 | 승률 | 최악 | 전체일 평균 | 전체일 승률 | 평균 초과 | p값* |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for h, s in rows:
        out.append(f"| {h}일 | {s['n']} | {pct(s['mean'])} | {pct(s['med'])} | {s['win']*100:.0f}% | "
                   f"{pct(s['worst'])} | {pct(s['base_mean'])} | {s['base_win']*100:.0f}% | "
                   f"{pct(s['mean']-s['base_mean'])} | {s['p']:.3f} |")
    return "\n".join(out)


def post_low(close, idx, h=120):
    """신호 이후 h일 안에 더 빠진 정도(신호일 종가 대비 최저)와 그 날까지 걸린 일수"""
    v = close.values
    res = []
    for i in idx:
        w = v[i + 1:i + 1 + h]
        if len(w) < 5:
            continue
        j = int(np.argmin(w))
        res.append((w[j] / v[i] - 1, j + 1))
    a = np.array(res)
    return a[:, 0], a[:, 1]


def main(name="IXIC", window=None):
    suf = "_1y" if window else ""
    close, src = load(name)
    rng = np.random.default_rng(SEED)
    L = [f"# 나스닥 폭락 이후 흐름 ({name})", "",
         f"- 데이터: `{src}`, {close.index[0].date()} ~ {close.index[-1].date()} ({len(close):,}거래일)",
         f"- 신호일 종가 기준 이후 N거래일 수익률(가격지수, 배당 제외). 일간 신호 쿨다운 {COOLDOWN}거래일, 누적 낙폭 신호는 " + ("최근 252거래일 고점 기준, -5% 안쪽으로 회복하면 재신호." if window else "하락 구간당 1번(신고가 전까지)."),
         f"- \\*p값: 같은 개수의 무작위 날짜를 {NBOOT}번 뽑았을 때 평균 수익률이 폭락 후 평균 이상일 확률 (작을수록 폭락 후가 특별히 좋았다는 뜻).",
         "- 규칙에 따른 과거 통계이고 투자 권유가 아니다.", ""]
    defs = [("일간 -3% 이하", daily_events(close, -0.03)), ("일간 -5% 이하", daily_events(close, -0.05)),
            ("고점 대비 -10% 첫 도달", dd_events(close, -0.10, window)), ("고점 대비 -20% 첫 도달", dd_events(close, -0.20, window))]
    ev_rows = []
    for title, idx in defs:
        L += [f"## {title} — 이벤트 {len(idx)}회", "", table(close, idx, rng), ""]
        if len(idx) >= 3:
            lo, days = post_low(close, idx)
            L += [f"신호 이후 120일 안 추가 하락(신호일 종가 대비 최저): 중앙값 {pct(np.median(lo))}, "
                  f"평균 {pct(lo.mean())}, 최악 {pct(lo.min())}. 최저점까지 중앙값 {int(np.median(days))}거래일.", ""]
        for i in idx:
            ev_rows.append(dict(rule=title, date=close.index[i].date(), close=close.values[i],
                                **{f"r{h}": (close.values[i + h] / close.values[i] - 1) if i + h < len(close) else np.nan
                                   for h in HORIZONS}))
    ev = pd.DataFrame(ev_rows)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    ev.round(4).to_csv(os.path.join(HERE, "results", f"events_{name}{suf}.csv"), index=False)

    # 구간 나눠 보기: 결과가 특정 시기에 쏠리는지
    L += ["## 시기별로 나눠 보기 (고점 대비 -10% 첫 도달, 20일/60일 후)", "",
          "| 시기 | 표본 | 20일 평균 | 60일 평균 | 60일 승률 |", "|---|---|---|---|---|"]
    e10 = ev[ev.rule == "고점 대비 -10% 첫 도달"].copy()
    e10["date"] = pd.to_datetime(e10["date"])
    for a, b in [(1971, 1989), (1990, 1999), (2000, 2009), (2010, 2026)]:
        s = e10[(e10.date.dt.year >= a) & (e10.date.dt.year <= b)]
        if len(s):
            L.append(f"| {a}~{b} | {len(s)} | {pct(s.r20.mean())} | {pct(s.r60.mean())} | {(s.r60.dropna() > 0).mean()*100:.0f}% |")
    L.append("")
    path = os.path.join(HERE, "results", f"RESULTS_{name}{suf}.md")
    open(path, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    print("저장:", path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "IXIC", 252 if "1y" in sys.argv[2:] else None)
