"""수급 매집 후보의 전향 추적. (python -m accumulation.forward)
v1(FLOW, FLOW_ACC, 2026-10-06 고정)과 v2(V2, 2026-10-07 고정)를 나란히 기록한다.
매일 확정 거래일 D의 후보를 research/accumulation/forward{,_v2}/YYYY-MM-DD.json에 한 번만 기록(수정 금지)하고,
5·20·40거래일 성과를 같은 날 평가 가능한 전 종목 평균(ALL)과 비교해 research/accumulation/forward_summary.{json,md}에 쓴다.
측정(2026-10-07 보완): 같은 종목은 직전 10개 기록일 안에 이미 신호가 있었으면 다시 세지 않는다(연속 출현을 한 사례로).
비교군 C_ONLY(수급만)·D_ONLY(위치만)를 같은 날 같은 방식으로 계산해, 수급과 위치 중 무엇이 성과를 만드는지 가른다.
진입은 D 다음 거래일 종가, 비용 왕복 0.5%(한국). 사례 30건 미만이면 '표본 부족'."""
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
from .flowrule import FLOW_CFG, GROUPS, V2_CFG, V2_GROUPS, flow_condition, groups, v2_condition

log = logging.getLogger("accumulation.forward")
ROOT = Path(__file__).resolve().parent.parent
FWD_DIR = ROOT / "research" / "accumulation" / "forward"
FWD_V2_DIR = ROOT / "research" / "accumulation" / "forward_v2"
COST = 0.0025 * 2
MIN_SIGNALS = 30
COOLDOWN = 10
FROZEN_ON = "2026-10-06"
V2_FROZEN_ON = "2026-10-07"
BASE_GROUPS = ("C_ONLY", "D_ONLY")
GROUP_LABEL = {"FLOW": "v1", "FLOW_ACC": "v1", "C_ONLY": "비교군", "D_ONLY": "비교군", "V2": "v2", "V2_FRG": "v2", "V2_INST": "v2"}


