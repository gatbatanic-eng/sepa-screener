"""수급 매집 후보(FLOW, FLOW_ACC)의 전향 추적. (python -m accumulation.forward)
매일 마지막 확정 거래일 D의 후보를 research/accumulation/forward/YYYY-MM-DD.json에 한 번만 기록(수정 금지)하고,
기록된 모든 날의 5·20·40거래일 성과를 같은 시장 전 종목 평균(ALL)·지수와 비교해 research/accumulation/forward_summary.{json,md}에 쓴다.
진입은 D 다음 거래일 종가, 비용 왕복 0.5%(한국). 신호 30건 미만이면 '표본 부족'으로 표시하고 성과를 근거로 쓰지 않는다."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from nhplug.flowstore import FlowStore

from .backtest import HORIZONS, block_bootstrap_ci, fetch_ohlcv
from .features import compute
from .flowrule import FLOW_CFG, GROUPS, flow_condition, groups

log = logging.getLogger("accumulation.forward")
ROOT = Path(__file__).resolve().parent.parent
FWD_DIR = ROOT / "research" / "accumulation" / "forward"
COST = 0.0025 * 2
MIN_SIGNALS = 30
FROZEN_ON = "2026-10-06"


def universe(root: Path | None = None) -> dict[str, str]:
    rows = json.loads(((root or ROOT) / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8"))
    return {str(r["code"]).zfill(6): r.get("market") for r in rows if r.get("status") == "OK" and r.get("code")}


def symbol(code: str, market: str | None) -> str:
    return code + (".KQ" if market == "KOSDAQ" else ".KS")


def last_final_session(store: FlowStore, kst_today: str) -> str | None:
    s = [d for d in store.sessions() if d < kst_today]
    return s[-1] if s else None


def record_day(store: FlowStore, data: dict[str, pd.DataFrame], uni: dict[str, str], day: str, out_dir: Path | None = None, min_stocks: int = 300) -> dict | None:
    """day의 후보를 기록한다. 이미 있으면 None. 최근 20거래일 수급이 모두 있고 그날 가격 봉이 있는 종목만 평가한다.
    평가 가능한 종목이 유니버스의 절반 미만이면(가격 봉이 아직 안 왔거나 수집 실패) 기록하지 않고 사유를 돌려준다 — 한 번만 쓰는 기록이 빈 값으로 굳지 않게."""
    out_dir = out_dir or FWD_DIR
    path = out_dir / f"{day[:4]}-{day[4:6]}-{day[6:]}.json"
    if path.exists():
        return None
    sess = [d for d in store.sessions(min_stocks) if d <= day][-FLOW_CFG["days"]:]
    if len(sess) < FLOW_CFG["days"] or sess[-1] != day:
        return {"skipped": f"수급 이력 {len(sess)}일(20일 필요)"}
    ts = pd.Timestamp(day)
    picks: dict[str, list] = {g: [] for g in GROUPS}
    eligible, diag = 0, {"noFlow": 0, "noPrice": 0, "noBar": 0, "notOk": 0}
    for code, mk in uni.items():
        win, df = store.window(code, sess), data.get(symbol(code, mk))
        if win is None:
            diag["noFlow"] += 1
            continue
        if df is None:
            diag["noPrice"] += 1
            continue
        if ts not in df.index:
            diag["noBar"] += 1
            continue
        f = compute(df.loc[:ts]).iloc[-1]
        if not f["ok"]:
            diag["notOk"] += 1
            continue
        eligible += 1
        c_ok, ev = flow_condition(win)
        for g in groups(c_ok, bool(f["A"]), bool(f["B"]), bool(f["D"])):
            picks[g].append({"code": code, "market": mk, "close": float(df.loc[ts, "close"]), **ev})
    if eligible < 0.5 * len(uni):
        return {"skipped": f"평가 가능 종목 {eligible}/{len(uni)}개 — 기록 보류", "diag": diag}
    rec = {"schemaVersion": 1, "date": day, "frozenOn": FROZEN_ON, "recordedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "rule": FLOW_CFG,
           "eligible": eligible, "diag": diag, "groups": picks}
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return rec


def _fwd(df: pd.DataFrame, day: str, h: int) -> float | None:
    """D 다음 거래일 종가 진입 → h거래일 뒤 종가 청산(비용 차감). 이력이 모자라면 None."""
    idx = df.index
    pos = idx.searchsorted(pd.Timestamp(day), side="right")      # D 다음 첫 거래일 위치
    if pos + h >= len(idx) or pos >= len(idx):
        return None
    return float(df["close"].iloc[pos + h] / df["close"].iloc[pos] - 1 - COST)


def summarize(records: list[dict], data: dict[str, pd.DataFrame], uni: dict[str, str]) -> dict:
    out: dict = {"groups": {g: {} for g in GROUPS}, "recordedDays": len(records)}
    for h in HORIZONS:
        base_by_day: dict[str, float] = {}
        per_group: dict[str, list[tuple[str, float]]] = {g: [] for g in GROUPS}
        for rec in records:
            day = rec["date"]
            rets = [r for code, mk in uni.items() if (df := data.get(symbol(code, mk))) is not None and (r := _fwd(df, day, h)) is not None]
            if not rets:
                continue
            base_by_day[day] = float(np.mean(rets))
            for g in GROUPS:
                for p in rec["groups"].get(g, []):
                    df = data.get(symbol(p["code"], p.get("market")))
                    r = _fwd(df, day, h) if df is not None else None
                    if r is not None:
                        per_group[g].append((day, r))
        for g in GROUPS:
            sig = per_group[g]
            n = len(sig)
            daily = pd.Series([r for _, r in sig], index=[d for d, _ in sig]).groupby(level=0).mean() if sig else pd.Series(dtype=float)
            diff = np.array([daily[d] - base_by_day[d] for d in daily.index if d in base_by_day])
            lo, hi = block_bootstrap_ci(diff, h) if len(diff) else (float("nan"), float("nan"))
            out["groups"][g][str(h)] = {"signals": n, "days": int(len(daily)), "enough": n >= MIN_SIGNALS,
                                        "meanPct": round(float(np.mean([r for _, r in sig]) * 100), 2) if n else None,
                                        "winRatePct": round(float(np.mean([r > 0 for _, r in sig]) * 100), 1) if n else None,
                                        "allMeanPct": round(float(np.mean(list(base_by_day.values())) * 100), 2) if base_by_day else None,
                                        "vsAllPp": round(float(diff.mean() * 100), 2) if len(diff) else None,
                                        "vsAllCI95": [round(lo * 100, 2), round(hi * 100, 2)] if lo == lo else None}
    return out


def to_markdown(summary: dict, records: list[dict]) -> str:
    o = ["# 수급 매집 후보 전향 추적", "", f"> 규칙 고정 {FROZEN_ON} · 한국 · 참고용(매수 신호 아님) · 신호 {MIN_SIGNALS}건 미만이면 성과를 근거로 쓰지 않습니다.", "",
         f"기록된 날 {summary['recordedDays']}일 · 규칙 {json.dumps(FLOW_CFG, ensure_ascii=False)}", "",
         "| 최근 기록일 | 평가 가능 종목 | FLOW | FLOW_ACC |", "|---|---|---|---|"]
    for rec in records[-10:][::-1]:
        o.append(f"| {rec['date']} | {rec['eligible']} | {len(rec['groups'].get('FLOW', []))} | {len(rec['groups'].get('FLOW_ACC', []))} |")
    o += ["", "| 그룹 | 보유 | 신호 | 날짜 | 평균% | 승률% | ALL 평균% | ALL 대비 %p | 95% 구간 | 판정 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for g, per in summary["groups"].items():
        for h, s in per.items():
            o.append(f"| {g} | {h}일 | {s['signals']} | {s['days']} | {s['meanPct']} | {s['winRatePct']} | {s['allMeanPct']} | {s['vsAllPp']} | {s['vsAllCI95']} | {'' if s['enough'] else '표본 부족'} |")
    return "\n".join(o)


def load_records(out_dir: Path | None = None) -> list[dict]:
    recs = []
    for f in sorted((out_dir or FWD_DIR).glob("*.json")):
        try:
            recs.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return recs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kst_today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).strftime("%Y%m%d")
    store, uni = FlowStore(), universe()
    day = last_final_session(store, kst_today)
    data = fetch_ohlcv([symbol(c, m) for c, m in uni.items()], start="2025-06-01")
    last_bars = pd.Series([d.index[-1] for d in data.values()]).dt.strftime("%Y%m%d").value_counts().head(3).to_dict() if data else {}
    log.info("가격 확보 %d/%d종목, 기준일 %s, 마지막 봉 분포 %s", len(data), len(uni), day, last_bars)
    final = [d for d in store.sessions() if d < kst_today][-5:]       # 최근 5개 확정 거래일 중 아직 기록이 없는 날은 뒤늦게라도 기록한다(규칙은 그날까지의 데이터만 쓴다)
    for d in final:
        res = record_day(store, data, uni, d)
        log.info("기록 %s: %s", d, (res or {}).get("skipped") or ("이미 있음" if res is None else {g: len(v) for g, v in res["groups"].items()}), )
    records = load_records()
    summary = summarize(records, data, uni)
    out = ROOT / "research" / "accumulation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "forward_summary.json").write_text(json.dumps({"updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), **summary}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "forward_summary.md").write_text(to_markdown(summary, records), encoding="utf-8")
    print(to_markdown(summary, records))


if __name__ == "__main__":
    main()
