"""추천 종목의 사후 검증(전향적). 매일 추천을 한 번만 기록하고, 추천 다음 거래일 종가에 진입했다고 보고 5·20·40거래일 뒤 수익률을
지수와 같은 날 같은 풀에서 무작위로 뽑은 대조군에 견준다. 등급(A 통과 · B 조건부 · C 관찰)별로 따로 집계해 '검증 팀이 실제로 도움이 되는지'도 본다.

기록: research/advisory/picks/날짜.json (한 번 쓰면 수정 금지)
성과: research/advisory/picks_outcomes.json (끝난 값은 고정), 공개 통계: docs/advisory/data/picks_stats.json
가격은 ledger.prices(Yahoo 수정주가 종가)라 Actions에서만 돈다. 표본이 TRACK_MIN_N 미만이면 '표본 부족'이며 우열 판단에 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from pathlib import Path

from . import config
from .store import read_json, write_json

PICKS_DIR = config.ROOT / "research" / "advisory" / "picks"
OUTCOMES = config.ROOT / "research" / "advisory" / "picks_outcomes.json"
STATS = config.OUT_DIR / "picks_stats.json"
GROUPS = ("PICK", "A", "B", "C", "CONTROL")
GROUP_LABEL = {"PICK": "추천 전체", "A": "검증 통과(A)", "B": "조건부(B)", "C": "관찰 전용(C)", "CONTROL": "대조군(무작위)"}


def _exchange(market: str, row_market: str | None) -> str:
    if str(market).lower() == "us":
        return "US"
    return "KOSPI" if str(row_market or "").upper().startswith("KOSPI") else "KOSDAQ"


def _control(session: str, root: Path) -> list[dict]:
    pool = []
    for m in ("kr", "us"):
        for r in read_json(root / "docs" / "data" / f"latest_{m}.json", []) or []:
            if r.get("status") == "OK" and r.get("close"):
                code = str(r["code"]).zfill(6) if m == "kr" else str(r["code"])
                pool.append({"market": m, "code": code, "name": r.get("name"), "price": r["close"], "exchange": _exchange(m, r.get("market"))})
    pool.sort(key=lambda e: (e["market"], e["code"]))
    rng = random.Random(f"advisory-picks-control:{session}")
    return rng.sample(pool, min(config.TRACK_CONTROL_PER_DAY, len(pool))) if pool else []


def record(session: str, rec: dict, root: Path | None = None, picks_dir: Path | None = None) -> bool:
    """오늘 추천(+대조군)을 한 번만 기록한다. 이미 있으면 False."""
    root = root or config.ROOT
    path = (picks_dir or PICKS_DIR) / f"{session}.json"
    if path.exists():
        return False
    entries = []
    for p in rec.get("picks", []):
        m = "us" if str(p["market"]).upper() == "US" else "kr"
        entries.append({"group": "PICK", "grade": p["grade"], "market": m, "code": p["code"], "name": p["name"], "price": p.get("price"),
                        "exchange": _exchange(m, p["market"]), "composite": p.get("composite"), "weight": p.get("weight")})
    entries += [dict(e, group="CONTROL", grade=None) for e in _control(session, root)]
    write_json(path, {"schemaVersion": 1, "session": session, "headline": rec.get("headline"), "entries": entries})
    return True


def load_entries(picks_dir: Path | None = None) -> list[dict]:
    out = []
    for f in sorted((picks_dir or PICKS_DIR).glob("*.json")):
        d = read_json(f)
        for e in (d or {}).get("entries", []):
            out.append({**e, "session": d["session"], "id": f"{d['session']}:{e['market']}:{e['code']}:{e['group']}"})
    return out


def update(today: dt.date, fetch=None, bench_fetch=None, picks_dir: Path | None = None, outcomes_path: Path | None = None,
           stats_path: Path | None = None) -> dict:
    from ledger import outcomes as oc, prices
    fetch, bench_fetch = fetch or prices.fetch_closes, bench_fetch or prices.fetch_benchmarks
    entries = load_entries(picks_dir)
    path = outcomes_path or OUTCOMES
    stored = read_json(path, {"schemaVersion": 1, "outcomes": {}})
    pending = [e for e in entries if oc.needs_outcomes(stored["outcomes"].get(e["id"]), config.TRACK_HORIZONS)]
    if pending:
        start = (dt.date.fromisoformat(min(e["session"] for e in pending)) - dt.timedelta(days=10)).isoformat()
        bench = bench_fetch(start)
        for market in ("kr", "us"):
            batch = [e for e in pending if e["market"] == market]
            if not batch:
                continue
            symbols = sorted({e["code"] for e in batch})
            hint = {e["code"]: e["exchange"] for e in batch if market == "kr"}
            closes, exch = fetch(market, symbols, start, hint) if market == "kr" else fetch(market, symbols, start)
            for e in batch:
                b = bench.get(e["exchange"])
                sig = dict(e)
                if b is not None and len(b):
                    sessions = [s for s in b.index if s > e["session"]]       # 추천 다음 거래일 종가에 진입
                    sig["date"] = sessions[0] if sessions else e["session"]
                else:
                    sig["date"] = e["session"]
                fresh = oc.compute_one(sig, closes.get(e["code"]), b, config.TRACK_HORIZONS)
                stored["outcomes"][e["id"]] = oc.merge(stored["outcomes"].get(e["id"]), fresh)
        write_json(path, stored)
    stats = summarize(entries, stored["outcomes"])
    stats.update(generatedAt=dt.datetime.now(dt.timezone.utc).isoformat(), today=today.isoformat())
    write_json(stats_path or STATS, stats)
    return stats


def _mean(v):
    return round(sum(v) / len(v), 3) if v else None


def summarize(entries: list[dict], outcomes: dict) -> dict:
    groups: dict[str, dict] = {g: {} for g in GROUPS}
    seen: set = set()
    by_group: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for e in sorted(entries, key=lambda e: e["session"]):
        keys = ["CONTROL"] if e["group"] == "CONTROL" else ["PICK"] + ([e["grade"]] if e.get("grade") in ("A", "B", "C") else [])
        for g in keys:
            ident = (g, e["market"], e["code"])
            if ident in seen:        # (그룹·종목)당 최초 추천 1개만 센다 — 연속 추천은 보유 기간이 겹친다
                continue
            seen.add(ident)
            by_group[g].append(e)
    for g, es in by_group.items():
        for h in config.TRACK_HORIZONS:
            done = [outcomes.get(e["id"], {}).get(str(h)) for e in es]
            comp = [o for o in done if o and o.get("status") == "complete"]
            rets, exc = [o["returnPct"] for o in comp], [o["excessPct"] for o in comp if o.get("excessPct") is not None]
            groups[g][str(h)] = {"n": len(comp), "recorded": len(es), "meanReturn": _mean(rets), "meanExcess": _mean(exc),
                                 "winRate": round(100 * sum(x > 0 for x in exc) / len(exc), 1) if exc else None,
                                 "unavailable": sum(1 for o in done if o and o.get("status") == "unavailable"),
                                 "status": "OK" if len(comp) >= config.TRACK_MIN_N else "INSUFFICIENT_SAMPLE"}
    days = sorted({e["session"] for e in entries})
    return {"schemaVersion": 1, "horizons": list(config.TRACK_HORIZONS), "minN": config.TRACK_MIN_N, "labels": GROUP_LABEL,
            "recordedDays": len(days), "firstDay": days[0] if days else None, "lastDay": days[-1] if days else None, "groups": groups}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--today", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    s = update(dt.date.fromisoformat(p.parse_args().today))
    print("추천 기록", s["recordedDays"], "일, 그룹별 5거래일:", {g: s["groups"][g]["5"]["n"] for g in GROUPS})


if __name__ == "__main__":
    main()
