"""먹을 자리 조건의 견고성 확인. (python -m leader_backtest.robust) 2026-10-10 고정 — 결과를 보고 규칙·기준을 바꾸지 않는다. 연구용이며 매수 신호가 아니다.
앞선 탐색(winners.py)이 전반기에서 찾은 조건을 **임계값 그대로** 다음 방식으로 다시 시험한다.
 1) GOOD 정의 4가지: G10(20일 +10%·낙폭 -8% 이내) G15(+15%, 원래 정의) G20(+20%) G40(40일 +25%·낙폭 -12% 이내)
 2) 기간 3등분(T1·T2·T3)과 전체. 탐색은 앞 절반에서 했으므로 T3(맨 뒤 3분의 1)가 가장 깨끗한 검증 구간이다.
 3) 대조 규칙: 단순 변동성(VOL)·추세+변동성·추세+눌림만 — 찾은 조건이 '변동성이 큰 종목이 더 움직인다'는 사실 이상을 더하는지 본다.
 4) 수익률(20일 고정 보유, -8% 손절 포함)을 ALL 대비 날짜별 평균 차이와 95% 이동 블록 부트스트랩 구간으로 본다.
견고 판정(미국 기준, 전부 충족해야 PASS): (a) G15 배수가 T1·T2·T3 모두 1.5 이상 (b) G10·G15·G20 중 2개 이상에서 전체 배수 1.5 이상 (c) 20일 고정 보유 수익률의 ALL 대비 차이 95% 구간 하한이 0 초과(전체)
 (d) T3 평균 수익률이 같은 기간 ALL보다 높음 (e) 같은 조건에서 변동성만 거른 대조(VOL)보다 G15 배수가 높음.
한계: 현재 유니버스(생존편향, 특히 과매도 반등 조건에 유리), 약 2년 상승장, 겹치는 날짜."""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

from .main import COST, START_EVAL, block_bootstrap_ci, fetch_ohlcv, ROOT, OUT_DIR
from .rules import first_of_cluster, forward_returns
from .winners import extra_features, labels

log = logging.getLogger("leader_backtest.robust")
GOODS = {"G10": ("ret20", "mdd20", 0.10, -0.08), "G15": ("ret20", "mdd20", 0.15, -0.08), "G20": ("ret20", "mdd20", 0.20, -0.08),
         "G40": ("ret40", "mdd40", 0.25, -0.12)}
# 규칙(고정). FOUND_*는 winners.py가 전반기에서 찾은 임계값 그대로다.
RULES = {
    "VOL": "ATR14/종가 >= 3%",
    "TREND_VOL": "추세 통과(RS 70+) & ATR >= 3%",
    "TREND_PB": "추세 통과 & 종가 <= 20일 지수이평 x 1.005",
    "TREND_VOL_PB": "추세 통과 & ATR >= 3% & 종가 <= 20일 이평 x 1.005",
    "FOUND_TREND": "추세 통과 & ATR >= 2.95% & 20일 이평 이하(<= +0.48%) & 손절폭 >= 21.58%",
    "OVERSOLD_VOL": "ATR >= 3% & RSI14 <= 40",
    "FOUND_ALL": "ATR >= 3.34% & 손절폭 >= 17.24% & RSI14 <= 41.6",
}
CONTROL_OF = {"TREND_VOL_PB": "TREND_VOL", "FOUND_TREND": "TREND_VOL", "TREND_PB": "TREND_VOL", "OVERSOLD_VOL": "VOL", "FOUND_ALL": "VOL", "TREND_VOL": "VOL"}


def build_panel(data: dict[str, pd.DataFrame], market: str) -> pd.DataFrame:
    rows = []
    for code, df in data.items():
        f = extra_features(df)
        for h in (20, 40):
            lab = labels(df["close"], COST[market], h)
            f[f"ret{h}"], f[f"mdd{h}"] = lab["ret"], lab["mdd"]
            f[f"stop{h}"] = forward_returns(df["close"], h, COST[market], 0.08)
        f["code"], f["close"] = code, df["close"]
        rows.append(f)
    p = pd.concat(rows)
    p.index.name = "date"
    p = p.reset_index()
    p["rs"] = p.groupby("date")["ret252"].rank(pct=True) * 100
    return p[(p["date"] >= START_EVAL) & p["ok"]].reset_index(drop=True)


