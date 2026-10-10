"""'먹기 좋았던 자리'의 공통점 탐색. (python -m leader_backtest.winners) 탐색적 분석이며 매수 신호가 아니다.
정의(결과를 보기 전에 고정): 신호일 t의 다음 거래일 종가에 진입해 20거래일 뒤 종가 수익률(비용 차감) >= +15% 이고, 그 사이 종가가 진입가 대비 -8% 아래로 내려간 적이 없으면 GOOD.
방법: 기간을 반으로 나눠 **전반기에서만** 특징을 찾고(조건 임계값도 전반기 분포로 정함) **후반기에서 그대로 검증**한다. 후반기에서도 같은 방향이 아니면 우연으로 본다.
한계: 현재 유니버스(생존편향), 약 2년 상승장, 겹치는 날짜(같은 종목 연속일)는 독립이 아니다. 찾은 조건은 가설이며 앞으로의 기록으로 다시 검증해야 한다."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .main import COST, START_EVAL, fetch_ohlcv
from .rules import CFG, features, first_of_cluster

log = logging.getLogger("leader_backtest.winners")
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "research" / "leader_backtest"
H, GOOD_RET, GOOD_MDD = 20, 0.15, -0.08
MIN_SUPPORT = 200          # 전반기에서 조건을 통과한 행이 이보다 적으면 채택하지 않는다
FEATURES = ["rs", "ret20", "ret60", "ret120", "distSma50", "distEma20", "distHigh", "atrPct", "range20", "volRatio", "vol5v50",
            "udVol", "slope50", "rsi", "pivDist", "up10", "gap5", "risk"]
LABEL = {"rs": "RS 순위(12개월 수익률 백분위)", "ret20": "최근 20일 수익률", "ret60": "최근 60일 수익률", "ret120": "최근 120일 수익률",
         "distSma50": "50일선 대비 거리", "distEma20": "20일 지수이평 대비 거리", "distHigh": "52주 고가 대비 위치", "atrPct": "ATR14/종가(변동성)",
         "range20": "최근 20일 고저폭/종가", "volRatio": "거래량/50일 평균", "vol5v50": "5일 평균 거래량/50일 평균", "udVol": "상승일/하락일 거래량(50일)",
         "slope50": "50일선 20일 기울기", "rsi": "RSI14", "pivDist": "60일 피벗 대비 거리(%)", "up10": "최근 10일 중 상승일 수", "gap5": "최근 5일 최대 갭상승(%)",
         "risk": "v1 손절폭(%)"}


def extra_features(df: pd.DataFrame) -> pd.DataFrame:
    f = features(df)
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    sma50 = c.rolling(50).mean()
    ema20 = c.ewm(span=20, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f["ret20"], f["ret60"], f["ret120"] = c / c.shift(20) - 1, c / c.shift(60) - 1, c / c.shift(120) - 1
    f["distSma50"], f["distEma20"] = c / sma50 - 1, c / ema20 - 1
    f["distHigh"] = c / h.rolling(252, min_periods=200).max() - 1
    f["atrPct"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / c
    f["range20"] = (h.rolling(20).max() - l.rolling(20).min()) / c
    f["vol5v50"] = v.rolling(5).mean() / v.rolling(50).mean().shift(1)
    up = v.where(c > c.shift(), 0.0).rolling(50).sum()
    dn = v.where(c < c.shift(), 0.0).rolling(50).sum()
    f["udVol"] = up / dn.replace(0, np.nan)
    f["slope50"] = sma50 / sma50.shift(20) - 1
    f["up10"] = (c > c.shift()).astype(float).rolling(10).sum()
    f["gap5"] = ((df["open"] / c.shift() - 1) * 100).rolling(5).max()
    return f


def labels(close: pd.Series, cost: float, h: int = H) -> pd.DataFrame:
    """신호일 t → 진입 t+1 종가. ret = h일 뒤 종가 수익률(비용 차감), mdd = 그 사이 종가의 최저 수익률."""
    c = close.to_numpy(float)
    ret, mdd = np.full(len(c), np.nan), np.full(len(c), np.nan)
    if len(c) >= h + 2:
        w = np.lib.stride_tricks.sliding_window_view(c[1:], h + 1)
        ret[:len(w)] = w[:, -1] / w[:, 0] - 1 - cost
        mdd[:len(w)] = w[:, 1:].min(axis=1) / w[:, 0] - 1
    return pd.DataFrame({"ret": ret, "mdd": mdd}, index=close.index)


def build_panel(data: dict[str, pd.DataFrame], market: str) -> pd.DataFrame:
    rows = []
    for code, df in data.items():
        f = extra_features(df)
        f = f.join(labels(df["close"], COST[market]))
        f["code"], f["close"] = code, df["close"]
        rows.append(f)
    p = pd.concat(rows)
    p.index.name = "date"
    p = p.reset_index()
    p["rs"] = p.groupby("date")["ret252"].rank(pct=True) * 100
    p = p[(p["date"] >= START_EVAL) & p["ok"] & p["ret"].notna()].reset_index(drop=True)
    p["good"] = (p["ret"] >= GOOD_RET) & (p["mdd"] >= GOOD_MDD)
    return p


def split_halves(p: pd.DataFrame) -> tuple[pd.Series, pd.Series, str]:
    dates = sorted(p["date"].unique())
    mid = dates[len(dates) // 2]
    return p["date"] < mid, p["date"] >= mid, str(pd.Timestamp(mid).date())


def univariate(p: pd.DataFrame, first: pd.Series, second: pd.Series, bins: int = 5) -> list[dict]:
    """전반기 분위수 경계로 5구간을 나눠 구간별 GOOD 비율을 전·후반 모두 계산. 같은 경계를 후반에도 쓴다."""
    out = []
    base1, base2 = p.loc[first, "good"].mean(), p.loc[second, "good"].mean()
    for f in FEATURES:
        edges = np.unique(p.loc[first, f].quantile(np.linspace(0, 1, bins + 1)).to_numpy())
        if len(edges) < 3:
            continue
        cut = lambda s: pd.cut(s, bins=np.r_[-np.inf, edges[1:-1], np.inf], labels=False)   # noqa: E731
        b = cut(p[f])
        rows = []
        for k in range(len(edges) - 1):
            m1, m2 = first & (b == k), second & (b == k)
            rows.append({"bin": k, "lo": float(edges[k]), "hi": float(edges[k + 1]), "n1": int(m1.sum()), "n2": int(m2.sum()),
                         "good1": round(float(p.loc[m1, "good"].mean() / base1), 2) if m1.sum() else None,
                         "good2": round(float(p.loc[m2, "good"].mean() / base2), 2) if m2.sum() else None})
        top = max(rows, key=lambda r: r["good1"] or 0)
        stable = top["good1"] and top["good2"] and top["good1"] >= 1.3 and top["good2"] >= 1.3
        out.append({"feature": f, "label": LABEL[f], "bins": rows, "bestBin": top["bin"], "stable": bool(stable)})
    return out


def search_rule(p: pd.DataFrame, first: pd.Series, depth: int = 3, grid=(20, 40, 60, 80)) -> list[dict]:
    """전반기에서 탐욕적으로 조건을 하나씩 더해 GOOD 비율(리프트)을 최대화한다. 임계값은 전반기 분위수만 쓴다."""
    d = p[first]
    base = d["good"].mean()
    mask = np.ones(len(d), bool)
    chosen: list[dict] = []
    used: set[str] = set()
    good = d["good"].to_numpy()
    for _ in range(depth):
        best = None
        for f in FEATURES:
            if f in used:
                continue
            x = d[f].to_numpy()
            for q in grid:
                thr = float(np.nanpercentile(x, q))
                for op in (">=", "<="):
                    m = mask & ((x >= thr) if op == ">=" else (x <= thr)) & ~np.isnan(x)
                    n = int(m.sum())
                    if n < MIN_SUPPORT:
                        continue
                    prec = float(good[m].mean())
                    if best is None or prec > best[0]:
                        best = (prec, f, op, thr, m)
        if best is None or (chosen and best[0] <= (good[mask].mean() * 1.05)):
            break
        prec, f, op, thr, mask = best
        used.add(f)
        chosen.append({"feature": f, "op": op, "threshold": round(thr, 4)})
    return chosen


def apply_rule(p: pd.DataFrame, rule: list[dict]) -> pd.Series:
    m = pd.Series(True, index=p.index)
    for c in rule:
        m &= (p[c["feature"]] >= c["threshold"]) if c["op"] == ">=" else (p[c["feature"]] <= c["threshold"])
    return m


def describe(p: pd.DataFrame, mask: pd.Series, half: pd.Series) -> dict:
    m = mask & half
    base = p.loc[half, "good"].mean()
    sel = p[m]
    # 독립 신호: 같은 종목 10거래일 쿨다운
    ind = first_of_cluster(p.loc[half], m.loc[half], 10).sum()
    return {"rows": int(m.sum()), "independent": int(ind), "stocks": int(sel["code"].nunique()), "dates": int(sel["date"].nunique()),
            "goodRate": round(float(sel["good"].mean() * 100), 1) if len(sel) else None, "baseRate": round(float(base * 100), 1),
            "lift": round(float(sel["good"].mean() / base), 2) if len(sel) and base else None,
            "meanRetPct": round(float(sel["ret"].mean() * 100), 2) if len(sel) else None,
            "medianRetPct": round(float(sel["ret"].median() * 100), 2) if len(sel) else None,
            "worstMddPct": round(float(sel["mdd"].min() * 100), 1) if len(sel) else None}


def recall_table(p: pd.DataFrame, first: pd.Series, second: pd.Series) -> dict:
    """GOOD였던 자리 중 각 진입 규칙이 신호를 낸 비율(쿨다운 전 원 조건). V1이 얼마나 놓치는지 본다."""
    raw = raw_masks(p)                      # 쿨다운 적용 전 원 조건
    out = {}
    for g, m in raw.items():
        r = {}
        for name, half in (("first", first), ("second", second)):
            gd = p["good"] & half
            r[name] = {"recallPct": round(float((m & gd).sum() / max(gd.sum(), 1) * 100), 1),
                       "precisionPct": round(float(p.loc[m & half, "good"].mean() * 100), 1) if (m & half).sum() else None,
                       "rows": int((m & half).sum())}
        out[g] = r
    return out


def raw_masks(p: pd.DataFrame, cfg: dict = CFG) -> dict[str, pd.Series]:
    trend = p["trend"] & (p["rs"] >= cfg["rs_trend"])
    chase = (p["rsi"] >= cfg["rsi_chase"]) | (p["pivDist"] >= cfg["pivot_chase"])
    return {"ALL": pd.Series(True, index=p.index), "TREND": trend,
            "V1": trend & (p["risk"] > 0) & (p["risk"] <= cfg["risk_max"]) & ~chase,
            "LEADER": trend & (p["rs"] >= cfg["rs_leader"]),
            "PULLBACK": trend & (p["rs"] >= cfg["rs_pullback"]) & p["pullback"],
            "BREAKOUT": trend & (p["pivDist"] > 0) & (p["pivDist"] <= cfg["pivot_chase"]) & (p["volRatio"] >= cfg["vol_break"])}


def analyze(p: pd.DataFrame, market: str) -> dict:
    first, second, mid = split_halves(p)
    res = {"market": market, "rows": int(len(p)), "splitDate": mid, "baseRate": {"first": round(float(p.loc[first, "good"].mean() * 100), 2), "second": round(float(p.loc[second, "good"].mean() * 100), 2)},
           "recall": recall_table(p, first, second), "univariate": univariate(p, first, second), "rules": {}}
    for name, sub in (("ALL", pd.Series(True, index=p.index)), ("TREND", raw_masks(p)["TREND"])):
        q = p[sub].reset_index(drop=True)
        if len(q) < 4 * MIN_SUPPORT:
            continue
        f1, f2, _ = split_halves(q)
        rule = search_rule(q, f1)
        if not rule:
            continue
        m = apply_rule(q, rule)
        res["rules"][name] = {"rule": rule, "discovery": describe(q, m, f1), "validation": describe(q, m, f2)}
    return res


def to_markdown(result: dict) -> str:
    o = ["# 먹기 좋았던 자리의 공통점 (탐색적 분석)", "",
         f"> 실행 {result['ranAt']} · GOOD = 다음 날 종가 진입 후 20거래일 수익률 ≥ +{GOOD_RET:.0%} 이고 그 사이 종가가 -{-GOOD_MDD:.0%} 아래로 간 적 없음. 전반기에서만 찾고 후반기에서 그대로 검증. 가설이며 매수 신호가 아님. 생존편향·상승장 구간 한계.", ""]
    for m in result["markets"].values():
        o += [f"## {m['market'].upper()} — 분석 행 {m['rows']:,}, 분할일 {m['splitDate']}", f"GOOD 기본 비율: 전반 {m['baseRate']['first']}% · 후반 {m['baseRate']['second']}%", "",
              "### 현재 규칙이 GOOD 자리를 얼마나 잡았나 (재현율 = GOOD 중 신호가 난 비율)", "| 규칙 | 전반 재현율% | 전반 정밀도% | 후반 재현율% | 후반 정밀도% | 후반 신호 행 |", "|---|---|---|---|---|---|"]
        for g, r in m["recall"].items():
            o.append(f"| {g} | {r['first']['recallPct']} | {r['first']['precisionPct']} | {r['second']['recallPct']} | {r['second']['precisionPct']} | {r['second']['rows']} |")
        o += ["", "### 특징별 GOOD 비율(기본 대비 배수) — 5분위, 전반 경계를 후반에 그대로 적용. ★ = 전·후반 모두 1.3배 이상인 최고 구간", "| 특징 | ★ | 최고 구간(전반 경계) | 전반 배수 | 후반 배수 | 구간 5개의 전반 배수 |", "|---|---|---|---|---|---|"]
        for u in m["univariate"]:
            b = u["bins"][u["bestBin"]]
            o.append(f"| {u['label']} | {'★' if u['stable'] else ''} | {b['lo']:.3g} ~ {b['hi']:.3g} | {b['good1']} | {b['good2']} | {[x['good1'] for x in u['bins']]} |")
        o.append("")
        for name, r in m["rules"].items():
            conds = " AND ".join(f"{LABEL[c['feature']]} {c['op']} {c['threshold']}" for c in r["rule"])
            o += [f"### 전반기에서 찾은 조건 ({name} 안에서): {conds}", "| 구간 | 행 | 독립 신호 | 종목 | GOOD% | 기본% | 배수 | 평균 20일% | 중앙값% | 최악 낙폭% |", "|---|---|---|---|---|---|---|---|---|---|"]
            for k, lab in (("discovery", "전반(탐색)"), ("validation", "후반(검증)")):
                d = r[k]
                o.append(f"| {lab} | {d['rows']} | {d['independent']} | {d['stocks']} | {d['goodRate']} | {d['baseRate']} | {d['lift']} | {d['meanRetPct']} | {d['medianRetPct']} | {d['worstMddPct']} |")
            o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kr = json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8"))
    us = json.loads((ROOT / "docs" / "data" / "latest_us.json").read_text(encoding="utf-8"))
    result = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "definition": {"horizon": H, "goodRet": GOOD_RET, "goodMdd": GOOD_MDD}, "markets": {}}
    for market, rows in (("us", us), ("kr", kr)):
        uni = [r for r in rows if r.get("status") == "OK" and r.get("code")]
        syms = {}
        for u in uni:
            code = str(u["code"])
            syms[code.replace(".", "-") if market == "us" else code.zfill(6) + (".KQ" if u.get("market") == "KOSDAQ" else ".KS")] = code
        data = fetch_ohlcv(list(syms))
        result["markets"][market] = analyze(build_panel(data, market), market)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "winners.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "winners.md").write_text(to_markdown(result), encoding="utf-8")
    print(to_markdown(result))


if __name__ == "__main__":
    main()
