"""초기 전환 신호(EARLY·RSACC) 5년 백테스트. (python -m leader_backtest.early_signal)
환경변수 LB_FETCH_START, LB_EVAL_START, LB_OUT 은 main 과 같다. 권장: 2020-10-01 / 2022-01-03.

규칙은 2026-11-09, 4개월 역분석(rally_4m) 직후·이 백테스트 결과를 보기 전에 고정했다. 결과를 보고 바꾸지 않는다(바꾸면 새 계열).
신호 정의는 rally_4m.build 와 똑같다(매 거래일 t, t일 종가까지의 데이터만 사용).
  TREND  sepa 조건 1~7 + RS v2 >= 80 (현재 스크리너 8/8)
  HI60V  종가 >= 전일까지 60일 최고가 & 거래량 >= 전일까지 50일 평균 x 1.5
  RSACC  종가 > 50일선 & 50일선 > 10거래일 전 & RS v2 20일 변화 >= +15
  EARLY  RSACC & 종가 >= 52주 저가 x 1.3 & RS 라인(종목/지수) 63일 신고가      ← 사전 지정 기본 신호
  ALL    판정 가능한 전 종목-일(기준선). ALL 외에는 같은 종목 직전 10거래일 안 재신호 제외.
평가 1(고정 보유): 신호 다음 거래일 종가 진입, 20·40·60거래일 뒤 종가(HOLD) / 종가 -8% 손절 포함(STOP8). 편도 비용 한국 25bp·미국 10bp.
  날짜별 평균을 한 관측치로 보고 ALL 대비 차이의 95% 구간은 이동 블록 부트스트랩. 연도별 ALL 대비 차이도 따로 본다(국면 의존 확인).
  사전 판정 기준: EARLY 의 HOLD40 ALL 대비 차이가 두 시장 중 하나 이상에서 95% 구간 하한 > 0 이고, 연도의 과반에서 양수면 '유망'.
평가 2(운용형): ops.simulate 그대로(비중 12.5%, 최대 8종목, -8% 손절, E1=50일선 이탈, E2=고점 대비 -15%, 지수>200일선 필터 켬/끔).
  신호 EARLY·RSACC·TREND. 대조군: 같은 날 EARLY 신호 수만큼 판정 가능 종목에서 무작위(시드 5개).
유니버스: 현재 세파 유니버스(한국 유동성 상위 후보·S&P500) — 생존편향. 이전 백테스트(sepa_go, ops)와 같은 조건이라 직접 비교 가능.
참고용이며 매수 신호가 아니다."""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

from .main import COST, OUT_DIR, ROOT, START_EVAL, START_FETCH, block_bootstrap_ci, fetch_ohlcv
from .ops import P as OPS_P, metrics, simulate
from .rally_4m import build
from .rules import forward_returns
from .sepa_go import first_of_cluster

log = logging.getLogger("leader_backtest.early_signal")
GROUPS = ("ALL", "TREND", "HI60V", "RSACC", "EARLY")
HORIZONS = (20, 40, 60)
MODES = {"HOLD": None, "STOP8": 0.08}
OPS_SIGNALS = ("EARLY", "RSACC", "TREND")


def masks_of(ev: pd.DataFrame) -> dict[str, pd.Series]:
    ok = ev["ok"] & ev["rs"].notna()
    out = {"ALL": ok}
    for g in GROUPS[1:]:
        out[g] = first_of_cluster(ev, ok & ev[g].fillna(False).astype(bool))
    return out


def stats(ev: pd.DataFrame, masks: dict, col: str, h: int, lo, hi=None) -> dict:
    p = ev[(ev["date"] >= lo) & ((ev["date"] < hi) if hi is not None else True)]
    out, base = {}, None
    for g in GROUPS:
        sel = p[masks[g].reindex(p.index).fillna(False) & p[col].notna()]
        daily = sel.groupby("date")[col].mean()
        if g == "ALL":
            base = daily
        diff = (daily - base.reindex(daily.index)).dropna()
        ci = block_bootstrap_ci(diff.values, h) if g != "ALL" and len(diff) else (float("nan"), float("nan"))
        out[g] = {"signals": int(len(sel)), "meanPct": round(float(sel[col].mean() * 100), 2) if len(sel) else None,
                  "medianPct": round(float(sel[col].median() * 100), 2) if len(sel) else None,
                  "winRatePct": round(float((sel[col] > 0).mean() * 100), 1) if len(sel) else None,
                  "vsAllPp": round(float(diff.mean() * 100), 2) if len(diff) and g != "ALL" else None,
                  "vsAllCI95": [round(ci[0] * 100, 2), round(ci[1] * 100, 2)] if ci[0] == ci[0] else None}
    return out


