"""SEPA v2 GO 신호 백테스트 — 실제 스크리너의 sepa/ 판정 코드를 과거 날짜마다 재생한다.
python -m leader_backtest.sepa_go   (환경변수 LB_FETCH_START, LB_EVAL_START, LB_OUT 은 main 과 같다. 권장: 2020-10-01 / 2022-01-03)

규칙은 2026-11-09 결과를 보기 전에 고정했다. 결과를 보고 바꾸지 않는다(바꾸면 새 계열).

판정(매 거래일 t, t일 종가까지의 데이터만 사용)
  TREND     screening.py 조건 1~7(종가>150·200일선, 150>200, 200일선>20거래일 전, 50>150·200, 종가>50일선,
            종가>=52주 저가x1.30, 종가>=52주 고가x0.75; 52주=252거래일 고가/저가) + 조건8 RS v2 RS_SCORE>=80.
            RS v2 = 21·63·126·252거래일 초과수익(벤치마크 대비)의 그날 유니버스 백분위 가중합(0.1/0.4/0.3/0.2),
            rs_change_20d 는 20거래일 전 시점으로 다시 랭킹. 벤치마크: 미국 ^GSPC, 한국 소속시장 ^KS11/^KQ11.
  GO_*      TREND 통과일 중 후보(아래)만 sepa.pipeline.evaluate_stock_v2 를 그대로 호출해 entry_state 를 얻는다.
            후보 = 당일 또는 직전 10거래일 안에 '거래량/50일평균(당일 포함)>=1.4 & CLV>=0.7' 인 날이 있는 TREND 통과일.
            (GO_BREAKOUT 은 당일 그 조건이, GO_PULLBACK 은 10거래일 안의 확인된 돌파가 필요조건이므로 누락 없음.)
            NEW = 현재 브랜치 설정(ATR% 수축 <=0.90), OLD = 이전 설정(금액 ATR 수축 <=0.75).
그룹(ALL 외에는 같은 종목 직전 10거래일 안 재신호 제외)
  ALL        TREND 판정이 가능한 전 종목-일(기준선)
  TREND      위 TREND 통과
  SIMPLE_BO  TREND & 종가가 전일까지 60일 최고가 대비 0~+3% & 거래량비>=1.4 & CLV>=0.7  (셋업 조건 없는 단순 돌파, 셋업의 몫을 가르는 비교군)
  GO_BO_NEW  NEW 의 GO_BREAKOUT
  GO_PB_NEW  NEW 의 GO_PULLBACK
  GO_NEW     GO_BO_NEW | GO_PB_NEW   (사전 지정 기본 그룹)
  GO_OLD     OLD 의 GO_BREAKOUT | GO_PULLBACK
평가 1(고정 보유): 신호일 다음 거래일 종가 진입, 20·40거래일 뒤 종가(HOLD) 또는 종가 -8% 손절 포함(STOP8). 편도 비용 한국 25bp·미국 10bp.
  날짜별 평균을 한 관측치로 보고 ALL 대비 차이의 95% 구간은 이동 블록 부트스트랩(블록=보유일수, 2,000회).
평가 2(운용형): ops.py 의 simulate 그대로(비중 12.5%, 최대 8종목, -8% 손절, E1=50일선 이탈, E2=고점 대비 -15%, 지수>200일선 필터 켬/끔).
  신호 GO_NEW·SIMPLE_BO. 대조군: 같은 날 GO_NEW 신호 수만큼 판정 가능 종목에서 무작위(시드 5개).
한계: 현재 유니버스(유동성 상위·S&P500)만 써서 생존편향, 레짐 sizeFactor 는 반영하지 않음(운용형 시장 필터로 대체),
  ATR60 등은 판정일 기준 최근 400봉으로 계산(지수평활 수렴 차이는 미미). 참고용이며 매수 신호가 아니다."""
from __future__ import annotations

import datetime as dt
import json
import logging
import time

import numpy as np
import pandas as pd

from sepa.config import load_config
from sepa.pipeline import evaluate_stock_v2
from sepa.rs import RsV2

from .main import COST, OUT_DIR, ROOT, START_EVAL, START_FETCH, block_bootstrap_ci, fetch_ohlcv
from .ops import P as OPS_P, metrics, simulate
from .rules import forward_returns

