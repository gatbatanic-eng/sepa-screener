"""원장 갱신: 새 신호 수집 → 사후 성과 계산 → 통계 → docs/research/ledger.json. (python -m ledger.main)"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

from . import adapters, config, outcomes as oc, prices, stats
from .store import read_json, write_json

log = logging.getLogger("ledger")
STRATEGIES = ("funnel", "multifactor")  # 가격으로 직접 계산하는 전략 (SEPA는 자체 추적기 결과를 쓴다)


def update_market(market: str, today: dt.date, bench: dict, fetch=prices.fetch_closes) -> tuple[list[dict], dict]:
    signals = adapters.all_signals(market)
    stored: dict = {}
    todo: dict[str, list[dict]] = {}
    for strategy in STRATEGIES:
        path = config.OUTCOMES_DIR / f"{strategy}_{market}.json"
        stored[strategy] = read_json(path, {"schemaVersion": 1, "outcomes": {}})
        todo[strategy] = [s for s in signals if s["strategy"] == strategy
                          and oc.needs_outcomes(stored[strategy]["outcomes"].get(s["id"]))]
    pending = [s for rows in todo.values() for s in rows]
    all_out: dict = {s["id"]: s["outcomes"] for s in signals if "outcomes" in s}
    if pending:
        symbols = sorted({s["symbol"] for s in pending})
        start = (dt.date.fromisoformat(min(s["date"] for s in pending)) - dt.timedelta(days=10)).isoformat()
        hint = {s["symbol"]: s["exchange"] for s in pending if s.get("exchange") and s["exchange"] != "US"}
        closes, exch = fetch(market, symbols, start, hint) if market == "kr" else fetch(market, symbols, start)
        log.info("%s: 계산 대상 신호 %d개, 가격 확보 %d/%d종목", market, len(pending), len(closes), len(symbols))
        for strategy, rows in todo.items():
            for s in rows:
                # 거래소를 못 찾은 종목은 달력만 코스피로 맞추고(가격이 없으면 '조회 불가'로 센다)
                name = "US" if market == "us" else exch.get(s["symbol"]) or s.get("exchange") or "KOSPI"
                fresh = oc.compute_one(s, closes.get(s["symbol"]), bench.get(name))
                stored[strategy]["outcomes"][s["id"]] = oc.merge(stored[strategy]["outcomes"].get(s["id"]), fresh)
    for strategy in STRATEGIES:
        write_json(config.OUTCOMES_DIR / f"{strategy}_{market}.json", stored[strategy])
        all_out.update(stored[strategy]["outcomes"])
    return signals, all_out


def run(today: dt.date, fetch=prices.fetch_closes, bench_fetch=prices.fetch_benchmarks) -> dict:
    for market in ("kr", "us"):
        adapters.ingest_funnel(market, today)
    start = (today - dt.timedelta(days=400)).isoformat()
    bench = bench_fetch(start)
    result = {"schemaVersion": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "today": today.isoformat(),
              "rules": {"frozenOn": config.RULES_FROZEN_ON, "horizons": list(config.HORIZONS), "minN": config.MIN_N,
                        "minWindows": config.MIN_WINDOWS, "bootstrap": config.BOOT_N, "controlPerDate": config.CONTROL_PER_DATE,
                        "benchmarks": config.BENCHMARK_LABEL, "topK": config.TOP_K},
              "strategies": {}}
    for market in ("kr", "us"):
        signals, outs = update_market(market, today, bench, fetch)
        for strategy, markets in stats.build(signals, outs).items():
            result["strategies"].setdefault(strategy, {}).update(markets)
    write_json(config.PUBLIC_JSON, result)
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--today", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    args = p.parse_args()
    result = run(dt.date.fromisoformat(args.today))
    for strategy, markets in result["strategies"].items():
        for market, groups in markets.items():
            log.info("%s/%s: 그룹 %s", strategy, market, ", ".join(sorted(groups)))


if __name__ == "__main__":
    main()
