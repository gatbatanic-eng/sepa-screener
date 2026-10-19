"""퀀트 계열 갱신: 재무·가격 → 월 리밸런싱 계좌(실전/사후 적용 × 4변형) → docs/research/quant.json. (python -m quant.main)"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

from agents import config as agent_config, freshness
from ledger import prices as ledger_prices

from . import config, inputs, stats
from .book import PriceBook
from .simulate import pool_periods, simulate
from .store import read_json, write_json, write_once

log = logging.getLogger("quant")
SERIES = {"live": config.INCEPTION_SIGNAL, "preview": config.PREVIEW_START_SIGNAL}


def _symbols_kr(universe, fin_kr, shares):
    out = {}
    for code, u in universe.items():
        f = fin_kr.get(code)
        if f and code in shares:
            out[code] = {"name": u["name"] or f.get("name", ""), "industry": f.get("industryCode"), "quarters": f["quarters"], "shares": shares[code]}
    return out


def _symbols_us(us_names, fin_us):
    out = {}
    for sym, name in us_names.items():
        f = fin_us.get(sym)
        if f:
            out[sym] = {"name": name or f.get("name", ""), "industry": f.get("industryCode"), "quarters": f["quarters"],
                        "sharesSeries": f.get("shares") or []}
    return out


def _summary(sim: dict, ref_all: dict, ref_ctl: dict | None, capital: float) -> dict:
    vals = [v for _, v in sim["curve"]]
    ex_all = stats.excess(sim["periods"], ref_all)
    out = {"totalReturnPct": round((vals[-1] / capital - 1) * 100, 2) if vals else None, "mddPct": stats.max_drawdown(vals) if vals else None,
           "periods": len(sim["periods"]), "trades": len(sim["trades"]), "cash": sim["cash"],
           "excessVsAll": stats.boot_mean_ci(ex_all) if ex_all else None, "excessVsAllN": len(ex_all)}
    if ref_ctl is not None:
        ex_ctl = stats.excess(sim["periods"], ref_ctl)
        out["excessVsControl"] = stats.boot_mean_ci(ex_ctl) if ex_ctl else None
        out["excessVsControlN"] = len(ex_ctl)
    out["sampleSufficient"] = len(sim["periods"]) >= config.MIN_PERIODS
    return out


def _ranking(df, variant_w, top=40):
    if df.empty:
        return []
    comp = variant_w[0] * df["value"] + variant_w[1] * df["momentum"] + variant_w[2] * df["quality"]
    t = df.assign(composite=comp).sort_values(["composite", "symbol"], ascending=[False, True]).head(top)
    return [{"rank": i + 1, "symbol": r.symbol, "name": r.name, "industry": r.industry, "composite": round(r.composite, 2),
             "value": round(r.value, 1), "momentum": round(r.momentum, 1), "quality": round(r.quality, 1)} for i, r in enumerate(t.itertuples())]


def run(today: dt.date, *, fetch=ledger_prices.fetch_closes, bench_fetch=ledger_prices.fetch_benchmarks, asof: dict | None = None,
        extra_path: Path | None = None, us_names: dict | None = None, fin_dir: Path | None = None, selections_dir: Path | None = None,
        reviews_path: Path | None = None, write: bool = True, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    asof = asof or freshness.basis(today)
    fin_dir, selections_dir = fin_dir or config.FIN_DIR, selections_dir or config.SELECTIONS_DIR
    reviews_path = reviews_path or config.REVIEWS_PATH
    extra_path = extra_path or (config.ROOT / "research" / "nhplug" / "kr_extra.json")
    if us_names is None:
        raw = read_json(config.UNIVERSE_US_PATH, {}) or {}
        us_names = {r["symbol"]: r.get("name", "") for r in raw.get("symbols", [])}
    fin_kr, fin_us = inputs.load_fin("kr", fin_dir), inputs.load_fin("us", fin_dir)
    kr_u = {c: u for c, u in inputs.kr_universe(extra_path).items() if c in fin_kr}
    us_syms = [s for s in us_names if s in fin_us]
    start = (dt.date.fromisoformat(config.PREVIEW_START_SIGNAL) - dt.timedelta(days=430)).isoformat()
    closes = {}
    if kr_u:
        got, _ = fetch("kr", sorted(kr_u), start, None)
        closes.update({("kr", c): s[s.index <= asof["kr"]] for c, s in got.items()})
    if us_syms:
        got, _ = fetch("us", sorted(us_syms), start)
        closes.update({("us", c): s[s.index <= asof["us"]] for c, s in got.items()})
    books = {m: PriceBook({c: s for (mm, c), s in closes.items() if mm == m}) for m in ("kr", "us")}
    merged = PriceBook({f"{m}:{c}": s for (m, c), s in closes.items()})
    # 시장별 심볼 공간이 다르므로(코드/티커) 계좌는 'kr:005930' 형태로 합친 한 권을 쓴다. 패널은 시장별 책으로 만든다.
    symbols = {"kr": _symbols_kr(kr_u, fin_kr, inputs.kr_shares(kr_u, books["kr"])), "us": _symbols_us(us_names, fin_us)}
    events_by_series = {}
    for name, first in SERIES.items():
        evs = []
        for m in ("kr", "us"):
            for e in inputs.build_events(m, symbols[m], books[m], asof[m], first):
                e = dict(e)
                e["df"] = e["df"].assign(symbol=[f"{m}:{s}" for s in e["df"]["symbol"]]) if len(e["df"]) else e["df"]
                e["industry"] = {f"{m}:{s}": v for s, v in e["industry"].items()}
                evs.append(e)
        events_by_series[name] = evs
    calendar = merged.calendar(merged.symbols(), "9999-12-31")
    reviews = read_json(reviews_path, []) or []
    retired_on = next((r["date"] for r in reversed(reviews) if r.get("variant") == "multifactor" and r.get("to") == "RETIRED"), None)
    result = {"schemaVersion": 1, "generatedAt": now.isoformat(), "asof": asof, "rulesFrozenOn": config.RULES_FROZEN_ON,
              "inceptionSignal": config.INCEPTION_SIGNAL, "previewStartSignal": config.PREVIEW_START_SIGNAL,
              "initialCapital": config.INITIAL_CAPITAL, "minPeriods": config.MIN_PERIODS, "series": {}, "labels": config.LABELS}
    drift, live_decisions = [], []
    for name, evs in events_by_series.items():
        sims = {v: simulate(evs, merged, calendar, variant=v, freeze_after=retired_on if (name == "live" and v == "multifactor") else None)
                for v in config.VARIANTS} if evs else {}
        ref_all = pool_periods(evs, merged) if evs else {}
        ref_ctl = {p["start"][:7]: p["returnPct"] for p in sims["control"]["periods"]} if sims else {}
        out_v = {}
        for v, sim in sims.items():
            s = _summary(sim, ref_all, None if v == "control" else ref_ctl, config.INITIAL_CAPITAL)
            out_v[v] = {"label": config.LABELS[v], "summary": s, "curve": sim["curve"], "periods": sim["periods"],
                        "holdings": sim["holdings"], "trades": sim["trades"][-60:]}
        series = {"firstSignal": SERIES[name], "events": [{"market": e["market"], "signal": e["signal"], "exec": e["exec"], "skipped": e["skipped"],
                                                         "eligible": len(e["df"]), "universe": e["universe"], "diag": e["diag"]} for e in evs],
                  "variants": out_v, "all": {"periodReturnsPct": {k: round(v, 3) for k, v in sorted(ref_all.items())}}}
        if name == "live" and sims:
            live_decisions = sims["multifactor"]["decisions"]
            mf = out_v["multifactor"]["summary"]
            stage_prev = next((r["to"] for r in reversed(reviews) if r.get("variant") == "multifactor"), "ACTIVE")
            stage, why = stats.review_stage(stage_prev, mf["mddPct"] or 0.0, mf["periods"],
                                            stats.excess(sims["multifactor"]["periods"], ref_ctl))
            if why and write:
                reviews.append({"date": asof["kr"], "variant": "multifactor", "from": stage_prev, "to": stage, "reason": why})
                write_json(reviews_path, reviews, indent=2)
            series["stage"] = {"current": stage, "since": next((r["date"] for r in reversed(reviews) if r.get("variant") == "multifactor"), None)}
        result["series"][name] = series
    # 선정 기록(실전, 멀티팩터): 신호일마다 한 번만 쓴다. 이미 있으면 다시 계산한 결과와 비교만 한다.
    live_events = {(e["market"], e["signal"]): e for e in events_by_series["live"]}
    for d in live_decisions:
        e = live_events[(d["market"], d["signal"])]
        path = selections_dir / f"{d['signal']}-{d['market']}.json"
        rec = {"schemaVersion": 1, "market": d["market"], "signal": d["signal"], "exec": d["exec"], "recordedAt": now.isoformat(),
               "universe": e["universe"], "eligible": len(e["df"]), "diag": e["diag"],
               "ranking": _ranking(e["df"], config.WEIGHTS["multifactor"]),
               "targets": d["targets"], "kept": d["kept"], "entries": d["entries"], "exits": d["exits"], "industrySkipped": d["industrySkipped"]}
        if write and not write_once(path, rec):
            old = read_json(path, {})
            if old.get("targets") != rec["targets"]:
                drift.append({"signal": d["signal"], "market": d["market"], "recorded": old.get("targets"), "recomputed": rec["targets"]})
    # 현재 순위(참고, 기록 아님): 시장별 마지막 거래일 기준
    current = {}
    for m in ("kr", "us"):
        cal = books[m].calendar(symbols[m].keys(), asof[m])
        if cal:
            panel = inputs.build_panel(m, cal[-1], symbols[m], books[m])
            current[m] = {"signal": cal[-1], "eligible": len(panel["rows"]), "universe": panel["universe"], "diag": panel["diag"],
                          "ranking": _ranking(panel["df"], config.WEIGHTS["multifactor"])}
    bench = bench_fetch(start) if (kr_u or us_syms) else {}
    result["current"] = current
    result["dataHealth"] = {"finKr": len(fin_kr), "finUs": len(fin_us), "universeKr": len(kr_u), "universeUs": len(us_syms),
                            "drift": drift, "benchmarks": {k: (round(float(v.iloc[-1]), 2) if len(v) else None) for k, v in bench.items()}}
    if write:
        write_json(config.PUBLIC_JSON, result)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="파일을 쓰지 않는다")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    today = agent_config.session_date()
    res = run(today, write=not args.dry)
    live = res["series"].get("live", {}).get("variants", {}).get("multifactor", {}).get("summary")
    log.info("퀀트 갱신 %s — 실전 멀티팩터: %s", today, live)


if __name__ == "__main__":
    main()