log = logging.getLogger("leader_backtest.sepa_go")
CFG_NEW = load_config()
CFG_OLD = load_config({"setup.atr_normalize": False, "setup.atr_contraction_max": 0.75})
HORIZONS = (20, 40)
MODES = {"HOLD": None, "STOP8": 0.08}
COOLDOWN = 10
WINDOW = 400                     # evaluate_stock_v2 에 넘기는 최근 봉 수(>= min_trading_days 260)
GROUPS = ("ALL", "TREND", "SIMPLE_BO", "GO_BO_NEW", "GO_PB_NEW", "GO_NEW", "GO_OLD")
GO_STATES = ("GO_BREAKOUT", "GO_PULLBACK")
OPS_SIGNALS = ("GO_NEW", "SIMPLE_BO")


def stock_features(df: pd.DataFrame, bench: pd.Series) -> pd.DataFrame:
    """df: 소문자 open/high/low/close/volume. 인과적 지표만."""
    c, h, l, v = (df[k].astype(float) for k in ("close", "high", "low", "volume"))
    tc = CFG_NEW.trend
    s50, s150, s200 = c.rolling(tc.sma_fast).mean(), c.rolling(tc.sma_mid).mean(), c.rolling(tc.sma_slow).mean()
    hi52 = h.rolling(tc.week52_window, min_periods=1).max()
    lo52 = l.rolling(tc.week52_window, min_periods=1).min()
    lb = CFG_NEW.data.ma_trend_lookback
    out = pd.DataFrame(index=df.index)
    conds = [(c > s150) & (c > s200), s150 > s200, s200 > s200.shift(lb), (s50 > s150) & (s50 > s200), c > s50,
             c >= lo52 * tc.low_52w_mult, c >= hi52 * tc.high_52w_mult]
    for i, x in enumerate(conds, 1):
        out[f"c{i}"] = x.fillna(False)
    out["c17"] = np.logical_and.reduce([out[f"c{i}"] for i in range(1, 8)])
    n = np.arange(len(df))
    out["ok"] = (n + 1 >= CFG_NEW.data.min_trading_days) & s200.shift(lb).notna().to_numpy()
    b = bench.reindex(df.index).ffill()
    rc = CFG_NEW.rs
    for p in (rc.period_short, rc.period_mid, rc.period_long, rc.period_year):
        out[f"er{p}"] = (c / c.shift(p) - 1) - (b / b.shift(p) - 1)
    av50 = v.rolling(CFG_NEW.setup.vol_dryup_slow).mean()
    rng = (h - l).replace(0, np.nan)
    out["volr"] = v / av50
    out["clv"] = ((c - l) / rng).fillna(0.0)
    bo = ((out["volr"] >= CFG_NEW.entry.breakout_volume_min) & (out["clv"] >= CFG_NEW.entry.breakout_clv_min)).fillna(False)
    out["bo_day"] = bo
    out["bo_recent"] = bo.astype(float).shift(1).rolling(CFG_NEW.entry.pullback_lookback, min_periods=1).max().fillna(0).astype(bool)
    piv60 = h.rolling(60).max().shift(1)
    d60 = (c / piv60 - 1) * 100
    out["simple_bo"] = (bo & (d60 >= 0) & (d60 <= CFG_NEW.entry.go_max_pct)).fillna(False)
    out["close"], out["sma50"], out["hi52"] = c, s50, hi52
    return out


def add_rs(panel: pd.DataFrame) -> pd.DataFrame:
    rc = CFG_NEW.rs
    w = {rc.period_short: rc.weight_short, rc.period_mid: rc.weight_mid, rc.period_long: rc.weight_long, rc.period_year: rc.weight_year}
    score = 0.0
    valid = panel["ok"]
    for p, wt in w.items():
        col = panel[f"er{p}"].where(valid)
        pct = col.groupby(panel["date"]).rank(pct=True) * 100
        score = score + wt * pct
    panel["rs"] = score                                          # 4개 중 하나라도 없으면 NaN
    panel = panel.sort_values(["code", "date"])
    panel["rs20"] = panel.groupby("code")["rs"].shift(CFG_NEW.rs.accel_lookback)
    panel["rs_line"] = np.nan
    return panel