def by_year(ev: pd.DataFrame, masks: dict, col: str) -> dict:
    out = {}
    base = ev[masks["ALL"] & ev[col].notna()].groupby("date")[col].mean()
    for g in GROUPS[1:]:
        sel = ev[masks[g] & ev[col].notna()]
        daily = sel.groupby("date")[col].mean()
        diff = (daily - base.reindex(daily.index)).dropna()
        out[g] = {str(y): {"n": int((sel["date"].dt.year == y).sum()), "vsAllPp": round(float(d.mean() * 100), 2)}
                  for y, d in diff.groupby(diff.index.year)}
    return out


def run_ops(panel: pd.DataFrame, masks: dict, market: str, regime_index: pd.Series) -> dict:
    piv = lambda col: panel.pivot(index="date", columns="code", values=col).sort_index()  # noqa: E731
    close, rs = piv("close"), piv("rs")
    sma50 = close.rolling(50).mean()

    def grid(mask: pd.Series) -> pd.DataFrame:
        full = pd.Series(False, index=panel.index)
        m2 = mask.dropna().astype(bool)
        full.loc[m2.index] = m2.values
        return panel.assign(m=full.values).pivot(index="date", columns="code", values="m").reindex(index=close.index, columns=close.columns).fillna(False).astype(bool)

    sigs = {g: grid(masks[g]) for g in OPS_SIGNALS}
    okm = grid(masks["ALL"])
    regime = regime_index > regime_index.rolling(OPS_P["regime_sma"]).mean()
    first = int(np.argmax(close.index >= pd.Timestamp(START_EVAL)))
    sl = slice(max(first - 1, 0), None)
    bench = regime_index[regime_index.index >= close.index[first]]
    out = {"first": str(close.index[first].date()), "last": str(close.index[-1].date()), "results": {}}
    cost = COST[market] / 2
    for ex in ("E1", "E2"):
        for rg in (True, False):
            reg = regime if rg else pd.Series(True, index=regime.index)
            tag = f"{ex}|{'REG' if rg else 'NOREG'}"
            for g in OPS_SIGNALS:
                s = simulate(close.iloc[sl], sma50.iloc[sl], sigs[g].iloc[sl], rs.iloc[sl], reg, ex, cost)
                out["results"][f"{g}|{tag}"] = metrics(s["equity"][s["equity"].index >= close.index[first]], s["trades"], bench)
            per_day = sigs["EARLY"].iloc[sl].sum(axis=1).to_numpy()
            okv = okm.iloc[sl].to_numpy(bool)
            ms = []
            for seed in OPS_P["seeds"]:
                rng = np.random.default_rng(seed)
                rnd = np.zeros(okv.shape, bool)
                for i, n in enumerate(per_day):
                    cand = np.flatnonzero(okv[i])
                    if n and len(cand):
                        rnd[i, rng.choice(cand, min(int(n), len(cand)), replace=False)] = True
                s = simulate(close.iloc[sl], sma50.iloc[sl], pd.DataFrame(rnd, index=close.index[sl], columns=close.columns), rs.iloc[sl], reg, ex, cost)
                ms.append(metrics(s["equity"][s["equity"].index >= close.index[first]], s["trades"], bench))
            keys = ("totalPct", "cagrPct", "mddPct", "trades", "winPct", "expectancyPct")
            out["results"][f"RANDOM|{tag}"] = {k: (round(float(np.mean([m[k] for m in ms if m[k] is not None])), 2) if any(m[k] is not None for m in ms) else None) for k in keys} | {
                "totalPctRange": [min(m["totalPct"] for m in ms), max(m["totalPct"] for m in ms)]}
    return out


