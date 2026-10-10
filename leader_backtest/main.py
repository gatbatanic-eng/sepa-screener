"""주도주 진입 규칙 백테스트. (python -m leader_backtest.main) 규칙은 rules.py(2026-10-10 고정). 결과 research/leader_backtest/backtest.{json,md}.
날짜별 평균을 한 관측치로 보고(같은 날 신호는 함께 움직이므로) ALL 대비 차이의 95% 구간은 이동 블록 부트스트랩(블록=보유일수, 2,000회, 시드 고정).
한계: 현재 세파 유니버스(유동성 상위·S&P500)만 써서 생존편향이 있다(모든 그룹에 같게 작용하나 주도주 그룹에 유리할 수 있다). 참고용이며 매수 신호가 아니다."""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .rules import CFG, GROUPS, HORIZONS, features, forward_returns, select

log = logging.getLogger("leader_backtest")
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "research" / "leader_backtest"
COST = {"kr": 0.0025 * 2, "us": 0.0010 * 2}
START_FETCH, START_EVAL = "2023-06-01", "2024-09-02"
BOOT_N, SEED = 2000, 20261010
MODES = {"HOLD": None, "STOP8": CFG["stop_exit"]}


def fetch_ohlcv(symbols: list[str], start: str = START_FETCH, chunk: int = 100) -> dict[str, pd.DataFrame]:
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
            if len(d) > 300:
                d.index = pd.DatetimeIndex(d.index).tz_localize(None)
                out[s] = d
        time.sleep(1)
    return out


def build_panel(data: dict[str, pd.DataFrame], market: str, index: pd.Series) -> pd.DataFrame:
    rows = []
    for code, df in data.items():
        f = features(df)
        f["code"] = code
        f["close"] = df["close"]
        for h in HORIZONS:
            for mode, stop in MODES.items():
                f[f"{mode}{h}"] = forward_returns(df["close"], h, COST[market], stop)
            f[f"idx{h}"] = forward_returns(index.reindex(df.index).ffill(), h, 0.0)
        rows.append(f)
    panel = pd.concat(rows)
    panel.index.name = "date"
    panel = panel.reset_index()
    panel["rs"] = panel.groupby("date")["ret252"].rank(pct=True) * 100       # 그날 유니버스 안 12개월 수익률 백분위
    return panel[panel["date"] >= START_EVAL].reset_index(drop=True)


def block_bootstrap_ci(x: np.ndarray, block: int, n: int = BOOT_N, seed: int = SEED) -> tuple[float, float]:
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


def stats_for(panel: pd.DataFrame, masks: dict[str, pd.Series], col: str, h: int, lo: str, hi: str | None = None) -> dict:
    p = panel[(panel["date"] >= lo) & ((panel["date"] < hi) if hi else True)]
    out, base = {}, None
    for g in GROUPS:
        sel = p[masks[g].reindex(p.index).fillna(False) & p[col].notna()]
        daily = sel.groupby("date")[col].mean()
        if g == "ALL":
            base = daily
        diff = (daily - base.reindex(daily.index)).dropna()
        lo_ci, hi_ci = block_bootstrap_ci(diff.values, h) if g != "ALL" else (float("nan"), float("nan"))
        risk = sel["risk"]
        sized = (np.minimum(1.0, CFG["risk_max"] / risk) * sel[col]) if len(sel) else sel[col]
        out[g] = {"signals": int(len(sel)), "days": int(len(daily)),
                  "meanPct": round(float(sel[col].mean() * 100), 2) if len(sel) else None,
                  "medianPct": round(float(sel[col].median() * 100), 2) if len(sel) else None,
                  "winRatePct": round(float((sel[col] > 0).mean() * 100), 1) if len(sel) else None,
                  "worstPct": round(float(sel[col].min() * 100), 1) if len(sel) else None,
                  "avgRiskPct": round(float(risk.mean()), 1) if len(sel) else None,
                  "sizedMeanPct": round(float(sized.mean() * 100), 2) if len(sel) else None,     # 손절폭 8% 초과분은 비중 8%/손절폭으로 줄인 신호당 수익(미사용 자본 수익 0)
                  "vsAllPp": round(float(diff.mean() * 100), 2) if len(diff) and g != "ALL" else None,
                  "vsAllCI95": [round(lo_ci * 100, 2), round(hi_ci * 100, 2)] if lo_ci == lo_ci else None}
    return out