def evaluate_go(data: dict[str, pd.DataFrame], panel: pd.DataFrame, benches: dict[str, pd.Series]) -> pd.DataFrame:
    """후보 행만 실제 sepa 파이프라인으로 판정. 반환: panel 과 같은 인덱스의 state_new/state_old."""
    panel["state_new"] = None
    panel["state_old"] = None
    cand = panel[panel["trend"] & (panel["bo_day"] | panel["bo_recent"]) & (panel["date"] >= START_EVAL)]
    log.info("sepa 판정 후보 %d 종목-일", len(cand))
    t0 = time.time()
    done = 0
    for code, rows in cand.groupby("code"):
        df = data[code]
        o = pd.DataFrame({"Open": df["open"], "High": df["high"], "Low": df["low"], "Close": df["close"], "Volume": df["volume"]}).astype(float)
        bench = benches[code]
        pos_of = {d: i for i, d in enumerate(o.index)}
        for idx, r in rows.iterrows():
            i = pos_of[r["date"]]
            sl = o.iloc[max(0, i + 1 - WINDOW): i + 1]
            line = (sl["Close"] / bench.reindex(sl.index).ffill()).dropna()
            win = line.iloc[-min(CFG_NEW.rs.rs_line_high_lookback, len(line)):]
            rs = RsV2(rs_score=float(r["rs"]), rs_score_20d_ago=None if pd.isna(r["rs20"]) else float(r["rs20"]),
                      rs_change_20d=None if pd.isna(r["rs20"]) else float(r["rs"] - r["rs20"]),
                      rs_line_new_high=bool(line.iloc[-1] >= win.max()) if len(line) else None)
            c17 = [bool(r[f"c{k}"]) for k in range(1, 8)]
            for cfg, col in ((CFG_NEW, "state_new"), (CFG_OLD, "state_old")):
                out = evaluate_stock_v2(sl, cfg, cond_1_7=c17, close_today=float(r["close"]), high_52w=float(r["hi52"]), rs=rs)
                panel.at[idx, col] = out.get("entry_state")
            done += 1
            if done % 2000 == 0:
                log.info("  %d/%d (%.0fs)", done, len(cand), time.time() - t0)
    log.info("sepa 판정 완료 %d건 %.0fs", done, time.time() - t0)
    return panel


def first_of_cluster(panel: pd.DataFrame, mask: pd.Series, cooldown: int = COOLDOWN) -> pd.Series:
    sig = panel.assign(m=mask.values).sort_values(["code", "date"])
    prior = sig.groupby("code")["m"].transform(lambda s: s.astype(float).shift().rolling(cooldown, min_periods=1).max().fillna(0).astype(bool))
    return (sig["m"] & ~prior).reindex(panel.index).fillna(False)


def masks_of(panel: pd.DataFrame) -> dict[str, pd.Series]:
    ok = panel["ok"] & panel["rs"].notna()
    trend = ok & panel["trend"]
    m = {"ALL": ok, "TREND": trend, "SIMPLE_BO": trend & panel["simple_bo"],
         "GO_BO_NEW": panel["state_new"] == "GO_BREAKOUT", "GO_PB_NEW": panel["state_new"] == "GO_PULLBACK",
         "GO_NEW": panel["state_new"].isin(GO_STATES), "GO_OLD": panel["state_old"].isin(GO_STATES)}
    return {g: (x if g == "ALL" else first_of_cluster(panel, x)) for g, x in m.items()}


