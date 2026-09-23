"""Cached, network-free six-lens reviews for the static recovery dashboard.

Public rule evidence is prepared locally for the screened universe. Only up to
5 automatic + 3 locally marked holdings + 2 manual choices are active in the UI.
No portfolio data is sent to the build or committed to the repository.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

from personas import build_evidence, normalize_code
from personas import track_record, valuation

VERSION = 1
LENSES = (
    ("trend", "추세·진입", "단기 · 현재 셋업"),
    ("growth", "성장·실적", "다음 분기 · 중기"),
    ("value", "가치·재무건전성", "중장기 · 가격과 재무"),
    ("quant", "퀀트·통계", "5·20·60거래일 · 표본 확인"),
    ("risk", "계좌 리스크", "진입 전 · 보유 중"),
    ("contrarian", "반론·검증", "핵심 가정이 바뀔 때"),
)


def load(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_changed(path, data):
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
    return True


def stock_id(market, code):
    market = str(market).lower()
    if market not in ("kr", "us") or code is None:
        return None
    code = normalize_code(market, code).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,19}", code):
        return None
    return f"{market}-{code}"


def six_lenses(evidence):
    """Merge overlapping lenses; preserve data limitations, never vote on a trade."""
    by_id = {p["id"]: copy.deepcopy(p) for p in evidence["personas"]}
    technical = by_id.get("technical", {})
    for key in ("supports", "concerns", "checks", "dataGaps"):
        by_id["trend"][key].extend(technical.get(key, []))
    cards = []
    for pid, name, horizon in LENSES:
        p = by_id[pid]
        # An assumed 1% account budget is not a measurement of this user's account.
        if pid == "risk":
            p["checks"] = [f for f in p["checks"] if f["id"] != "risk_sizing"]
            p["dataGaps"].append("보유 수량·비중·계좌 잔액 미연결: 계좌 손실액·집중도는 계산하지 않음")
        if pid == "value":
            p["dataGaps"].append("업종·자체 과거 밸류에이션 비교 미연결: 절대 PER만으로 저평가 판단 불가")
        p["concerns"].sort(key=lambda f: -f.get("severity", 1))
        gaps = list(dict.fromkeys(p["dataGaps"]))
        state = "주의" if p["concerns"] else "근거 확인" if p["supports"] else "판단 유보"
        if gaps and state == "근거 확인":
            state = "자료 제한"
        cards.append({
            "id": pid, "name": name, "horizon": horizon, "state": state,
            "supports": p["supports"], "concerns": p["concerns"],
            "checks": p["checks"], "dataGaps": gaps,
        })
    return cards


def build_review(row, fund, context, yv, *, as_of, today, recovery=None):
    evidence = build_evidence(row, fund, today=today, context=context, valuation=yv)
    cards = six_lenses(evidence)
    return {
        "schemaVersion": VERSION, "code": evidence["code"], "name": evidence["name"],
        "market": str(row["market"]).upper(), "asOf": as_of,
        "source": "기존 스크리너·공시 재무·저장된 Yahoo 지표·성과 기록 / 규칙 기반",
        "fundamentalPeriod": (evidence["snapshot"].get("fundamentals") or {}).get("latestPeriod"),
        "valuationAsOf": (yv or {}).get("fetchedAt"),
        "valuationStale": bool((yv or {}).get("stale")),
        "cards": cards, "recovery": recovery,
        "limits": ["관점별 검토 기간이 다르며 찬반을 합산하지 않습니다.",
                   "복구 모드 진입·손절·금액 규칙은 코멘트로 변경되지 않습니다.",
                   "규칙 엔진의 SEPA 구조적 손절과 복구 모드 참고 손절은 서로 다른 기준입니다."],
    }


def generate_reviews(snapshot, root, *, today=None):
    started = time.perf_counter()
    today = today or datetime.now(timezone.utc).date()
    out = root / "docs" / "recovery" / "personas"
    automatic = []
    recovery_rows = {}
    for r in snapshot["entries"] + snapshot["strongest"]:
        sid = stock_id(r.get("marketBucket"), r.get("code"))
        if sid and sid not in automatic and len(automatic) < 5:
            automatic.append(sid)
        if sid and sid not in recovery_rows:
            recovery_rows[sid] = {
                "entryPass": r.get("entryPass") is True, "reason": r.get("entryReason"),
                "asOf": snapshot["sessions"].get(str(r.get("marketBucket")).lower()),
                "executionPriority": r.get("executionPriority"),
                "executionPlan": r.get("executionPlan"),
                "suggestedAmountKRW": r.get("suggestedAmountKRW"),
            }
    catalog, changed, gaps = [], 0, []
    for market in ("kr", "us"):
        rows = load(root / "docs" / "data" / f"latest_{market}.json", [])
        history = load(root / "docs" / "data" / f"history_{market}.json", [])
        as_of = history[-1].get("date") if history else None
        ctx = track_record.context_for(market, root)
        yv_cache = valuation.load_cache(root / "docs" / "data" / "valuation_us.json") if market == "us" else None
        for row in rows:
            sid = stock_id(market, row.get("code"))
            if not sid or row.get("status") != "OK":
                continue
            row = {**row, "market": market.upper(), "code": normalize_code(market, row["code"])}
            fund = load(root / "docs" / "data" / "fundamentals" / market / f"{row['code']}.json", None)
            yv = valuation.get(yv_cache, row["code"]) if yv_cache else None
            review = build_review(row, fund, ctx, yv, as_of=as_of, today=today,
                                  recovery=recovery_rows.get(sid))
            # A content hash makes browser caches safe across new market snapshots.
            digest = hashlib.sha256(json.dumps(review, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            changed += write_changed(out / f"{sid}.json", review)
            catalog.append({"id": sid, "code": row["code"], "name": row.get("name") or row["code"],
                            "market": market.upper(), "asOf": as_of, "revision": digest})
    available = {r["id"] for r in catalog}
    for sid in automatic:
        if sid not in available:
            gaps.append(f"{sid}: 스크리너 원본이 없어 분석 대기")
    catalog.sort(key=lambda r: (r["market"], r["code"]))
    result = {"schemaVersion": VERSION, "automatic": [s for s in automatic if s in available],
              "limits": {"automatic": 5, "holdings": 3, "manual": 2},
              "sessions": snapshot["sessions"], "stocks": catalog, "dataGaps": gaps,
              "mode": "rules", "llmCalls": 0}
    write_changed(out / "catalog.json", result)
    # Only delete obsolete public review files from this exact output directory.
    for path in out.glob("*.json"):
        if path.name != "catalog.json" and path.stem not in available:
            path.unlink()
    for name in ("review.html", "review.js", "review.css"):
        source = (Path(__file__).parent / name).read_text(encoding="utf-8")
        (out / ("index.html" if name == "review.html" else name)).write_text(source, encoding="utf-8")
    return {"stocks": len(catalog), "changed": changed, "llmCalls": 0,
            "seconds": round(time.perf_counter() - started, 3)}
