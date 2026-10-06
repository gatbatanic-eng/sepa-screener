"""매집 가능성 필터(가격·거래량 부분)의 과거 백테스트. (python -m accumulation.backtest)
신호일 다음 거래일 종가에 진입해 5·20·40거래일 뒤 종가에 청산(편도 비용 한국 25bp·미국 10bp). 비교 대상:
 ALL(그날 지표를 계산할 수 있는 전 종목 평균), TREND(D만 충족), ACC(A·B·D·E 모두), ACC3(점수 3 이상).
날짜별 평균을 한 관측치로 보고(같은 날 신호는 함께 움직이므로), 차이의 95% 구간은 이동 블록 부트스트랩(블록=보유일수, 2,000회, 시드 고정).
한계: 현재의 유동성 상위 종목만 쓰므로 생존편향이 있고, 수급은 이력이 없어 넣지 못했다."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .features import CFG, compute

log = logging.getLogger("accumulation")
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "research" / "accumulation"
HORIZONS = (5, 20, 40)
COST = {"kr": 0.0025 * 2, "us": 0.0010 * 2}        # 왕복 비용(에이전트 리그와 같은 가정)
GROUPS = ("ALL", "TREND", "ACC", "ACC3")
COOLDOWN = 10                                       # 같은 종목의 신호는 10거래일 안에 다시 세지 않는다(ACC·ACC3)
START_FETCH, START_EVAL = "2023-06-01", "2024-09-02"
BOOT_N, SEED = 2000, 20261006


def fetch_ohlcv(symbols: list[str], start: str = START_FETCH, chunk: int = 100) -> dict[str, pd.DataFrame]:
    import time
    import yfinance as yf
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        frame = None
        for attempt in range(3):
            try:
                frame = yf.download(part, start=start, interval="1d", auto_adjust=True, progress=False, threads=True, group_by="column")
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("다운로드 실패(%d/3): %s", attempt + 1, exc)
                time.sleep(5 * (attempt + 1))
        if frame is None or frame.empty:
            continue
        for s in part:
            try:
                d = pd.DataFrame({k: frame[k][s] for k in ("Open", "High", "Low", "Close", "Volume")}).dropna()
            except KeyError:
                continue
            d.columns = ["open", "high", "low", "close", "volume"]
            d = d[(d["close"] > 0) & (d["volume"] >= 0)]
            if len(d) > 260:
                d.index = pd.DatetimeIndex(d.index).tz_localize(None)
                out[s] = d
        time.sleep(1)
    return out


def forward_returns(close: pd.Series, h: int, cost: float) -> pd.Series:
    """신호일 t → 진입 t+1 종가, 청산 t+1+h 종가. 비용을 뺀 수익률. 마지막 h+1일은 NaN."""
    return close.shift(-(1 + h)) / close.shift(-1) - 1 - cost


def build_panel(data: dict[str, pd.DataFrame], market: str, index: pd.Series) -> pd.DataFrame:
    """종목×날짜 행. 열: code, A,B,D,E,score,ok, fwd{h}, idx{h}."""
    rows = []
    for code, df in data.items():
        f = compute(df)
        f["code"] = code
        for h in HORIZONS:
            f[f"fwd{h}"] = forward_returns(df["close"], h, COST[market])
            f[f"idx{h}"] = forward_returns(index.reindex(df.index).ffill(), h, 0.0)
        rows.append(f)
    panel = pd.concat(rows)
    panel.index.name = "date"
    return panel.reset_index()


def _first_of_cluster(df: pd.DataFrame, mask: pd.Series) -> pd.Series:
    """같은 종목에서 직전 COOLDOWN거래일 안에 이미 신호가 있었으면 제외."""
    sig = df.assign(m=mask.values).sort_values(["code", "date"])
    prior = sig.groupby("code")["m"].transform(lambda s: s.astype(float).shift().rolling(COOLDOWN, min_periods=1).max().fillna(0).astype(bool))
    keep = sig["m"] & ~prior
    return keep.reindex(df.index).fillna(False)


def select(panel: pd.DataFrame) -> dict[str, pd.Series]:
    ok = panel["ok"]
    acc = ok & panel["A"] & panel["B"] & panel["D"] & panel["E"]
    acc3 = ok & (panel["score"] >= 3)
    return {"ALL": ok, "TREND": ok & panel["D"], "ACC": _first_of_cluster(panel, acc), "ACC3": _first_of_cluster(panel, acc3)}


def block_bootstrap_ci(x: np.ndarray, block: int, n: int = BOOT_N, seed: int = SEED) -> tuple[float, float]:
    """이동 블록 부트스트랩으로 평균의 95% 구간. 관측치가 블록의 2배보다 적으면 NaN."""
    x = np.asarray(x, float)
    if len(x) < max(2 * block, 10):
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    starts = np.arange(len(x) - block + 1)
    k = int(np.ceil(len(x) / block))
    means = np.empty(n)
    for i in range(n):
        idx = rng.choice(starts, k)
        means[i] = np.concatenate([x[s:s + block] for s in idx])[:len(x)].mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def stats_for(panel: pd.DataFrame, masks: dict[str, pd.Series], h: int, lo: str, hi: str | None = None) -> dict:
    """기간 [lo, hi) 안에서 그룹별 평균 수익률과 ALL 대비 초과. 수익률이 NaN인 행(최근 h+1일)은 뺀다."""
    p = panel[(panel["date"] >= lo) & ((panel["date"] < hi) if hi else True)]
    col = f"fwd{h}"
    out = {}
    base = None
    for g in GROUPS:
        sel = p[masks[g].reindex(p.index).fillna(False) & p[col].notna()]
        daily = sel.groupby("date")[col].mean()
        if g == "ALL":
            base = daily
        diff = (daily - base.reindex(daily.index)).dropna() if base is not None else daily
        lo_ci, hi_ci = block_bootstrap_ci(diff.values, h)
        idx_ex = (sel[col] - sel[f"idx{h}"]).groupby(sel["date"]).mean().dropna()
        out[g] = {"signals": int(len(sel)), "days": int(len(daily)),
                  "meanPct": round(float(sel[col].mean() * 100), 2) if len(sel) else None,
                  "medianPct": round(float(sel[col].median() * 100), 2) if len(sel) else None,
                  "winRatePct": round(float((sel[col] > 0).mean() * 100), 1) if len(sel) else None,
                  "dailyMeanPct": round(float(daily.mean() * 100), 2) if len(daily) else None,
                  "vsAllPp": round(float(diff.mean() * 100), 2) if len(diff) and g != "ALL" else None,
                  "vsAllCI95": [round(lo_ci * 100, 2), round(hi_ci * 100, 2)] if g != "ALL" and lo_ci == lo_ci else None,
                  "vsIndexPp": round(float(idx_ex.mean() * 100), 2) if len(idx_ex) else None}
    return out


def run_market(market: str, universe: list[dict], index_symbol: dict[str, str] | str) -> dict:
    syms = {}
    for u in universe:
        code = str(u["code"])
        sym = code.replace(".", "-") if market == "us" else code.zfill(6) + (".KQ" if u.get("market") == "KOSDAQ" else ".KS")
        syms[sym] = code
    data = fetch_ohlcv(list(syms))
    idx_syms = [index_symbol] if isinstance(index_symbol, str) else sorted(set(index_symbol.values()))
    idx_data: dict[str, pd.Series] = {}
    import yfinance as yf
    for s in idx_syms:
        f = yf.download(s, start=START_FETCH, interval="1d", auto_adjust=True, progress=False)
        close = f["Close"].iloc[:, 0] if isinstance(f.columns, pd.MultiIndex) else f["Close"]
        close.index = pd.DatetimeIndex(close.index).tz_localize(None)
        idx_data[s] = close.dropna()
    main_idx = idx_data[idx_syms[0]]
    if market == "kr":  # 코스닥 종목은 코스닥 지수와 비교
        panels = []
        for ex, isym in index_symbol.items():
            sub = {s: d for s, d in data.items() if s.endswith(".KQ" if ex == "KOSDAQ" else ".KS")}
            if sub:
                panels.append(build_panel(sub, market, idx_data[isym]))
        panel = pd.concat(panels, ignore_index=True)
    else:
        panel = build_panel(data, market, main_idx)
    panel = panel[panel["date"] >= START_EVAL].reset_index(drop=True)
    masks = select(panel)
    dates = sorted(panel["date"].unique())
    mid = str(pd.Timestamp(dates[len(dates) // 2]).date())
    res = {"market": market, "stocks": len(data), "firstDate": str(pd.Timestamp(dates[0]).date()), "lastDate": str(pd.Timestamp(dates[-1]).date()),
           "splitDate": mid, "passRate": {g: round(float(masks[g].mean() * 100), 2) for g in GROUPS},
           "condRate": {c: round(float(panel.loc[panel["ok"], c].mean() * 100), 1) for c in "ABDE"}, "horizons": {}}
    for h in HORIZONS:
        res["horizons"][str(h)] = {"all": stats_for(panel, masks, h, START_EVAL),
                                   "firstHalf": stats_for(panel, masks, h, START_EVAL, mid),
                                   "secondHalf": stats_for(panel, masks, h, mid)}
    return res


def to_markdown(result: dict) -> str:
    o = ["# 매집 가능성 필터 백테스트 (가격·거래량 부분)", "", f"> 실행 {result['ranAt']} · 생존편향 있음(현재 유동성 상위 종목) · 수급 미포함 · 참고용이며 매수 신호가 아닙니다.", "",
         f"조건 값: {json.dumps(CFG, ensure_ascii=False)}", ""]
    for m in result["markets"].values():
        o += [f"## {m['market'].upper()} — 종목 {m['stocks']}개, {m['firstDate']} ~ {m['lastDate']} (전·후반 분할 {m['splitDate']})",
              f"조건별 충족률(%): {m['condRate']} · 그룹 통과율(%): {m['passRate']}", ""]
        for h, per in m["horizons"].items():
            o += [f"### {h}거래일 보유", "| 기간 | 그룹 | 신호 | 날짜 | 평균% | 중앙값% | 승률% | ALL 대비 %p | 95% 구간 | 지수 대비 %p |", "|---|---|---|---|---|---|---|---|---|---|"]
            for pname, label in (("all", "전체"), ("firstHalf", "전반"), ("secondHalf", "후반")):
                for g, s in per[pname].items():
                    o.append(f"| {label} | {g} | {s['signals']} | {s['days']} | {s['meanPct']} | {s['medianPct']} | {s['winRatePct']} | {s['vsAllPp']} | {s['vsAllCI95']} | {s['vsIndexPp']} |")
            o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kr = json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8"))
    us = json.loads((ROOT / "docs" / "data" / "latest_us.json").read_text(encoding="utf-8"))
    kr_u = [r for r in kr if r.get("status") == "OK" and r.get("code")]
    us_u = [r for r in us if r.get("status") == "OK" and r.get("code")]
    result = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "config": CFG, "horizons": HORIZONS, "markets": {}}
    result["markets"]["kr"] = run_market("kr", kr_u, {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"})
    result["markets"]["us"] = run_market("us", us_u, "^GSPC")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "backtest.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "backtest.md").write_text(to_markdown(result), encoding="utf-8")
    print(to_markdown(result))


if __name__ == "__main__":
    main()