def stats(panel: pd.DataFrame, masks: dict, col: str, h: int, lo, hi=None) -> dict:
    p = panel[(panel["date"] >= lo) & ((panel["date"] < hi) if hi is not None else True)]
    out, base = {}, None
    for g in GROUPS:
        sel = p[masks[g].reindex(p.index).fillna(False) & p[col].notna()]
        daily = sel.groupby("date")[col].mean()
        if g == "ALL":
            base = daily
        diff = (daily - base.reindex(daily.index)).dropna() if base is not None else pd.Series(dtype=float)
        ci = block_bootstrap_ci(diff.values, h) if g != "ALL" and len(diff) else (float("nan"), float("nan"))
        out[g] = {"signals": int(len(sel)), "meanPct": round(float(sel[col].mean() * 100), 2) if len(sel) else None,
                  "medianPct": round(float(sel[col].median() * 100), 2) if len(sel) else None,
                  "winRatePct": round(float((sel[col] > 0).mean() * 100), 1) if len(sel) else None,
                  "vsAllPp": round(float(diff.mean() * 100), 2) if len(diff) and g != "ALL" else None,
                  "vsAllCI95": [round(ci[0] * 100, 2), round(ci[1] * 100, 2)] if ci[0] == ci[0] else None}
    return out


def run_market(market: str, universe: list[dict], index_map: dict[str, str]) -> dict:
    import yfinance as yf
    syms, ex_of = {}, {}
    for u in universe:
        code = str(u["code"])
        s = code.replace(".", "-") if market == "us" else code.zfill(6) + (".KQ" if u.get("market") == "KOSDAQ" else ".KS")
        syms[s] = code
        ex_of[s] = "KOSDAQ" if s.endswith(".KQ") else ("KOSPI" if market == "kr" else "US")
    data = fetch_ohlcv(list(syms), start=START_FETCH)
    idx = {}
    for ex, isym in index_map.items():
        f = yf.download(isym, start=START_FETCH, interval="1d", auto_adjust=True, progress=False)
        cl = f["Close"].iloc[:, 0] if isinstance(f.columns, pd.MultiIndex) else f["Close"]
        cl.index = pd.DatetimeIndex(cl.index).tz_localize(None)
        idx[ex] = cl.dropna()
    benches = {s: idx[ex_of[s]] for s in data}
    rows = []
    for s, df in data.items():
        f = stock_features(df, benches[s])
        f["code"] = s
        for hh in HORIZONS:
            for mode, stop in MODES.items():
                f[f"{mode}{hh}"] = forward_returns(df["close"], hh, COST[market], stop)
        rows.append(f)
    panel = pd.concat(rows)
    panel.index.name = "date"
    panel = add_rs(panel.reset_index()).reset_index(drop=True)
    panel["trend"] = panel["ok"] & panel["c17"] & (panel["rs"] >= CFG_NEW.rs.rs_min)
    panel = evaluate_go(data, panel, benches)
    ev = panel[panel["date"] >= START_EVAL].copy()
    masks = masks_of(ev)
    dates = sorted(ev["date"].unique())
    first, last = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    mid = pd.Timestamp(dates[len(dates) // 2])
    res = {"market": market, "stocks": len(data), "firstDate": str(first.date()), "lastDate": str(last.date()), "splitDate": str(mid.date()),
           "signalCounts": {g: int(masks[g].sum()) for g in GROUPS},
           "signalsByYear": {g: {str(y): int(v) for y, v in ev[masks[g]].groupby(ev[masks[g]]["date"].dt.year).size().items()} for g in GROUPS if g != "ALL"},
           "stateCountsNew": {str(k): int(v) for k, v in ev["state_new"].value_counts().items()},
           "eventStudy": {}}
    for hh in HORIZONS:
        for mode in MODES:
            col = f"{mode}{hh}"
            res["eventStudy"][col] = {"all": stats(ev, masks, col, hh, first), "firstHalf": stats(ev, masks, col, hh, first, mid),
                                      "secondHalf": stats(ev, masks, col, hh, mid)}
    res["ops"] = run_ops(panel, masks, market, idx["KOSPI" if market == "kr" else "US"])
    return res


def run_ops(panel: pd.DataFrame, masks_eval: dict, market: str, regime_index: pd.Series) -> dict:
    piv = lambda col: panel.pivot(index="date", columns="code", values=col).sort_index()
    close, sma50, rs = piv("close"), piv("sma50"), piv("rs")
    ev_index = panel.index[panel["date"] >= START_EVAL]

    def to_grid(mask: pd.Series) -> pd.DataFrame:
        full = pd.Series(False, index=panel.index)
        full.loc[ev_index] = mask.reindex(ev_index).fillna(False).values
        return panel.assign(m=full.values).pivot(index="date", columns="code", values="m").reindex(index=close.index, columns=close.columns).fillna(False).astype(bool)

    sigs = {g: to_grid(masks_eval[g]) for g in OPS_SIGNALS}
    okm = to_grid(masks_eval["ALL"])
    regime = regime_index > regime_index.rolling(OPS_P["regime_sma"]).mean()
    keep = close.index >= pd.Timestamp(START_EVAL)
    first = int(np.argmax(keep))
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
                eq = s["equity"][s["equity"].index >= close.index[first]]
                out["results"][f"{g}|{tag}"] = metrics(eq, s["trades"], bench)
            per_day = sigs["GO_NEW"].iloc[sl].sum(axis=1).to_numpy()
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
                eq = s["equity"][s["equity"].index >= close.index[first]]
                ms.append(metrics(eq, s["trades"], bench))
            keys = ("totalPct", "cagrPct", "mddPct", "trades", "winPct", "expectancyPct")
            out["results"][f"RANDOM|{tag}"] = {k: round(float(np.mean([m[k] for m in ms if m[k] is not None])), 2) if any(m[k] is not None for m in ms) else None for k in keys} | {
                "totalPctRange": [min(m["totalPct"] for m in ms), max(m["totalPct"] for m in ms)]}
    return out


def to_markdown(res: dict) -> str:
    o = ["# SEPA v2 GO 신호 백테스트", "", f"> 실행 {res['ranAt']} · 규칙 2026-11-09 고정 · 생존편향 있음 · 참고용이며 매수 신호가 아닙니다.", "", "```", __doc__.strip(), "```", ""]
    for m in res["markets"].values():
        o += [f"## {m['market'].upper()} — 종목 {m['stocks']}개, {m['firstDate']} ~ {m['lastDate']} (전·후반 {m['splitDate']})", "",
              f"신호 수: {m['signalCounts']}", "", f"연도별 신호 수: {json.dumps(m['signalsByYear'], ensure_ascii=False)}", "",
              f"NEW 판정 상태 분포(후보만): {m['stateCountsNew']}", ""]
        for col, per in m["eventStudy"].items():
            o += [f"### 고정 보유 {col}", "| 기간 | 그룹 | 신호 | 평균% | 중앙값% | 승률% | ALL 대비 %p | 95% 구간 |", "|---|---|---|---|---|---|---|---|"]
            for pn, lab in (("all", "전체"), ("firstHalf", "전반"), ("secondHalf", "후반")):
                for g, s in per[pn].items():
                    o.append(f"| {lab} | {g} | {s['signals']} | {s['meanPct']} | {s['medianPct']} | {s['winRatePct']} | {s['vsAllPp']} | {s['vsAllCI95']} |")
            o.append("")
        o += [f"### 운용형 ({m['ops']['first']} ~ {m['ops']['last']})", "| 신호|청산|시장필터 | 누적% | 연환산% | MDD% | 거래 | 승률% | 기대값% | 지수 누적%/MDD% |", "|---|---|---|---|---|---|---|---|---|"]
        for k, v in m["ops"]["results"].items():
            if k.startswith("RANDOM"):
                o.append(f"| {k} (5시드 평균) | {v['totalPct']} (범위 {v['totalPctRange']}) | {v['cagrPct']} | {v['mddPct']} | {v['trades']} | {v['winPct']} | {v['expectancyPct']} | - |")
            else:
                o.append(f"| {k} | {v['totalPct']} | {v['cagrPct']} | {v['mddPct']} | {v['trades']} | {v['winPct']} | {v['expectancyPct']} | {v['benchTotalPct']}/{v['benchMddPct']} |")
        o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load = lambda m: [r for r in json.loads((ROOT / "docs" / "data" / f"latest_{m}.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    res = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "fetchStart": START_FETCH, "evalStart": START_EVAL, "markets": {}}
    res["markets"]["us"] = run_market("us", load("us"), {"US": "^GSPC"})
    res["markets"]["kr"] = run_market("kr", load("kr"), {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "sepa_go.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT_DIR / "sepa_go.md").write_text(to_markdown(res), encoding="utf-8")
    print(to_markdown(res))


if __name__ == "__main__":
    main()
