"""각 탭의 기록 → 원장 신호. SEPA는 기존 추적기를 그대로 읽고, 깔때기·멀티팩터는 원장 파일을 만든다."""
from __future__ import annotations

import csv
import datetime as dt
import logging
from pathlib import Path

from . import config
from .signals import control_sample, effective_date, expand_file
from .store import read_gz, write_immutable_gz, read_json

log = logging.getLogger("ledger")


# --- SEPA: research/{kr,us}.json (신호 에피소드 + 5/20/60 사후 성과가 이미 계산돼 있다) ----------------------------
def sepa_signals(market: str, research_dir: Path | None = None) -> list[dict]:
    doc = read_json((research_dir or config.ROOT / "research") / f"{market}.json")
    if not doc:
        return []
    out = []
    for s in doc.get("signals", []):
        outcomes = {}
        for h, o in (s.get("outcomes") or {}).items():
            status = o.get("status")
            outcomes[int(h)] = ({"status": "complete", "returnPct": o["returnPct"], "benchmarkPct": o.get("benchmarkPct"),
                                 "excessPct": o.get("excessPct"), "maxDownPct": o.get("maxDownPct")}
                                if status == "complete" and o.get("returnPct") is not None
                                else {"status": "pending" if status == "pending" else "unavailable",
                                      "reason": o.get("reason") or status})
        out.append({"id": f"sepa:{market}:{s['id']}", "strategy": "sepa", "market": market, "group": s["group"],
                    "date": s["date"], "symbol": s["code"], "name": s.get("name"), "price": s.get("originalClose"),
                    "exchange": s.get("benchmark") if market == "kr" else "US", "rank": None, "score": None,
                    "outcomes": outcomes})
    return out


# --- 깔때기: research/funnel/{market}/snapshots/*.json.gz (전 종목 순위·가격·T1 포함) ----------------------------
def norm_exchange(market: str, label: str | None) -> str | None:
    if market == "us":
        return "US"
    label = (label or "").upper()
    return "KOSPI" if label.startswith("KOSPI") else "KOSDAQ" if label.startswith(("KOSDAQ", "KONEX")) else None


def funnel_groups(row: dict) -> list[str]:
    groups = []
    if not row.get("excluded"):
        if row.get("rank") and row["rank"] <= config.TOP_K:
            groups.append("TOP50")
        if row.get("T1") == "ON":
            groups.append("T1_ON")
    return groups


def ingest_funnel(market: str, today: dt.date, funnel_dir: Path | None = None, signals_dir: Path | None = None) -> int:
    """하루가 끝난(오늘 이전) 스냅샷만 원장으로 옮긴다. 같은 날 여러 번 갱신돼도 마지막 값 하나를 한 번만 고정한다."""
    snap_dir = (funnel_dir or config.ROOT / "research" / "funnel") / market / "snapshots"
    target = (signals_dir or config.SIGNALS_DIR) / "funnel" / market
    added = 0
    for file in sorted(snap_dir.glob("*.json.gz")):
        file_date = file.name.split(".")[0]
        if dt.date.fromisoformat(file_date) >= today or (target / file.name).exists():
            continue
        snap = read_gz(file)
        rows = [r for r in snap["rows"] if r.get("price")]
        control = set(control_sample("funnel", market, file_date, [r["symbol"] for r in rows]))
        kept = []
        for r in rows:
            groups = funnel_groups(r) + (["CONTROL"] if r["symbol"] in control else [])
            if groups:
                kept.append({"symbol": r["symbol"], "price": r["price"], "rank": r.get("rank"), "score": r.get("composite"),
                             "exchange": norm_exchange(market, r.get("exchange")), "groups": groups})
        doc = {"strategy": "funnel", "market": market, "fileDate": file_date, "recordedAt": snap["recordedAt"],
               "effectiveDate": effective_date(snap["recordedAt"], market).isoformat(), "rows": kept}
        if write_immutable_gz(target / file.name, doc):
            added += 1
    return added


# --- 멀티팩터: output/screening_result.csv (실행 때마다 덮어써지므로 신호별로 따로 남겨야 한다) -----------------
def _market_of(label: str) -> tuple[str, str]:
    label = (label or "").upper()
    if label.startswith("KOSPI"):
        return "kr", "KOSPI"
    if label.startswith("KOSDAQ") or label == "KONEX":
        return "kr", "KOSDAQ"
    return "us", "US"


def ingest_multifactor(csv_path: Path, recorded_at: str, signals_dir: Path | None = None) -> int:
    target = (signals_dir or config.SIGNALS_DIR) / "multifactor"
    by_market: dict[str, list[dict]] = {"kr": [], "us": []}
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            market, exchange = _market_of(r.get("market", ""))
            try:
                price = float(r["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if not r.get("symbol") or price <= 0 or not r.get("signal"):
                continue
            by_market[market].append({"symbol": r["symbol"].strip(), "name": r.get("name"), "price": price, "exchange": exchange,
                                      "score": float(r["composite_score"]) if r.get("composite_score") not in (None, "") else None,
                                      "signal": r["signal"].strip().upper()})
    added = 0
    for market, rows in by_market.items():
        if not rows:
            continue
        eff = effective_date(recorded_at, market).isoformat()
        control = set(control_sample("multifactor", market, eff, [r["symbol"] for r in rows]))
        kept = [{"symbol": r["symbol"], "name": r["name"], "price": r["price"], "exchange": r["exchange"], "score": r["score"],
                 "groups": [r["signal"]] + (["CONTROL"] if r["symbol"] in control else [])} for r in rows]
        doc = {"strategy": "multifactor", "market": market, "fileDate": eff, "recordedAt": recorded_at, "effectiveDate": eff, "rows": kept}
        if write_immutable_gz(target / market / f"{eff}.json.gz", doc):
            added += 1
    return added


def ledger_signals(strategy: str, market: str, signals_dir: Path | None = None) -> list[dict]:
    out: list[dict] = []
    for file in sorted(((signals_dir or config.SIGNALS_DIR) / strategy / market).glob("*.json.gz")):
        out.extend(expand_file(strategy, market, file.name.split(".")[0], read_gz(file)))
    return out


def all_signals(market: str) -> list[dict]:
    return sepa_signals(market) + ledger_signals("funnel", market) + ledger_signals("multifactor", market)