def universe(root: Path | None = None) -> dict[str, str]:
    rows = json.loads(((root or ROOT) / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8"))
    return {str(r["code"]).zfill(6): r.get("market") for r in rows if r.get("status") == "OK" and r.get("code")}


def symbol(code: str, market: str | None) -> str:
    return code + (".KQ" if market == "KOSDAQ" else ".KS")


def last_final_session(store: FlowStore, kst_today: str) -> str | None:
    s = [d for d in store.sessions() if d < kst_today]
    return s[-1] if s else None


def features_for(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """종목별 지표를 한 번만 계산한다(compute는 미래 값을 쓰지 않으므로 날짜별로 잘라 계산한 것과 같다)."""
    return {s: compute(df) for s, df in data.items()}


def _evaluate(store, data, feats, uni, day, min_stocks):
    """day의 종목별 판정을 돌려준다: (rows, diag, sess). rows = [(code, mk, close, f(Series), win)]."""
    sess = [d for d in store.sessions(min_stocks) if d <= day][-FLOW_CFG["days"]:]
    if len(sess) < FLOW_CFG["days"] or sess[-1] != day:
        return None, {"skipped": f"수급 이력 {len(sess)}일(20일 필요)"}, sess
    ts = pd.Timestamp(day)
    rows, diag = [], {"noFlow": 0, "noPrice": 0, "noBar": 0, "notOk": 0}
    for code, mk in uni.items():
        sym = symbol(code, mk)
        win, df = store.window(code, sess), data.get(sym)
        if win is None:
            diag["noFlow"] += 1
            continue
        if df is None:
            diag["noPrice"] += 1
            continue
        if ts not in df.index:
            diag["noBar"] += 1
            continue
        f = (feats[sym] if feats and sym in feats else compute(df.loc[:ts])).loc[ts]
        if not f["ok"]:
            diag["notOk"] += 1
            continue
        rows.append((code, mk, float(df.loc[ts, "close"]), f, win))
    return rows, diag, sess


def record_day(store: FlowStore, data: dict[str, pd.DataFrame], uni: dict[str, str], day: str, out_dir: Path | None = None,
               min_stocks: int = 300, series: str = "v1", feats: dict | None = None) -> dict | None:
    """day의 후보를 기록한다(series='v1' 또는 'v2'). 이미 있으면 None.
    평가 가능한 종목이 유니버스의 절반 미만이면(가격 봉·프로그램 값이 아직 없는 경우 등) 기록하지 않고 사유를 돌려준다."""
    out_dir = out_dir or (FWD_V2_DIR if series == "v2" else FWD_DIR)
    path = out_dir / f"{day[:4]}-{day[4:6]}-{day[6:]}.json"
    if path.exists():
        return None
    rows, diag, _ = _evaluate(store, data, feats, uni, day, min_stocks)
    if rows is None:
        return diag
    picks: dict[str, list] = {g: [] for g in (V2_GROUPS[:1] if series == "v2" else GROUPS)}
    eligible = 0
    if series == "v2":
        diag["noProgram"] = 0
    for code, mk, close, f, win in rows:
        if series == "v2":
            hr = f.get("hiRatio")
            ok, ev = v2_condition(win, float(hr) if hr == hr else None)
            if ok is None:
                diag["noProgram"] += 1
                continue
            eligible += 1
            if ok:
                picks["V2"].append({"code": code, "market": mk, "close": close, **ev})
        else:
            eligible += 1
            c_ok, ev = flow_condition(win)
            for g in groups(c_ok, bool(f["A"]), bool(f["B"]), bool(f["D"])):
                picks[g].append({"code": code, "market": mk, "close": close, **ev})
    if eligible < 0.5 * len(uni):
        return {"skipped": f"평가 가능 종목 {eligible}/{len(uni)}개 — 기록 보류", "diag": diag}
    rec = {"schemaVersion": 1, "series": series, "date": day, "frozenOn": V2_FROZEN_ON if series == "v2" else FROZEN_ON,
           "recordedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "rule": V2_CFG if series == "v2" else FLOW_CFG,
           "eligible": eligible, "diag": diag, "groups": picks}
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return rec


def baselines(store: FlowStore, data, feats, uni, days: list[str], min_stocks: int = 300) -> dict[str, dict[str, list[str]]]:
    """비교군: {날짜: {'C_ONLY': [코드], 'D_ONLY': [코드]}}. 기록하지 않고 매번 같은 규칙으로 계산한다(그날까지의 데이터만 사용)."""
    out = {}
    for day in days:
        rows, _, _ = _evaluate(store, data, feats, uni, day, min_stocks)
        if rows is None:
            continue
        out[day] = {"C_ONLY": [c for c, _, _, _, w in rows if flow_condition(w)[0]], "D_ONLY": [c for c, _, _, f, _ in rows if bool(f["D"])]}
    return out


def _fwd(df: pd.DataFrame, day: str, h: int) -> float | None:
    """D 다음 거래일 종가 진입 → h거래일 뒤 종가 청산(비용 차감). 이력이 모자라면 None."""
    idx = df.index
    pos = idx.searchsorted(pd.Timestamp(day), side="right")      # D 다음 첫 거래일 위치
    if pos + h >= len(idx) or pos >= len(idx):
        return None
    return float(df["close"].iloc[pos + h] / df["close"].iloc[pos] - 1 - COST)


def dedupe(events: dict[str, list[str]], cooldown: int = COOLDOWN) -> list[tuple[str, str]]:
    """{날짜: [코드]} → 같은 종목이 직전 cooldown개 기록일 안에 이미 나왔으면 뺀 (날짜, 코드) 목록. 연속 출현은 첫날 하나로 센다."""
    days = sorted(events)
    last_seen: dict[str, int] = {}
    out = []
    for i, d in enumerate(days):
        for code in events[d]:
            j = last_seen.get(code)
            if j is None or i - j > cooldown:
                out.append((d, code))
            last_seen[code] = i
    return out


def summarize(events_by_group: dict[str, dict[str, list[str]]], data: dict[str, pd.DataFrame], uni: dict[str, str]) -> dict:
    """events_by_group: {그룹: {날짜: [코드]}}. 그룹마다 중복을 뺀 사례의 성과를 같은 날 ALL 평균과 비교."""
    all_days = sorted({d for ev in events_by_group.values() for d in ev})
    out: dict = {"groups": {}, "recordedDays": len(all_days), "cooldown": COOLDOWN}
    for h in HORIZONS:
        base: dict[str, float] = {}
        for d in all_days:
            rets = [r for code, mk in uni.items() if (df := data.get(symbol(code, mk))) is not None and (r := _fwd(df, d, h)) is not None]
            if rets:
                base[d] = float(np.mean(rets))
        for g, ev in events_by_group.items():
            sig = []
            for d, code in dedupe(ev):
                df = data.get(symbol(code, uni.get(code)))
                r = _fwd(df, d, h) if df is not None and d in base else None
                if r is not None:
                    sig.append((d, r))
            n = len(sig)
            daily = pd.Series([r for _, r in sig], index=[d for d, _ in sig]).groupby(level=0).mean() if sig else pd.Series(dtype=float)
            diff = np.array([daily[d] - base[d] for d in daily.index])
            lo, hi = block_bootstrap_ci(diff, h) if len(diff) else (float("nan"), float("nan"))
            out["groups"].setdefault(g, {})[str(h)] = {
                "series": GROUP_LABEL.get(g, ""), "signals": n, "days": int(len(daily)), "enough": n >= MIN_SIGNALS,
                "meanPct": round(float(np.mean([r for _, r in sig]) * 100), 2) if n else None,
                "winRatePct": round(float(np.mean([r > 0 for _, r in sig]) * 100), 1) if n else None,
                "allMeanPct": round(float(np.mean([base[d] for d in daily.index]) * 100), 2) if len(daily) else None,
                "vsAllPp": round(float(diff.mean() * 100), 2) if len(diff) else None,
                "vsAllCI95": [round(lo * 100, 2), round(hi * 100, 2)] if lo == lo else None}
    return out


def events_from(records: list[dict], group_names) -> dict[str, dict[str, list[str]]]:
    return {g: {r["date"]: [p["code"] for p in r["groups"].get(g, [])] for r in records} for g in group_names}


def v2_events(records: list[dict]) -> dict[str, dict[str, list[str]]]:
    ev = {"V2": {}, "V2_FRG": {}, "V2_INST": {}}
    for r in records:
        ps = r["groups"].get("V2", [])
        ev["V2"][r["date"]] = [p["code"] for p in ps]
        ev["V2_FRG"][r["date"]] = [p["code"] for p in ps if p.get("lead") == "FRG"]
        ev["V2_INST"][r["date"]] = [p["code"] for p in ps if p.get("lead") == "INST"]
    return ev


def to_markdown(summary: dict, records: list[dict], records_v2: list[dict]) -> str:
    o = ["# 수급 매집 후보 전향 추적", "",
         f"> v1 고정 {FROZEN_ON} · v2 고정 {V2_FROZEN_ON} · 한국 · 참고용(매수 신호 아님) · 같은 종목은 {COOLDOWN}개 기록일 안에 다시 세지 않음 · 사례 {MIN_SIGNALS}건 미만이면 '표본 부족'.", "",
         f"v1 규칙 {json.dumps(FLOW_CFG, ensure_ascii=False)} · v2 규칙 {json.dumps(V2_CFG, ensure_ascii=False)}", "",
         "| 기록일 | v1 평가 종목 | FLOW | FLOW_ACC | v2 평가 종목 | V2 |", "|---|---|---|---|---|---|"]
    v2 = {r["date"]: r for r in records_v2}
    for d in sorted({r["date"] for r in records} | set(v2), reverse=True)[:10]:
        r = next((x for x in records if x["date"] == d), None)
        q = v2.get(d)
        o.append(f"| {d} | {r['eligible'] if r else '–'} | {len(r['groups'].get('FLOW', [])) if r else '–'} | {len(r['groups'].get('FLOW_ACC', [])) if r else '–'} | "
                 f"{q['eligible'] if q else '–'} | {len(q['groups'].get('V2', [])) if q else '–'} |")
    o += ["", "| 계열 | 그룹 | 보유 | 사례 | 날짜 | 평균% | 승률% | ALL 평균% | ALL 대비 %p | 95% 구간 | 판정 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for g, per in summary["groups"].items():
        for h, s in per.items():
            o.append(f"| {s['series']} | {g} | {h}일 | {s['signals']} | {s['days']} | {s['meanPct']} | {s['winRatePct']} | {s['allMeanPct']} | {s['vsAllPp']} | {s['vsAllCI95']} | {'' if s['enough'] else '표본 부족'} |")
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
    data = fetch_ohlcv([symbol(c, m) for c, m in uni.items()], start="2025-06-01")
    feats = features_for(data)
    last_bars = pd.Series([d.index[-1] for d in data.values()]).dt.strftime("%Y%m%d").value_counts().head(3).to_dict() if data else {}
    log.info("가격 확보 %d/%d종목, 기준일 %s, 마지막 봉 분포 %s", len(data), len(uni), last_final_session(store, kst_today), last_bars)
    final = [d for d in store.sessions() if d < kst_today][-5:]       # 최근 5개 확정 거래일 중 아직 기록이 없는 날은 뒤늦게라도 기록한다(규칙은 그날까지의 데이터만 쓴다)
    for series in ("v1", "v2"):
        for d in final:
            res = record_day(store, data, uni, d, series=series, feats=feats)
            log.info("%s 기록 %s: %s", series, d, (res or {}).get("skipped") or ("이미 있음" if res is None else {g: len(v) for g, v in res["groups"].items()}))
    records, records_v2 = load_records(FWD_DIR), load_records(FWD_V2_DIR)
    events = {**events_from(records, GROUPS), **v2_events(records_v2)}
    base = baselines(store, data, feats, uni, [r["date"] for r in records])
    events.update({g: {d: v[g] for d, v in base.items()} for g in BASE_GROUPS})
    summary = summarize(events, data, uni)
    out = ROOT / "research" / "accumulation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "forward_summary.json").write_text(json.dumps({"updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), **summary}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "forward_summary.md").write_text(to_markdown(summary, records, records_v2), encoding="utf-8")
    print(to_markdown(summary, records, records_v2))


if __name__ == "__main__":
    main()