def rule_masks(p: pd.DataFrame) -> dict[str, pd.Series]:
    trend = p["trend"] & (p["rs"] >= 70)
    vol = p["atrPct"] >= 0.03
    pb = p["distEma20"] <= 0.005
    m = {"ALL": pd.Series(True, index=p.index), "VOL": vol, "TREND_VOL": trend & vol, "TREND_PB": trend & pb, "TREND_VOL_PB": trend & vol & pb,
         "FOUND_TREND": trend & (p["atrPct"] >= 0.0295) & (p["distEma20"] <= 0.0048) & (p["risk"] >= 21.5825),
         "OVERSOLD_VOL": vol & (p["rsi"] <= 40),
         "FOUND_ALL": (p["atrPct"] >= 0.0334) & (p["risk"] >= 17.2411) & (p["rsi"] <= 41.6)}
    return {k: v.fillna(False) for k, v in m.items()}


def periods(p: pd.DataFrame) -> dict[str, pd.Series]:
    d = sorted(p["date"].unique())
    a, b = d[len(d) // 3], d[2 * len(d) // 3]
    return {"ALL": pd.Series(True, index=p.index), "T1": p["date"] < a, "T2": (p["date"] >= a) & (p["date"] < b), "T3": p["date"] >= b}


def good_flag(p: pd.DataFrame, g: str) -> pd.Series:
    r, d, ret_min, mdd_min = GOODS[g]
    return ((p[r] >= ret_min) & (p[d] >= mdd_min)).where(p[r].notna(), np.nan)


def _daily_diff(p: pd.DataFrame, sel: pd.Series, base: pd.Series, col: str) -> pd.Series:
    a = p.loc[sel & p[col].notna()].groupby("date")[col].mean()
    b = p.loc[base & p[col].notna()].groupby("date")[col].mean()
    return (a - b.reindex(a.index)).dropna()


def evaluate(p: pd.DataFrame) -> dict:
    masks, pers = rule_masks(p), periods(p)
    gf = {g: good_flag(p, g) for g in GOODS}
    for g, s in gf.items():
        p[f"good_{g}"] = s
    out: dict = {"rows": int(len(p)), "periods": {}}
    for pname, pm in pers.items():
        res = {}
        base_mask = masks["ALL"] & pm
        base_good = {g: float(p.loc[base_mask, f"good_{g}"].mean()) for g in GOODS}
        base_ret = float(p.loc[base_mask, "ret20"].mean())
        for rname, rm in masks.items():
            m = rm & pm
            r = {"rows": int(m.sum()), "independent": int(first_of_cluster(p, m, 10).sum()) if rname != "ALL" else None,
                 "stocks": int(p.loc[m, "code"].nunique())}
            for g in GOODS:
                s = p.loc[m, f"good_{g}"]
                r[f"rate_{g}"] = round(float(s.mean() * 100), 1) if s.notna().any() else None
                r[f"lift_{g}"] = round(float(s.mean() / base_good[g]), 2) if s.notna().any() and base_good[g] else None
            for col, key in (("ret20", "ret20"), ("stop20", "stop20")):
                v = p.loc[m, col]
                r[f"mean_{key}"] = round(float(v.mean() * 100), 2) if v.notna().any() else None
                r[f"median_{key}"] = round(float(v.median() * 100), 2) if v.notna().any() else None
            risk = p.loc[m, "risk"]
            sized = np.minimum(1.0, 8.0 / risk) * p.loc[m, "stop20"]
            r["sizedStop20"] = round(float(sized.mean() * 100), 2) if sized.notna().any() else None   # 손절폭 8% 초과분은 비중 8%/손절폭, -8% 종가 손절 포함
            if rname != "ALL":
                diff = _daily_diff(p, m, base_mask, "ret20")
                lo, hi = block_bootstrap_ci(diff.values, 20)
                r["vsAllPp"] = round(float(diff.mean() * 100), 2) if len(diff) else None
                r["vsAllCI95"] = [round(lo * 100, 2), round(hi * 100, 2)] if lo == lo else None
            res[rname] = r
        res["_baseRet20"] = round(base_ret * 100, 2)
        out["periods"][pname] = res
    out["verdict"] = verdict(out)
    return out


def verdict(out: dict) -> dict:
    """견고 판정 (a)~(e). 규칙마다 항목별 통과 여부와 PASS."""
    P = out["periods"]
    v = {}
    for r in RULES:
        if r in ("VOL",):
            continue
        t = [P[x][r].get("lift_G15") for x in ("T1", "T2", "T3")]
        a = all(x is not None and x >= 1.5 for x in t)
        b = sum(1 for g in ("G10", "G15", "G20") if (P["ALL"][r].get(f"lift_{g}") or 0) >= 1.5) >= 2
        ci = P["ALL"][r].get("vsAllCI95")
        c = bool(ci and ci[0] > 0)
        t3, t3_all = P["T3"][r].get("mean_ret20"), P["T3"]["_baseRet20"]
        d = t3 is not None and t3 > t3_all
        ctrl = CONTROL_OF[r]
        e = (P["ALL"][r].get("lift_G15") or 0) > (P["ALL"][ctrl].get("lift_G15") or 0)
        v[r] = {"a_G15_lift_all_thirds": a, "b_two_of_three_defs": b, "c_ret_CI_above0": c, "d_T3_beats_all": d, "e_beats_control": e, "control": ctrl,
                "PASS": bool(a and b and c and d and e)}
    return v


def to_markdown(res: dict) -> str:
    o = ["# 먹을 자리 조건 견고성 확인", "", f"> 실행 {res['ranAt']} · 규칙·판정 기준 2026-10-10 고정 · 생존편향·상승장 한계 · 연구용",
         "판정(PASS): (a) G15 배수 T1·T2·T3 모두 ≥1.5 (b) G10·G15·G20 중 2개 이상 전체 배수 ≥1.5 (c) 20일 수익률 ALL 대비 95% 구간 하한 >0 (d) T3 평균수익 > 같은 기간 ALL (e) 대조 규칙보다 G15 배수 높음", ""]
    for m in res["markets"].values():
        o += [f"## {m['market'].upper()} — 분석 행 {m['rows']:,}", "", "### 규칙별 판정", "| 규칙 | 정의 | a | b | c | d | e(대조) | PASS |", "|---|---|---|---|---|---|---|---|"]
        for r, v in m["verdict"].items():
            o.append(f"| {r} | {RULES[r]} | {'O' if v['a_G15_lift_all_thirds'] else 'X'} | {'O' if v['b_two_of_three_defs'] else 'X'} | {'O' if v['c_ret_CI_above0'] else 'X'} | {'O' if v['d_T3_beats_all'] else 'X'} | {'O' if v['e_beats_control'] else 'X'}({v['control']}) | **{'PASS' if v['PASS'] else 'FAIL'}** |")
        for pname, per in m["periods"].items():
            o += ["", f"### 기간 {pname} (ALL 20일 평균 {per['_baseRet20']}%)", "| 규칙 | 행 | 독립 | 종목 | 배수 G10 | G15 | G20 | G40 | G15% | 평균20일% | 중앙값% | 손절포함% | 비중축소·손절% | ALL 대비 %p | 95% 구간 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for r, x in per.items():
                if r.startswith("_"):
                    continue
                o.append(f"| {r} | {x['rows']} | {x['independent']} | {x['stocks']} | {x.get('lift_G10')} | {x.get('lift_G15')} | {x.get('lift_G20')} | {x.get('lift_G40')} | {x.get('rate_G15')} | {x['mean_ret20']} | {x['median_ret20']} | {x['mean_stop20']} | {x['sizedStop20']} | {x.get('vsAllPp')} | {x.get('vsAllCI95')} |")
        o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    res = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "goods": {k: list(v) for k, v in GOODS.items()}, "rules": RULES, "markets": {}}
    for market in ("us", "kr"):
        rows = json.loads((ROOT / "docs" / "data" / f"latest_{market}.json").read_text(encoding="utf-8"))
        syms = {}
        for u in (r for r in rows if r.get("status") == "OK" and r.get("code")):
            code = str(u["code"])
            syms[code.replace(".", "-") if market == "us" else code.zfill(6) + (".KQ" if u.get("market") == "KOSDAQ" else ".KS")] = code
        panel = build_panel(fetch_ohlcv(list(syms)), market)
        res["markets"][market] = {"market": market, **evaluate(panel)}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "robust.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "robust.md").write_text(to_markdown(res), encoding="utf-8")
    print(to_markdown(res))


if __name__ == "__main__":
    main()
