"""에이전트 팀 갱신: SEPA 신호 보관 → 가격 조회 → 에이전트별 가상 계좌 시뮬레이션 → docs/research/agents.json. (python -m agents.main)"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

from ledger import prices

from . import config, signals as sg, stats
from .roster import ROSTER
from .simulate import simulate
from .store import write_json

log = logging.getLogger("agents")
SERIES = {"live": config.INCEPTION, "preview": config.PREVIEW_START}


def run(today: dt.date, fetch=prices.fetch_closes, bench_fetch=prices.fetch_benchmarks) -> dict:
    sigs = {m: sg.archive(m) for m in ("kr", "us")}
    start = (dt.date.fromisoformat(config.PREVIEW_START) - dt.timedelta(days=10)).isoformat()
    closes: dict[tuple[str, str], object] = {}
    for m, rows in sigs.items():
        symbols = sorted({s["code"] for s in rows})
        if not symbols:
            continue
        hint = {s["code"]: s["exchange"] for s in rows if m == "kr" and s.get("exchange") in ("KOSPI", "KOSDAQ")}
        got, _ = fetch(m, symbols, start, hint) if m == "kr" else fetch(m, symbols, start)
        log.info("%s: 가격 확보 %d/%d종목", m, len(got), len(symbols))
        closes.update({(m, c): s for c, s in got.items()})
    bench_all = bench_fetch(start)
    bench = {k: v for k, v in bench_all.items() if k in ("KOSPI", "US")}
    all_sigs = [s for rows in sigs.values() for s in rows]
    out = {"schemaVersion": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "today": today.isoformat(),
           "rules": {"frozenOn": config.RULES_FROZEN_ON, "inception": config.INCEPTION, "previewStart": config.PREVIEW_START,
                     "capital": config.INITIAL_CAPITAL, "maxPositions": config.MAX_POSITIONS, "holdSessions": config.HOLD_SESSIONS,
                     "stopPct": config.STOP_PCT, "costBps": config.COST_BPS, "minClosed": config.MIN_CLOSED},
           "agents": {}}
    for aid, a in ROSTER.items():
        entry = {"label": a["label"], "lens": a["lens"], "series": {}}
        for name, begin in SERIES.items():
            res = simulate(a["select"], all_sigs, closes, begin)
            entry["series"][name] = {"summary": stats.summarize(res, bench), "curve": res["curve"], "trades": res["trades"],
                                     "open": res["open"], "skipped": res["skipped"]}
        out["agents"][aid] = entry
    write_json(config.PUBLIC_JSON, out)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--today", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    res = run(dt.date.fromisoformat(p.parse_args().today))
    for aid, a in res["agents"].items():
        for name, s in a["series"].items():
            log.info("%s/%s: %s", aid, name, s["summary"])


if __name__ == "__main__":
    main()