def run_market(market: str, universe: list[dict], index_map: dict[str, str]) -> dict:
    import yfinance as yf
    syms, ex_of = [], {}
    for u in universe:
        code = str(u["code"])
        s = code.replace(".", "-") if market == "us" else code.zfill(6) + (".KQ" if u.get("market") == "KOSDAQ" else ".KS")
        syms.append(s)
        ex_of[s] = ("KOSDAQ" if s.endswith(".KQ") else "KOSPI") if market == "kr" else "US"
    data = fetch_ohlcv(syms, start=START_FETCH)
    idx = {}
    for ex, isym in index_map.items():
        f = yf.download(isym, start=START_FETCH, interval="1d", auto_adjust=True, progress=False)
        cl = f["Close"].iloc[:, 0] if isinstance(f.columns, pd.MultiIndex) else f["Close"]
        cl.index = pd.DatetimeIndex(cl.index).tz_localize(None)
        idx[ex] = cl.dropna()
    panel = build(data, {s: idx[ex_of[s]] for s in data})
    keep_cols = ["date", "code", "close", "ok", "rs", *GROUPS[1:]]
    panel = panel[keep_cols].copy()
    fr = []
    for s, df in data.items():
        f = pd.DataFrame(index=df.index)
        for h in HORIZONS:
            for mode, stop in MODES.items():
                f[f"{mode}{h}"] = forward_returns(df["close"], h, COST[market], stop)
        f["code"] = s
        f.index.name = "date"
        fr.append(f.reset_index())
    fr = pd.concat(fr)
    panel = panel.merge(fr, on=["date", "code"], how="left")
    ev = panel[panel["date"] >= START_EVAL].copy()
    masks = masks_of(ev)
    dates = sorted(ev["date"].unique())
    first, last, mid = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1]), pd.Timestamp(dates[len(dates) // 2])
    res = {"market": market, "stocks": len(data), "firstDate": str(first.date()), "lastDate": str(last.date()), "splitDate": str(mid.date()),
           "signalCounts": {g: int(masks[g].sum()) for g in GROUPS}, "eventStudy": {}, "byYear": {}}
    for h in HORIZONS:
        for mode in MODES:
            col = f"{mode}{h}"
            res["eventStudy"][col] = {"all": stats(ev, masks, col, h, first), "firstHalf": stats(ev, masks, col, h, first, mid),
                                      "secondHalf": stats(ev, masks, col, h, mid)}
            res["byYear"][col] = by_year(ev, masks, col)
    full_masks = {g: m.reindex(panel.index) for g, m in masks.items()}
    res["ops"] = run_ops(panel, full_masks, market, idx["KOSPI" if market == "kr" else "US"])
    e = res["eventStudy"]["HOLD40"]["all"]["EARLY"]
    yrs = res["byYear"]["HOLD40"]["EARLY"]
    res["verdict"] = {"hold40VsAll": e["vsAllPp"], "ci": e["vsAllCI95"], "positiveYears": sum(1 for v in yrs.values() if v["vsAllPp"] > 0), "years": len(yrs)}
    return res


def to_markdown(res: dict) -> str:
    o = ["# 초기 전환 신호(EARLY·RSACC) 5년 백테스트", "", f"> 실행 {res['ranAt']} · 규칙 2026-11-09 고정 · 생존편향 있음 · 참고용이며 매수 신호가 아닙니다.", "", "```", __doc__.strip(), "```", ""]
    for m in res["markets"].values():
        o += [f"## {m['market'].upper()} — 종목 {m['stocks']}개, {m['firstDate']} ~ {m['lastDate']} (전·후반 {m['splitDate']})", "",
              f"신호 수: {m['signalCounts']}", "", f"사전 판정용(EARLY HOLD40): {m['verdict']}", ""]
        for col, per in m["eventStudy"].items():
            o += [f"### 고정 보유 {col}", "| 기간 | 그룹 | 신호 | 평균% | 중앙값% | 승률% | ALL 대비 %p | 95% 구간 |", "|---|---|---|---|---|---|---|---|"]
            for pn, lab in (("all", "전체"), ("firstHalf", "전반"), ("secondHalf", "후반")):
                for g, s in per[pn].items():
                    o.append(f"| {lab} | {g} | {s['signals']} | {s['meanPct']} | {s['medianPct']} | {s['winRatePct']} | {s['vsAllPp']} | {s['vsAllCI95']} |")
            o.append("")
        o += ["### 연도별 ALL 대비 %p (HOLD40)", "| 그룹 | " + " | ".join(next(iter(m["byYear"]["HOLD40"].values()))) + " |", "|---|" + "---|" * len(next(iter(m["byYear"]["HOLD40"].values())))]
        for g, ys in m["byYear"]["HOLD40"].items():
            o.append(f"| {g} | " + " | ".join(f"{v['vsAllPp']} (n={v['n']})" for v in ys.values()) + " |")
        o += ["", f"### 운용형 ({m['ops']['first']} ~ {m['ops']['last']})", "| 신호|청산|시장필터 | 누적% | 연환산% | MDD% | 거래 | 승률% | 기대값% | 지수 누적%/MDD% |", "|---|---|---|---|---|---|---|---|---|"]
        for k, v in m["ops"]["results"].items():
            if k.startswith("RANDOM"):
                o.append(f"| {k} (5시드 평균) | {v['totalPct']} (범위 {v['totalPctRange']}) | {v['cagrPct']} | {v['mddPct']} | {v['trades']} | {v['winPct']} | {v['expectancyPct']} | - |")
            else:
                o.append(f"| {k} | {v['totalPct']} | {v['cagrPct']} | {v['mddPct']} | {v['trades']} | {v['winPct']} | {v['expectancyPct']} | {v['benchTotalPct']}/{v['benchMddPct']} |")
        o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load = lambda m: [r for r in json.loads((ROOT / "docs" / "data" / f"latest_{m}.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]  # noqa: E731
    res = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "fetchStart": START_FETCH, "evalStart": START_EVAL, "markets": {}}
    res["markets"]["us"] = run_market("us", load("us"), {"US": "^GSPC"})
    res["markets"]["kr"] = run_market("kr", load("kr"), {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "early_signal.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT_DIR / "early_signal.md").write_text(to_markdown(res), encoding="utf-8")
    print(to_markdown(res))


if __name__ == "__main__":
    main()
