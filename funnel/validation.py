"""전향적 검증: 기록 시점의 후보 목록을 나중 가격과 비교한다.

스냅샷은 기록 당시 값 그대로 두고(사후 수정 금지), 경과일이 1·3·6·12개월을
처음 넘는 실행에서 그 시점 수익률을 한 번만 고정(frozen)한다.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path

CHECKPOINTS = (("1m", 30), ("3m", 91), ("6m", 182), ("12m", 365))


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values) * 100, 2) if values else None


def group_returns(rows: list[dict], prices: dict[str, float], top_k: int) -> dict:
    """상위 top_k(관문 통과·순위 기준) vs 관문 통과 전체 vs 관문 탈락 평균 수익률(%)."""
    groups: dict[str, list[float]] = {"top": [], "passed": [], "excluded": []}
    for r in rows:
        now, entry = prices.get(r["symbol"]), r.get("price")
        if not now or not entry:
            continue
        ret = now / entry - 1
        if r.get("excluded"):
            groups["excluded"].append(ret)
            continue
        groups["passed"].append(ret)
        if r.get("rank") and r["rank"] <= top_k:
            groups["top"].append(ret)
    return {k: {"avgReturnPct": _mean(v), "n": len(v)} for k, v in groups.items()}


def update(snapshot_dir: Path, path: Path, prices: dict[str, float], bench_now: float | None,
           today: dt.date, top_k: int) -> dict:
    state = json.loads(path.read_text()) if path.exists() else {"schemaVersion": 1, "snapshots": {}}
    for file in sorted(snapshot_dir.glob("*.json.gz")):
        snap = json.loads(gzip.open(file, "rt", encoding="utf-8").read())
        date = snap["recordedAt"][:10]
        age = (today - dt.date.fromisoformat(date)).days
        if age < 1:
            continue
        entry = state["snapshots"].setdefault(date, {"frozen": {}})
        current = group_returns(snap["rows"], prices, top_k)
        bench0 = snap.get("benchmark", {}).get("price")
        current["benchmarkReturnPct"] = round((bench_now / bench0 - 1) * 100, 2) if bench_now and bench0 else None
        entry.update({"ageDays": age, "current": current, "measuredAt": today.isoformat()})
        for label, days in CHECKPOINTS:
            if age >= days and label not in entry["frozen"]:
                entry["frozen"][label] = {**current, "ageDays": age, "measuredAt": today.isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return state
