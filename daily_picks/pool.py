"""후보 풀: 시장별로 각 전략이 지금 고른 종목과, 종목별 진입 자료(손절폭·판정·추격·유동성)를 모은다. 읽기 전용."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from . import config as C

ROOT = Path(__file__).resolve().parents[1]
CORE = {"sepa": "SEPA", "funnel": "실적 턴어라운드"}
RESEARCH = {"multifactor": "멀티팩터", "technical": "기술적", "range": "RANGE-MR", "aggressive": "계좌복구", "rebound": "반등관찰"}
TRACKER_FILES = {"sepa": "", "range": "range_", "aggressive": "aggressive_", "rebound": "rebound_"}


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tracker_members(strategy: str, market: str, root: Path = ROOT, groups: tuple[str, ...] | None = None) -> dict[str, list[str]]:
    """추적기 membership에서 '지금(최신 계열) 선정된' 종목 → 그룹 목록."""
    doc = _json(root / "research" / f"{TRACKER_FILES[strategy]}{market}.json")
    if not doc or not doc.get("days"):
        return {}
    last = max(doc["days"].values(), key=lambda d: (d.get("date") or "", d.get("recordedAt") or ""))
    sid = last.get("strategySeriesId")
    out: dict[str, list[str]] = {}
    for key, on in (doc.get("membership") or {}).items():
        if not on:
            continue
        series, code, group = key.split(":", 2)
        if series != sid or (groups is not None and group not in groups):
            continue
        out.setdefault(code, []).append(group)
    return out


def latest_multifactor(market: str, root: Path = ROOT) -> dict[str, dict]:
    files = sorted((root / "research" / "ledger" / "signals" / "multifactor" / market).glob("*.json.gz"))
    if not files:
        return {}
    with gzip.open(files[-1], "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    return {r["symbol"]: r for r in doc.get("rows", []) if set(r.get("groups") or []) & {"BUY", "WATCH"}}


def load(market: str, root: Path = ROOT) -> dict:
    """{'sepaRows', 'funnel', 'techRows', 'aggrRows', 'selected': {code: {strategy: detail}}, 'regime', 'asOf'}"""
    sepa_rows = {r["code"]: r for r in (_json(root / "docs" / "data" / f"latest_{market}.json") or [])}
    funnel_doc = _json(root / "docs" / "research" / f"funnel_{market}.json") or {}
    funnel = {t["symbol"]: t for t in funnel_doc.get("top", [])}
    tech_doc = _json(root / "docs" / "technical" / "data" / f"latest_{market}.json") or {}
    tech = {r["code"]: r for r in tech_doc.get("rows", []) if r.get("status") == "OK"}
    aggr_doc = _json(root / "research" / f"aggressive_{market}.json") or {}
    aggr = {r["code"]: r for r in aggr_doc.get("latestRows", [])}

    selected: dict[str, dict] = {}

    def add(code: str, strategy: str, detail):
        selected.setdefault(code, {})[strategy] = detail

    for code, groups in tracker_members("sepa", market, root, C.SEPA_GROUPS).items():
        add(code, "sepa", groups)
    for code, t in funnel.items():
        add(code, "funnel", t.get("rank"))
    for code, r in latest_multifactor(market, root).items():
        add(code, "multifactor", r.get("groups"))
    for code, r in tech.items():
        verdicts = [v for v in (r.get("trendVerdict"), r.get("reboundVerdict")) if v in ("매수검토", "관찰")]
        if verdicts:
            add(code, "technical", verdicts)
    for strat in ("range", "aggressive", "rebound"):
        for code, groups in tracker_members(strat, market, root).items():
            add(code, strat, groups)
    regime_row = next(iter(sepa_rows.values()), {})
    return {"sepaRows": sepa_rows, "funnel": funnel, "techRows": tech, "aggrRows": aggr, "selected": selected,
            "regime": regime_row.get("regime"), "gate": regime_row.get("marketGate"),
            "asOf": funnel_doc.get("session") or (aggr_doc.get("latestSession")), "funnelRecordedAt": funnel_doc.get("recordedAt")}
