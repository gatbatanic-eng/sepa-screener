"""계좌복구 공격매매 모드 v2.

research/aggressive_{kr,us}.json 의 공격형 모멘텀 결과를 바탕으로
매일 강한 5개를 랭킹하고, 위험 게이트를 통과한 후보만 300~400만원
규모의 참고 포지션으로 제시한다. 실제 주문은 하지 않는다.

추가 방어장치:
- 시장 국면 + breadth + 권장 진입비중
- 갭/급등 추격 금지
- 최근 실적 모멘텀
- 미국 Yahoo forward EPS 추정치의 누적 변화(정식 리비전 피드 아님)
- 실적발표 임박 차단(데이터가 있을 때만)
- 동일 섹터/산업군 신규진입 중복 제한
- AGGR_GO 실제 5거래일 사후성과에 따른 금액 상한
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT / "research"
OUT = ROOT / "docs" / "recovery"
DATA = OUT / "data"
FUNDAMENTALS = ROOT / "docs" / "data" / "fundamentals"
VALUATION_US = ROOT / "docs" / "data" / "valuation_us.json"

TOP_STRONG = 5
TOP_ENTRY = 3
MAX_NEW_ENTRIES_PER_DAY = 2

MIN_ENTRY_SCORE = 78.0
STRICT_RISK_PCT = 5.5
ENTRY_MIN_KRW = 3_000_000
ENTRY_MID_KRW = 3_500_000
ENTRY_MAX_KRW = 4_000_000

GAP_HARD_MAX_PCT = 6.0
GAP_CHASE_PCT = 3.0
PIVOT_CHASE_PCT = 3.0
DAY_CHASE_PCT = 8.0

BREADTH_HARD_MIN = 0.30
BREADTH_CAUTION = 0.45

EVENT_BLOCK_DAYS = 2
EVENT_CAUTION_DAYS = 5

ESTIMATE_MIN_SAMPLES = 10
ESTIMATE_BLOCK_PCT = -5.0


def _num(v: Any, default=None):
    try:
        if isinstance(v, bool):
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _clip(x: float, lo=0.0, hi=1.0) -> float:
    return max(lo, min(hi, x))


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError, TypeError):
        return default


def technical_strength_score(row: dict) -> float:
    """0~100. 돌파 직후 강도는 높이고, 피벗에서 과하게 멀어진 추격은 낮춘다."""
    slope = _clip((_num(row.get("sma50Slope10Pct"), 0.0) or 0.0) / 8.0) * 15
    high = _clip(((_num(row.get("highProximityPct"), -10.0) or -10.0) + 10.0) / 10.0) * 10

    pd = _num(row.get("pivotDistancePct"), 99.0)
    if -3 <= pd <= 3:
        pivot = 15.0
    elif 3 < pd <= 5:
        pivot = 15.0 * (1 - (pd - 3) / 2)
    elif -6 <= pd < -3:
        pivot = 10.0 * (1 - abs(pd + 3) / 3)
    else:
        pivot = 0.0

    vr = _num(row.get("volumeRatio"), 0.0) or 0.0
    volume = _clip((vr - 1.0) / 1.5) * 15
    clv = _clip(((_num(row.get("clv"), 0.5) or 0.5) - 0.5) / 0.5) * 10

    rsi = _num(row.get("rsi14"), 50.0) or 50.0
    rsi_score = max(0.0, 1.0 - abs(rsi - 65.0) / 15.0) * 10

    signals = _clip((_num(row.get("signalCount"), 0.0) or 0.0) / 6.0) * 15
    risk = _num(row.get("initialRiskPct"), 99.0) or 99.0
    risk_score = _clip((7.0 - risk) / 7.0) * 10

    return round(slope + high + pivot + volume + clv + rsi_score + signals + risk_score, 1)


strength_score = technical_strength_score


def rankable(row: dict) -> bool:
    return bool(
        row.get("status") == "OK"
        and row.get("inUniverse") is True
        and row.get("marketOk") is True
        and row.get("trendOkAggressive") is True
        and row.get("nearHigh") is True
        and row.get("rsOk") is True
        and row.get("liquidityOk") is True
    )


def _pct(quarter: dict, key: str):
    v = quarter.get(key)
    return _num(v.get("pct")) if isinstance(v, dict) else None


def fundamental_metrics(market: str, code: str) -> dict:
    data = _load_json(FUNDAMENTALS / market / f"{code}.json", {})
    qs = data.get("quarters") or []
    if not qs:
        return {"available": False, "score": None, "risk": "UNKNOWN", "industryCode": None}

    last = qs[-1]
    prev = qs[-2] if len(qs) >= 2 else {}
    rev_yoy = _pct(last, "revenueYoY")
    eps_yoy = _pct(last, "epsYoY")
    op_yoy = _pct(last, "operatingProfitYoY")
    ni_yoy = _pct(last, "netIncomeYoY")
    profit_yoy = eps_yoy if eps_yoy is not None else op_yoy if op_yoy is not None else ni_yoy

    labels = []
    for key in ("epsYoY", "operatingProfitYoY", "netIncomeYoY"):
        v = last.get(key)
        if isinstance(v, dict) and v.get("label"):
            labels.append(str(v["label"]))
    profit_label = " / ".join(labels) if labels else None

    margin = _num(last.get("operatingMargin"))
    prev_margin = _num(prev.get("operatingMargin"))
    margin_chg = margin - prev_margin if margin is not None and prev_margin is not None else None

    pieces = []
    if rev_yoy is not None:
        pieces.append((_clip((rev_yoy + 10) / 50) * 100, 0.30))
    if profit_label and "흑자전환" in profit_label:
        pieces.append((100.0, 0.50))
    elif profit_yoy is not None:
        pieces.append((_clip((profit_yoy + 20) / 120) * 100, 0.50))
    if margin_chg is not None:
        pieces.append((_clip((margin_chg + 3) / 8) * 100, 0.20))

    score = None
    if pieces:
        total_w = sum(w for _, w in pieces)
        score = round(sum(v * w for v, w in pieces) / total_w, 1)

    risk = "OK"
    if rev_yoy is not None and rev_yoy < -10 and margin_chg is not None and margin_chg < -3:
        risk = "BLOCK"
    elif (rev_yoy is not None and rev_yoy < 0) or (margin_chg is not None and margin_chg < -2):
        risk = "WARN"

    return {
        "available": True,
        "score": score,
        "latestPeriod": last.get("period"),
        "industryCode": data.get("industryCode"),
        "revenueYoY": rev_yoy,
        "profitYoY": profit_yoy,
        "profitLabel": profit_label,
        "operatingMargin": margin,
        "marginChangePp": round(margin_chg, 2) if margin_chg is not None else None,
        "risk": risk,
    }


def load_valuation_us() -> dict:
    return (_load_json(VALUATION_US, {}).get("symbols") or {})


def estimate_history() -> list[dict]:
    return _load_json(DATA / "estimate_history.json", [])


def estimate_metrics(code: str, current_forward_eps, history: list[dict]) -> dict:
    current = _num(current_forward_eps)
    if current is None:
        return {"available": False, "samples": 0, "trend": "UNKNOWN", "deltaPct": None}

    past = []
    for rec in history:
        v = _num((rec.get("symbols") or {}).get(code))
        if v is not None:
            past.append(v)
    if not past:
        return {"available": True, "samples": 0, "trend": "NEW", "deltaPct": None, "forwardEps": current}

    base = past[max(0, len(past) - 20)]
    delta = None if base == 0 else (current / base - 1) * 100
    trend = "UNKNOWN" if delta is None else "UP" if delta >= 3 else "DOWN" if delta <= -3 else "FLAT"
    return {
        "available": True,
        "samples": len(past),
        "trend": trend,
        "deltaPct": round(delta, 2) if delta is not None else None,
        "forwardEps": current,
        "source": "Yahoo forwardEps 누적 변화(정식 컨센서스 리비전 피드 아님)",
    }


def update_estimate_history(session_key: str, valuation: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "estimate_history.json"
    hist = estimate_history()
    symbols = {}
    for code, row in valuation.items():
        val = _num(row.get("forwardEps"))
        if val is not None and not row.get("stale"):
            symbols[code] = val
    hist = [h for h in hist if h.get("sessionKey") != session_key]
    hist.append({
        "sessionKey": session_key,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
    })
    path.write_text(
        json.dumps(hist[-60:], ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def _parse_event_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(v[:10])
            except ValueError:
                return None
    return None


def event_metrics(row: dict, valuation: dict, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    if row.get("marketBucket") != "US":
        return {"status": "UNKNOWN", "days": None, "date": None}

    v = valuation.get(str(row.get("code"))) or {}
    candidates = [
        _parse_event_date(v.get("earningsTimestampStart")),
        _parse_event_date(v.get("earningsTimestamp")),
        _parse_event_date(v.get("earningsTimestampEnd")),
    ]
    candidates = [d for d in candidates if d is not None and d >= today]
    if not candidates:
        return {"status": "UNKNOWN", "days": None, "date": None}

    d = min(candidates)
    days = (d - today).days
    status = "BLOCK" if days <= EVENT_BLOCK_DAYS else "WARN" if days <= EVENT_CAUTION_DAYS else "OK"
    return {"status": status, "days": days, "date": d.isoformat()}


def market_metrics(row: dict) -> dict:
    regime = row.get("regime") or "UNKNOWN"
    breadth = _num(row.get("breadth"))
    size_factor = _num(row.get("sizeFactor"), 1.0)
    cap = 1.0

    if regime == "RED":
        cap = 0.0
    elif regime in ("YELLOW", "RECOVERY"):
        cap = min(cap, 0.75)

    if breadth is not None:
        if breadth < BREADTH_HARD_MIN:
            cap = 0.0
        elif breadth < BREADTH_CAUTION:
            cap = min(cap, 0.75)
        elif breadth < 0.55:
            cap = min(cap, 0.90)

    if size_factor is not None:
        cap = min(cap, max(0.0, min(1.0, size_factor)))

    return {
        "regime": regime,
        "breadth": breadth,
        "sizeFactor": size_factor,
        "exposureCap": round(cap, 2),
        "blocked": cap <= 0,
    }


def gap_chase_metrics(row: dict) -> dict:
    gap = _num(row.get("gapPct"))
    day = _num(row.get("changePct1d"))
    pivot = _num(row.get("pivotDistancePct"), 99.0)
    hard = gap is not None and gap >= GAP_HARD_MAX_PCT
    chase = hard or bool(
        (gap is not None and gap >= GAP_CHASE_PCT and pivot >= PIVOT_CHASE_PCT)
        or (day is not None and day >= DAY_CHASE_PCT and pivot >= PIVOT_CHASE_PCT)
    )
    return {"gapPct": gap, "changePct1d": day, "chase": chase, "hard": hard}


def enrich_row(row: dict, market: str, valuation: dict, estimates: list[dict]) -> dict:
    r = dict(row)
    r["marketBucket"] = market.upper()
    r["technicalScore"] = technical_strength_score(r)

    fm = fundamental_metrics(market, str(r.get("code")))
    r["fundamental"] = fm

    if market == "us":
        val = valuation.get(str(r.get("code"))) or {}
        r["sector"] = val.get("sector")
        r["industry"] = val.get("industry")
        r["forwardEps"] = _num(val.get("forwardEps"))
        r["estimate"] = estimate_metrics(str(r.get("code")), r["forwardEps"], estimates)
        r["sectorKey"] = r["sector"]
    else:
        industry_code = fm.get("industryCode")
        r["sector"] = None
        r["industry"] = industry_code
        r["estimate"] = {"available": False, "samples": 0, "trend": "UNKNOWN", "deltaPct": None}
        r["sectorKey"] = f"KSIC:{str(industry_code)[:2]}" if industry_code else None

    r["marketRisk"] = market_metrics(r)
    r["gapRisk"] = gap_chase_metrics(r)
    r["eventRisk"] = event_metrics(r, valuation)

    weighted = [(r["technicalScore"], 0.85)]
    if fm.get("score") is not None:
        weighted.append((fm["score"], 0.15))
    denom = sum(w for _, w in weighted)
    r["baseScore"] = round(sum(v * w for v, w in weighted) / denom, 1)
    return r


def apply_sector_strength(rows: list[dict]) -> None:
    raw = [r["technicalScore"] for r in rows]
    overall = statistics.median(raw) if raw else 50.0
    groups: dict[str, list[float]] = {}
    for r in rows:
        if r.get("sectorKey"):
            groups.setdefault(r["sectorKey"], []).append(r["technicalScore"])

    for r in rows:
        vals = groups.get(r.get("sectorKey") or "", [])
        adjustment = 0.0
        if len(vals) >= 2:
            proxy = round(max(0.0, min(100.0, 50 + (statistics.median(vals) - overall) * 4)), 1)
            r["sectorCohortScore"] = proxy
            r["sectorCohortN"] = len(vals)
            adjustment = max(-3.0, min(3.0, (proxy - 50) * 0.06))
        else:
            r["sectorCohortScore"] = None
            r["sectorCohortN"] = len(vals)

        est = r.get("estimate") or {}
        est_adj = 0.0
        if est.get("samples", 0) >= ESTIMATE_MIN_SAMPLES:
            est_adj = 2.0 if est.get("trend") == "UP" else -3.0 if est.get("trend") == "DOWN" else 0.0
        r["strengthScore"] = round(max(0.0, min(100.0, r["baseScore"] + adjustment + est_adj)), 1)


def estimate_blocked(row: dict) -> bool:
    e = row.get("estimate") or {}
    delta = _num(e.get("deltaPct"))
    return bool(e.get("samples", 0) >= ESTIMATE_MIN_SAMPLES and delta is not None and delta <= ESTIMATE_BLOCK_PCT)


def entry_ok(row: dict, score: float) -> bool:
    fm = row.get("fundamental") or {}
    mr = row.get("marketRisk") or market_metrics(row)
    gap = row.get("gapRisk") or gap_chase_metrics(row)
    event = row.get("eventRisk") or {"status": "UNKNOWN"}
    return bool(
        rankable(row)
        and row.get("aggressiveGo") is True
        and row.get("breakout") is True
        and row.get("volumeOk") is True
        and row.get("clvOk") is True
        and row.get("rsiOk") is True
        and row.get("notExtended") is True
        and (_num(row.get("initialRiskPct"), 99.0) or 99.0) <= STRICT_RISK_PCT
        and score >= MIN_ENTRY_SCORE
        and not mr.get("blocked")
        and not gap.get("chase")
        and event.get("status") != "BLOCK"
        and fm.get("risk") != "BLOCK"
        and not estimate_blocked(row)
    )


def track_record_stats(states: list[dict]) -> dict:
    obs = []
    for state in states:
        for signal in state.get("signals") or []:
            if signal.get("group") != "AGGR_GO":
                continue
            out = (signal.get("outcomes") or {}).get("5") or {}
            ret = _num(out.get("returnPct"))
            if out.get("status") == "complete" and ret is not None:
                obs.append({
                    "date": signal.get("date") or "",
                    "returnPct": ret,
                    "excessPct": _num(out.get("excessPct"), 0.0) or 0.0,
                    "maxDownPct": _num(out.get("maxDownPct")),
                })

    obs.sort(key=lambda x: x["date"])
    n = len(obs)
    if n == 0:
        return {
            "samples5d": 0, "winRate5d": None, "avgReturn5d": None, "avgExcess5d": None,
            "approxMdd5d": None, "avgMaxDown5d": None,
            "mode": "EARLY", "amountCapKRW": ENTRY_MIN_KRW,
            "note": "표본 20개 전에는 300만원 상한.",
        }

    rets = [x["returnPct"] for x in obs]
    excess = [x["excessPct"] for x in obs]
    path = peak = 1.0
    mdd = 0.0
    for r in rets:
        path *= 1 + r / 100
        peak = max(peak, path)
        mdd = min(mdd, path / peak - 1)
    downs = [x["maxDownPct"] for x in obs if x["maxDownPct"] is not None]

    win = sum(r > 0 for r in rets) / n * 100
    avg_ret = statistics.fmean(rets)
    avg_exc = statistics.fmean(excess)

    if n < 20:
        mode, cap = "EARLY", ENTRY_MIN_KRW
    elif win < 45 or avg_exc < 0:
        mode, cap = "DEFENSIVE", ENTRY_MIN_KRW
    elif n >= 40 and win >= 55 and avg_exc >= 1.0 and mdd >= -0.20:
        mode, cap = "STRONG", ENTRY_MAX_KRW
    else:
        mode, cap = "NORMAL", ENTRY_MID_KRW

    return {
        "samples5d": n,
        "winRate5d": round(win, 1),
        "avgReturn5d": round(avg_ret, 2),
        "avgExcess5d": round(avg_exc, 2),
        "approxMdd5d": round(mdd * 100, 2),
        "avgMaxDown5d": round(statistics.fmean(downs), 2) if downs else None,
        "mode": mode,
        "amountCapKRW": cap,
        "note": "AGGR_GO 5일 사후수익을 순차복리한 연구용 근사 MDD. 실제 계좌 MDD가 아님.",
    }


def suggested_amount(score: float, risk_pct: float, *, market_cap: float = 1.0,
                     event_status: str = "OK", track_cap: int = ENTRY_MAX_KRW) -> int:
    if score >= 90 and risk_pct <= 4.5:
        base = ENTRY_MAX_KRW
    elif score >= 84 and risk_pct <= 5.0:
        base = ENTRY_MID_KRW
    else:
        base = ENTRY_MIN_KRW
    if market_cap < 0.90 or event_status == "WARN":
        base = ENTRY_MIN_KRW
    return int(min(base, track_cap))


def load_market(market: str) -> tuple[list[dict], str | None, dict]:
    state = _load_json(RESEARCH / f"aggressive_{market}.json", {})
    return state.get("latestRows") or [], state.get("latestSession"), state


def rejection_reason(row: dict) -> str:
    failures = []
    if not row.get("aggressiveGo"):
        failures.append("공격돌파 미충족")
    if (_num(row.get("initialRiskPct"), 99.0) or 99.0) > STRICT_RISK_PCT:
        failures.append("초기리스크 초과")
    if (row.get("marketRisk") or {}).get("blocked"):
        failures.append("시장국면/breadth")
    if (row.get("gapRisk") or {}).get("chase"):
        failures.append("갭/급등 추격")
    if (row.get("eventRisk") or {}).get("status") == "BLOCK":
        failures.append("실적발표 임박")
    if (row.get("fundamental") or {}).get("risk") == "BLOCK":
        failures.append("실적 급악화")
    if estimate_blocked(row):
        failures.append("forward EPS 추정 하향")
    return ", ".join(failures) if failures else "진입 조건 통과"


def build_snapshot() -> dict:
    valuation = load_valuation_us()
    estimates = estimate_history()
    sessions, states, all_rows = {}, [], []

    for market in ("kr", "us"):
        rows, session, state = load_market(market)
        sessions[market] = session
        states.append(state)
        for row in rows:
            if rankable(row):
                all_rows.append(enrich_row(row, market, valuation, estimates))

    apply_sector_strength(all_rows)
    ranked = sorted(
        all_rows,
        key=lambda r: (
            r["strengthScore"],
            _num(r.get("signalCount"), 0.0) or 0.0,
            _num(r.get("volumeRatio"), 0.0) or 0.0,
        ),
        reverse=True,
    )

    strongest = ranked[:TOP_STRONG]
    track = track_record_stats(states)
    entries, used_sectors = [], set()

    for r in strongest:
        r["entryReason"] = rejection_reason(r)
        if not entry_ok(r, r["strengthScore"]):
            continue

        key = r.get("sectorKey")
        if key and key in used_sectors:
            r["entryReason"] = "동일 섹터 신규진입 중복 제한"
            continue

        risk = _num(r.get("initialRiskPct"), 0.0) or 0.0
        mr = r.get("marketRisk") or {}
        ev = r.get("eventRisk") or {}
        amount = suggested_amount(
            r["strengthScore"], risk,
            market_cap=_num(mr.get("exposureCap"), 1.0) or 0.0,
            event_status=ev.get("status") or "UNKNOWN",
            track_cap=int(track["amountCapKRW"]),
        )
        e = dict(r)
        e.update(
            suggestedAmountKRW=amount,
            estimatedInitialRiskKRW=round(amount * risk / 100),
            oneRKRW=round(amount * risk / 100),
            twoRTargetPct=round(risk * 2, 2),
            managementRule="손절가 이탈 전량정리 / +1R 방어강화 / +2R 1/3 익절 / 5거래일 +3% 미만 시간손절",
            entryReason="진입 조건 통과",
        )
        entries.append(e)
        if key:
            used_sectors.add(key)
        if len(entries) >= TOP_ENTRY:
            break

    entry_ids = {(e.get("marketBucket"), e.get("code")) for e in entries}
    for r in strongest:
        r["entryPass"] = (r.get("marketBucket"), r.get("code")) in entry_ids

    session_key = "|".join(str(sessions.get(k) or "") for k in ("kr", "us"))
    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sessionKey": session_key,
        "sessions": sessions,
        "trackRecord": track,
        "rules": {
            "topStrong": TOP_STRONG,
            "topEntry": TOP_ENTRY,
            "maxNewEntriesPerDay": MAX_NEW_ENTRIES_PER_DAY,
            "minEntryScore": MIN_ENTRY_SCORE,
            "maxInitialRiskPct": STRICT_RISK_PCT,
            "entryKRW": [ENTRY_MIN_KRW, ENTRY_MID_KRW, ENTRY_MAX_KRW],
            "sameSectorNewEntryMax": 1,
            "gapHardMaxPct": GAP_HARD_MAX_PCT,
            "breadthHardMin": BREADTH_HARD_MIN,
            "eventBlockDays": EVENT_BLOCK_DAYS,
            "estimateRevisionProxy": "Yahoo forwardEps 누적 변화. 정식 컨센서스 리비전 피드 아님.",
        },
        "strongest": strongest,
        "entries": entries,
    }


def compact_row(r: dict) -> dict:
    keys = [
        "marketBucket", "code", "name", "close", "priceAsOf", "technicalScore", "strengthScore",
        "entryPass", "entryReason", "sector", "industry", "sectorKey", "sectorCohortScore",
        "aggressiveGo", "signalCount", "volumeRatio", "rsi14", "pivotDistancePct",
        "gapPct", "changePct1d", "referenceStop", "initialRiskPct",
        "suggestedAmountKRW", "estimatedInitialRiskKRW",
        "marketRisk", "gapRisk", "eventRisk", "fundamental", "estimate",
    ]
    return {k: r.get(k) for k in keys}


def update_history(snapshot: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "history.json"
    hist = _load_json(path, [])
    rec = {
        "sessionKey": snapshot["sessionKey"],
        "generatedAt": snapshot["generatedAt"],
        "sessions": snapshot["sessions"],
        "trackRecord": snapshot["trackRecord"],
        "strongest": [compact_row(r) for r in snapshot["strongest"]],
        "entries": [compact_row(r) for r in snapshot["entries"]],
    }
    hist = [h for h in hist if h.get("sessionKey") != snapshot["sessionKey"]]
    hist.append(rec)
    path.write_text(json.dumps(hist[-180:], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _fmt(v, digits=1):
    if v is None:
        return "-"
    return f"{v:,.{digits}f}" if isinstance(v, (int, float)) else str(v)


def render_html(snapshot: dict) -> str:
    rows = []
    for i, r in enumerate(snapshot["strongest"], 1):
        mr = r.get("marketRisk") or {}
        fm = r.get("fundamental") or {}
        est = r.get("estimate") or {}
        ev = r.get("eventRisk") or {}
        amount = next(
            (e.get("suggestedAmountKRW") for e in snapshot["entries"]
             if e.get("code") == r.get("code") and e.get("marketBucket") == r.get("marketBucket")), None
        )
        rows.append(
            f"<tr><td>{i}</td><td>{r.get('marketBucket','')}</td>"
            f"<td><b>{r.get('name') or r.get('code')}</b><br><small>{r.get('code')} · {r.get('sector') or r.get('industry') or '-'}</small></td>"
            f"<td>{_fmt(r.get('strengthScore'))}<br><small>기술 {_fmt(r.get('technicalScore'))}</small></td>"
            f"<td><b>{'진입' if r.get('entryPass') else '관찰'}</b><br><small>{r.get('entryReason') or ''}</small></td>"
            f"<td>{mr.get('regime','-')}<br><small>B {_fmt(mr.get('breadth'),2)}</small></td>"
            f"<td>{_fmt(r.get('gapPct'),2)}%</td><td>{_fmt(r.get('volumeRatio'),2)}x</td>"
            f"<td>{_fmt(fm.get('score'))}<br><small>매출 {_fmt(fm.get('revenueYoY'))}%</small></td>"
            f"<td>{est.get('trend','-')}<br><small>{_fmt(est.get('deltaPct'))}%</small></td>"
            f"<td>{ev.get('status','-')}<br><small>{ev.get('date') or '-'}</small></td>"
            f"<td>{_fmt(r.get('initialRiskPct'))}%</td><td>{_fmt(r.get('referenceStop'),2)}</td>"
            f"<td>{'-' if amount is None else f'{amount/1_000_000:.1f}백만원'}</td></tr>"
        )

    executable = snapshot["entries"][:MAX_NEW_ENTRIES_PER_DAY]
    entry_text = "오늘 조건 통과 종목 없음" if not executable else " / ".join(
        f"{e.get('name') or e.get('code')} {e.get('suggestedAmountKRW',0)/1_000_000:.1f}백만원"
        for e in executable
    )
    tr = snapshot["trackRecord"]
    track_text = (
        f"{tr.get('mode')} · 5일 표본 {tr.get('samples5d',0)} · "
        f"승률 {_fmt(tr.get('winRate5d'))}% · 평균 {_fmt(tr.get('avgReturn5d'),2)}% · "
        f"초과 {_fmt(tr.get('avgExcess5d'),2)}% · 근사MDD {_fmt(tr.get('approxMdd5d'),2)}% · "
        f"금액상한 {tr.get('amountCapKRW',0)/1_000_000:.1f}백만원"
    )
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>계좌복구 공격매매</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;margin:0;background:#f6f7f9;color:#111}}
.wrap{{max-width:1400px;margin:auto;padding:20px}} .card{{background:#fff;border-radius:16px;padding:18px;margin-bottom:16px;box-shadow:0 1px 6px #0001}}
h1{{font-size:26px;margin:0 0 8px}} .sub,.note,small{{color:#666}} .hero{{font-size:20px;font-weight:700}}
.scroll{{overflow-x:auto}} table{{width:100%;border-collapse:collapse;font-size:13px;min-width:1200px}}
th,td{{padding:10px 7px;border-bottom:1px solid #eee;text-align:right;vertical-align:top}}
th:nth-child(3),td:nth-child(3),th:nth-child(5),td:nth-child(5){{text-align:left}} .note{{font-size:13px;line-height:1.65}}
@media(max-width:700px){{.wrap{{padding:10px}} table{{font-size:12px}} th,td{{padding:8px 4px}}}}
</style></head><body><div class="wrap">
<div class="card"><h1>계좌복구 공격매매 모드 v2</h1><div class="sub">KR {snapshot['sessions'].get('kr') or '-'} · US {snapshot['sessions'].get('us') or '-'}</div></div>
<div class="card"><div class="hero">오늘 실행 우선순위: {entry_text}</div>
<p class="note">강한 5개를 먼저 찾은 뒤 시장국면/breadth, 갭 추격, 실적, 실적발표, forward EPS 추정 변화, 섹터 중복을 차례로 검사합니다. 후보는 최대 3개지만 하루 실제 신규진입 운용 한도는 2개입니다.</p></div>
<div class="card"><b>공격모드 트랙레코드</b><p class="note">{track_text}<br>{tr.get('note','')}</p></div>
<div class="card scroll"><table><thead><tr><th>#</th><th>시장</th><th>종목</th><th>강도</th><th>판정</th><th>시장</th><th>갭</th><th>거래량</th><th>실적</th><th>EPS추정</th><th>실적일</th><th>위험</th><th>손절</th><th>금액</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<div class="card note"><b>하드 게이트</b><br>
① 강도 {MIN_ENTRY_SCORE:.0f}+ ② 초기리스크 ≤ {STRICT_RISK_PCT:.1f}% ③ RED 또는 breadth&lt;{BREADTH_HARD_MIN:.0%} 금지
④ 갭 {GAP_HARD_MAX_PCT:.0f}%+ 및 피벗에서 벌어진 급등 추격 금지 ⑤ 확인 가능한 실적발표 {EVENT_BLOCK_DAYS}일 이내 금지
⑥ 실적 급악화 차단 ⑦ forward EPS 기록 {ESTIMATE_MIN_SAMPLES}회 이상 뒤 {ESTIMATE_BLOCK_PCT:.0f}% 이하 하향 차단
⑧ 동일 섹터 하루 신규 1개. forward EPS는 Yahoo 애널리스트 추정 기반 보조 프록시입니다.</div>
</div></body></html>"""


def main():
    snapshot = build_snapshot()
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "latest.json").write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    update_history(snapshot)
    update_estimate_history(snapshot["sessionKey"], load_valuation_us())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(render_html(snapshot), encoding="utf-8")
    print(f"strongest={len(snapshot['strongest'])}, entries={len(snapshot['entries'])}, track={snapshot['trackRecord']['mode']}/{snapshot['trackRecord']['samples5d']}")


if __name__ == "__main__":
    main()
