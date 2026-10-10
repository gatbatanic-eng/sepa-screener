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


# --- 자체 추적기를 가진 전략: research/*.json (신호 에피소드 + 5/20/60 사후 성과가 이미 계산돼 있다) --------------------
# 전략 → 파일 이름 접두. SEPA는 research/{kr,us}.json, 나머지는 research/{접두}_{kr,us}.json. 반등관찰은 한국만 있다.
TRACKERS = {"sepa": "", "range": "range_", "aggressive": "aggressive_", "rebound": "rebound_"}


def tracker_signals(strategy: str, market: str, research_dir: Path | None = None) -> list[dict]:
    doc = read_json((research_dir or config.ROOT / "research") / f"{TRACKERS[strategy]}{market}.json")
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
        out.append({"id": f"{strategy}:{market}:{s['id']}", "strategy": strategy, "market": market, "group": s["group"],
                    "date": s["date"], "symbol": s["code"], "name": s.get("name"), "price": s.get("originalClose"),
                    "exchange": s.get("benchmark") if market == "kr" else "US", "rank": None, "score": None,
                    "outcomes": outcomes})
    return out


def sepa_signals(market: str, research_dir: Path | None = None) -> list[dict]:
    return tracker_signals("sepa", market, research_dir)


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
        # 2026-10-12부터 스냅샷에 거래일(session)이 들어 있다. 그 전 기록은 기록 시각이 가리키는 거래일을 쓴다.
        doc = {"strategy": "funnel", "market": market, "fileDate": file_date, "recordedAt": snap["recordedAt"],
               "effectiveDate": snap.get("session") or effective_date(snap["recordedAt"], market).isoformat(), "rows": kept}
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
    read = 0
    # 멀티팩터 CSV는 utf-8-sig(BOM)로 저장된다. utf-8로 읽으면 첫 컬럼명이 달라져 모든 행이 조용히 걸러진다.
    with open(csv_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            read += 1
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
    if read and not any(by_market.values()):
        raise ValueError(f"멀티팩터 CSV {read}행을 읽었지만 유효한 신호가 없다: 컬럼명·가격 형식을 확인하세요")
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


# --- 기술적 신호: docs/technical/data/latest_{kr,us}.json (실행 때마다 덮어써지므로 신호별로 따로 남겨야 한다) ---------
LEDGER_STRATEGIES = ("funnel", "multifactor", "technical", "picks")  # 원장이 신호 파일을 직접 만들고 가격으로 성과를 계산하는 전략
TECH_VERDICT = {"매수검토": "REVIEW", "관찰": "WATCH", "진입보류": "HOLD"}


def technical_groups(row: dict) -> list[str]:
    groups = []
    for prefix, key in (("TREND", "trendVerdict"), ("REBOUND", "reboundVerdict")):
        code = TECH_VERDICT.get(str(row.get(key) or "").strip())
        if code:
            groups.append(f"{prefix}_{code}")
    return groups


def ingest_technical(market: str, latest_path: Path, recorded_at: str, signals_dir: Path | None = None) -> int:
    """기술적 신호 실행 직후 호출: 이번 판정을 그 거래일 기록으로 한 번 고정한다(같은 날 다시 불러도 덮어쓰지 않는다)."""
    doc = read_json(latest_path)
    rows = (doc or {}).get("rows") or []
    usable = [r for r in rows if r.get("status") == "OK" and r.get("code") and (r.get("close") or 0) > 0]
    if not usable:
        raise ValueError(f"기술적 신호 {market}: 사용할 수 있는 행이 없다({len(rows)}행) — 원장에 기록하지 않았습니다")
    eff = effective_date(recorded_at, market).isoformat()
    control = set(control_sample("technical", market, eff, [str(r["code"]) for r in usable]))
    kept = []
    for r in usable:
        code = str(r["code"])
        groups = technical_groups(r) + (["CONTROL"] if code in control else [])
        if groups:
            kept.append({"symbol": code, "name": r.get("name"), "price": float(r["close"]), "exchange": norm_exchange(market, r.get("market")),
                         "score": r.get("trendScore"), "groups": groups})
    out = {"strategy": "technical", "market": market, "fileDate": eff, "recordedAt": recorded_at, "effectiveDate": eff, "rows": kept}
    return 1 if write_immutable_gz((signals_dir or config.SIGNALS_DIR) / "technical" / market / f"{eff}.json.gz", out) else 0


# --- 오늘의 추천: research/daily_picks/{market}/{거래일}.json (이미 하루 한 번 고정된 기록) ----------------------------
def ingest_picks(market: str, picks_dir: Path | None = None, signals_dir: Path | None = None) -> int:
    """추천 기록 → 원장. PICK = 그날 추천 종목, CONTROL = 같은 날 후보 풀에서 추천을 뺀 무작위 표본(필터가 풀 평균보다 나은지 본다)."""
    src = (picks_dir or config.ROOT / "research" / "daily_picks") / market
    target = (signals_dir or config.SIGNALS_DIR) / "picks" / market
    added = 0
    for file in sorted(src.glob("*.json")):
        name = file.stem + ".json.gz"
        if (target / name).exists():
            continue
        rec = read_json(file)
        if not rec or not rec.get("session"):
            continue
        # PICK = v1(섹터 미반영), PICK_V2 = v2(섹터·시장 환경 반영), PICK_V3 = v3(손절폭 필터 없이 비중 조절), PICK_V4 = v4(미국 추세 통과 + 변동성 3% 이상). 같은 종목이 둘 다면 한 행에 그룹 두 개.
        by: dict[str, dict] = {}
        for key, group in (("picks", "PICK"), ("picksV2", "PICK_V2"), ("picksV3", "PICK_V3"), ("picksV4", "PICK_V4")):
            for i, p in enumerate(rec.get(key) or [], 1):
                if not p.get("price"):
                    continue
                row = by.setdefault(p["code"], {"symbol": p["code"], "name": p.get("name"), "price": p["price"], "exchange": norm_exchange(market, p.get("market")),
                                                "score": (p.get("v2") or {}).get("score", p.get("score")), "rank": i, "groups": []})
                row["groups"].append(group)
        rows = list(by.values())
        picked = set(by)
        rest = [c for c, px in (rec.get("poolPrices") or {}).items() if c not in picked and px]
        for sym in control_sample("picks", market, rec["session"], rest):
            rows.append({"symbol": sym, "name": None, "price": rec["poolPrices"][sym], "exchange": "US" if market == "us" else None,
                         "score": None, "rank": None, "groups": ["CONTROL"]})
        doc = {"strategy": "picks", "market": market, "fileDate": rec["session"], "recordedAt": rec["recordedAt"],
               "effectiveDate": rec["session"], "rows": rows}
        if rows and write_immutable_gz(target / name, doc):
            added += 1
    return added


def ledger_signals(strategy: str, market: str, signals_dir: Path | None = None) -> list[dict]:
    out: list[dict] = []
    for file in sorted(((signals_dir or config.SIGNALS_DIR) / strategy / market).glob("*.json.gz")):
        out.extend(expand_file(strategy, market, file.name.split(".")[0], read_gz(file)))
    return out


def all_signals(market: str) -> list[dict]:
    out = [s for strategy in TRACKERS for s in tracker_signals(strategy, market)]
    for strategy in LEDGER_STRATEGIES:
        out += ledger_signals(strategy, market)
    return out
