"""에이전트 팀 갱신: SEPA 신호 보관 → 가격 조회 → 에이전트별 가상 계좌 시뮬레이션 → docs/research/agents.json. (python -m agents.main)"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

from ledger import prices

from . import config, review as rv, signals as sg, stats
from .roster import ROSTER
from .simulate import simulate
from .store import read_json, write_json

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
    reviews_path = config.STATE_DIR / "reviews.json"
    reviews = read_json(reviews_path, {"schemaVersion": 1, "agents": {}})
    raw: dict[str, dict[str, dict]] = {}
    for aid, a in ROSTER.items():
        retired = (reviews["agents"].get(aid) or {})
        end = retired["since"] if retired.get("status") == "RETIRED" else None  # 소멸한 계좌는 소멸일에 멈춘다
        raw[aid] = {name: simulate(a["select"], all_sigs, closes, begin, end if name == "live" else None)
                    for name, begin in SERIES.items()}
    today_s = today.isoformat()
    control = raw["control"]["live"]["trades"]
    for aid, a in ROSTER.items():
        entry = {"label": a["label"], "lens": a["lens"], "series": {}}
        for name, res in raw[aid].items():
            entry["series"][name] = {"summary": stats.summarize(res, bench), "curve": res["curve"], "trades": res["trades"],
                                     "open": res["open"], "skipped": res["skipped"]}
        if aid == "control":
            entry["review"] = {"status": "BASELINE", "capitalWeight": None}
        else:
            live = entry["series"]["live"]["summary"]
            state, evidence = rv.review(reviews["agents"].get(aid), live if live.get("status") != "NO_DATA" else {},
                                        raw[aid]["live"]["trades"], control, today_s)
            reviews["agents"][aid] = state
            entry["review"] = {"status": state["status"], "since": state["since"], "history": state["history"],
                               "capitalWeight": config.CAPITAL_WEIGHT[state["status"]], "evidence": evidence}
        out["agents"][aid] = entry
    out["rules"]["review"] = {"frozenOn": config.REVIEW_FROZEN_ON, "mddProbation": config.MDD_PROBATION, "mddRetire": config.MDD_RETIRE,
                              "minClosedReview": config.MIN_CLOSED_REVIEW, "minClosedRetire": config.MIN_CLOSED_RETIRE,
                              "probationRecheck": config.PROBATION_RECHECK, "probationMaxExtra": config.PROBATION_MAX_EXTRA,
                              "weights": config.CAPITAL_WEIGHT}
    write_json(reviews_path, reviews)
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