def run_market(market: str, universe: list[dict], index_symbol) -> dict:
    import yfinance as yf
    syms = {}
    for u in universe:
        code = str(u["code"])
        syms[code.replace(".", "-") if market == "us" else code.zfill(6) + (".KQ" if u.get("market") == "KOSDAQ" else ".KS")] = code
    data = fetch_ohlcv(list(syms))
    idx_syms = [index_symbol] if isinstance(index_symbol, str) else sorted(set(index_symbol.values()))
    idx = {}
    for s in idx_syms:
        f = yf.download(s, start=START_FETCH, interval="1d", auto_adjust=True, progress=False)
        close = f["Close"].iloc[:, 0] if isinstance(f.columns, pd.MultiIndex) else f["Close"]
        close.index = pd.DatetimeIndex(close.index).tz_localize(None)
        idx[s] = close.dropna()
    if market == "kr":
        panels = []
        for ex, isym in index_symbol.items():
            sub = {s: d for s, d in data.items() if s.endswith(".KQ" if ex == "KOSDAQ" else ".KS")}
            if sub:
                panels.append(build_panel(sub, market, idx[isym]))
        panel = pd.concat(panels, ignore_index=True)
        panel["rs"] = panel.groupby("date")["ret252"].rank(pct=True) * 100        # 한국은 코스피·코스닥을 합쳐 순위를 다시 매긴다
    else:
        panel = build_panel(data, market, idx[idx_syms[0]])
    return evaluate(panel, market, len(data))


def evaluate(panel: pd.DataFrame, market: str, n_stocks: int) -> dict:
    masks = select(panel)
    dates = sorted(panel["date"].unique())
    first, last = str(pd.Timestamp(dates[0]).date()), str(pd.Timestamp(dates[-1]).date())
    mid = str(pd.Timestamp(dates[len(dates) // 2]).date())
    res = {"market": market, "stocks": n_stocks, "firstDate": first, "lastDate": last, "splitDate": mid,
           "passRate": {g: round(float(masks[g].mean() * 100), 2) for g in GROUPS}, "results": {}}
    for h in HORIZONS:
        for mode in MODES:
            col = f"{mode}{h}"
            res["results"][col] = {"all": stats_for(panel, masks, col, h, first), "firstHalf": stats_for(panel, masks, col, h, first, mid),
                                   "secondHalf": stats_for(panel, masks, col, h, mid)}
    return res


def to_markdown(result: dict) -> str:
    o = ["# 주도주 진입 규칙 백테스트", "", f"> 실행 {result['ranAt']} · 규칙 2026-10-10 고정(결과를 보고 바꾸지 않음) · 생존편향 있음(현재 유니버스) · 참고용이며 매수 신호가 아닙니다.", "",
         f"규칙 값: {json.dumps(CFG, ensure_ascii=False)}", "",
         "그룹: ALL 전 종목 기준선 · TREND 추세 통과 · V1 오늘의 추천 진입 조건(손절폭 8% 이하·추격 아님) · LEADER RS 90 이상(과열 허용) · PULLBACK 20일선 눌림 · BREAKOUT 피벗 돌파. "
         "HOLD=고정 보유, STOP8=종가 -8% 손절 포함. 평균%는 비용 차감, 'ALL 대비'는 날짜별 평균의 차이이고 95% 구간이 0을 포함하면 우연과 구분되지 않습니다. 비중축소% = 손절폭 8% 초과 신호를 8%/손절폭으로 줄인 신호당 수익.", ""]
    for m in result["markets"].values():
        o += [f"## {m['market'].upper()} — 종목 {m['stocks']}개, {m['firstDate']} ~ {m['lastDate']} (전·후반 분할 {m['splitDate']})", f"그룹 통과율(%): {m['passRate']}", ""]
        for col, per in m["results"].items():
            o += [f"### {col}", "| 기간 | 그룹 | 신호 | 평균% | 중앙값% | 승률% | 최악% | 평균 손절폭% | 비중축소% | ALL 대비 %p | 95% 구간 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
            for pname, label in (("all", "전체"), ("firstHalf", "전반"), ("secondHalf", "후반")):
                for g, s in per[pname].items():
                    o.append(f"| {label} | {g} | {s['signals']} | {s['meanPct']} | {s['medianPct']} | {s['winRatePct']} | {s['worstPct']} | {s['avgRiskPct']} | {s['sizedMeanPct']} | {s['vsAllPp']} | {s['vsAllCI95']} |")
            o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kr = json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8"))
    us = json.loads((ROOT / "docs" / "data" / "latest_us.json").read_text(encoding="utf-8"))
    kr_u = [r for r in kr if r.get("status") == "OK" and r.get("code")]
    us_u = [r for r in us if r.get("status") == "OK" and r.get("code")]
    result = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "config": CFG, "horizons": HORIZONS, "markets": {}}
    result["markets"]["us"] = run_market("us", us_u, "^GSPC")
    result["markets"]["kr"] = run_market("kr", kr_u, {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "backtest.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "backtest.md").write_text(to_markdown(result), encoding="utf-8")
    print(to_markdown(result))


if __name__ == "__main__":
    main()
