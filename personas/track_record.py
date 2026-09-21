"""personas/track_record.py — 성과 트래커(research/{kr,us}.json) 읽기·집계·점검. 읽기 전용.

research_tracker.py 가 만든 "신호 에피소드 + 5/20/60거래일 사후 성과"를 페르소나 로직이 쓸 수 있는
기저율(base rate)로 바꾼다. 트래커 자체는 수정하지 않는다.

트래커 데이터를 그대로 세면 안 되는 이유 (실제 데이터에서 확인됨)
------------------------------------------------------------
전략 버전(strategyId)은 config 와 sepa/*.py, screening.py 의 해시라서 코드가 바뀔 때마다 새로 생긴다.
새 버전은 membership 이 비어 있으므로 **이미 통과 중이던 종목이 다시 "신규 신호"로 등록**된다.
그래서 같은 종목이 여러 번 잡혀 표본이 부풀려진다(미국 신호 167개 = 고유 종목 57개).
→ 기본은 "종목·그룹당 가장 이른 신호 1개"로 정리하고, 원본 대비 몇 개가 중복이었는지를 항상 함께 보고한다.

놓치지 않기 위한 원칙
--------------------
- pending(아직 기간 미도래) / unavailable(가격 누락) / complete 를 각각 센다. 조용히 버리지 않는다.
- 완료 표본이 없거나 MIN_N 미만이면 그 사실 자체를 결과(status)로 돌려준다(빈 값으로 두지 않음).
- audit() 이 관측 누락 평일, 오래된 최신 세션, 전략 버전 난립, 가격 누락 신호를 경고로 낸다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
MIN_N = 30                    # 이 미만이면 "표본 부족"으로 표시(통계적 해석 제한)
MIN_DATES = 5                 # 신호일이 이보다 적으면 표본이 많아도 "신호일 집중"(독립 표본 아님)으로 표시
HORIZONS = (5, 20, 60)
GROUPS = ("TREND", "READY", "GO")
STALE_WEEKDAYS = 3            # 최신 세션이 오늘보다 이 평일 수 이상 오래되면 경고
PENDING_SLACK = 3             # 기간이 지났는데도 pending 이면 수집 누락 의심(휴장 여유 평일 수)


# ---------------------------------------------------------------------------
# 로드
# ---------------------------------------------------------------------------
def load_state(market: str, root: Path = ROOT) -> Optional[dict]:
    m = market.lower()
    for p in (root / "research" / f"{m}.json", root / "docs" / "research" / f"{m}.json"):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def norm_code(market: str, code: Any) -> str:
    text = str(code).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if market.lower() == "kr" and text.isdigit() else text


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _today_kst() -> dt.date:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()


# ---------------------------------------------------------------------------
# 중복 정리
# ---------------------------------------------------------------------------
def dedupe_signals(signals: Iterable[dict], market: str, group: str) -> tuple[list[dict], dict]:
    """(종목, 그룹)당 가장 이른 신호 1개만 남긴다.

    반환 info: raw(원본 신호 수), unique(고유 종목 수), duplicatesRemoved, strategyVersions.
    """
    rows = [s for s in signals if s.get("group") == group]
    rows.sort(key=lambda s: (s.get("date", ""), s.get("strategyId", "")))
    kept: dict[str, dict] = {}
    for s in rows:
        kept.setdefault(norm_code(market, s.get("code")), s)
    info = {
        "raw": len(rows),
        "unique": len(kept),
        "duplicatesRemoved": len(rows) - len(kept),
        "strategyVersions": len({s.get("strategyId") for s in rows}),
    }
    return list(kept.values()), info


def _seed_dates(state: dict) -> dict[str, str]:
    """전략 버전별 첫 관측일. 이 날의 신호는 '추적 시작 시점에 이미 통과 중이던 종목'(시드)이다."""
    out: dict[str, str] = {}
    for d in (state.get("days") or {}).values():
        sid, date = d.get("strategyId"), d.get("date")
        if sid and date and (sid not in out or date < out[sid]):
            out[sid] = date
    return out


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------
def _outcome(signal: dict, horizon: int) -> dict:
    return (signal.get("outcomes") or {}).get(str(horizon)) or {}


def horizon_stats(signals: list[dict], horizon: int) -> dict:
    complete, pending, unavailable, dates = [], 0, {}, set()
    for s in signals:
        o = _outcome(s, horizon)
        status = o.get("status") or "pending"
        if status == "complete" and _num(o.get("returnPct")) is not None:
            complete.append(o)
            dates.add(s.get("date"))
        elif status == "unavailable":
            reason = o.get("reason") or "사유 미기록"
            unavailable[reason] = unavailable.get(reason, 0) + 1
        else:
            pending += 1
    rets = [o["returnPct"] for o in complete]
    excess = [o["excessPct"] for o in complete if _num(o.get("excessPct")) is not None]
    downs = [o["maxDownPct"] for o in complete if _num(o.get("maxDownPct")) is not None]
    out: dict[str, Any] = {
        "n": len(complete), "distinctSignalDates": len(dates),   # 신호일이 적으면 종목 수가 많아도 독립 표본이 아님
        "pending": pending, "unavailable": sum(unavailable.values()),
        "unavailableReasons": unavailable,
        "meanReturnPct": statistics.fmean(rets) if rets else None,
        "medianReturnPct": statistics.median(rets) if rets else None,
        "winRate": (sum(r > 0 for r in rets) / len(rets)) if rets else None,
        "meanExcessPct": statistics.fmean(excess) if excess else None,
        "excessWinRate": (sum(x > 0 for x in excess) / len(excess)) if excess else None,
        "meanMaxDownPct": statistics.fmean(downs) if downs else None,
    }
    return out


def build_base_rate(state: Optional[dict], market: str, group: str = "TREND", *,
                    horizons: tuple[int, ...] = HORIZONS, min_n: int = MIN_N, min_dates: int = MIN_DATES) -> dict:
    """페르소나 context 로 넘길 기저율. 데이터가 없거나 얕아도 status 로 그대로 알려준다."""
    if not state:
        return {"group": group, "market": market.lower(), "status": "none", "reason": "성과 트래커 파일 없음",
                "primary": None, "horizons": {}}
    kept, info = dedupe_signals(state.get("signals") or [], market, group)
    seeds = _seed_dates(state)
    seeded = sum(1 for s in kept if s.get("date") == seeds.get(s.get("strategyId")))
    hs = {str(h): horizon_stats(kept, h) for h in horizons}

    eligible = [h for h in horizons if hs[str(h)]["n"] >= min_n]
    if eligible:
        primary = max(eligible)
    else:
        with_data = [h for h in horizons if hs[str(h)]["n"] > 0]
        primary = max(with_data, key=lambda h: (hs[str(h)]["n"], h)) if with_data else None
    if primary is None:
        status, reason = "none", "완료된 사후 성과 표본이 아직 없음"
    elif hs[str(primary)]["n"] >= min_n and hs[str(primary)]["distinctSignalDates"] >= min_dates:
        status, reason = "ok", ""
    elif hs[str(primary)]["n"] >= min_n:
        status = "clustered"
        reason = (f"표본 {hs[str(primary)]['n']}개지만 신호일이 {hs[str(primary)]['distinctSignalDates']}일뿐이라 "
                  "독립 표본이 아님(시장 공통 요인의 영향이 큼)")
    else:
        status, reason = "thin", f"표본 {hs[str(primary)]['n']}개 < {min_n}개"

    days = sorted({d.get("date") for d in (state.get("days") or {}).values() if d.get("date")})
    return {
        "group": group, "market": market.lower(), "status": status, "reason": reason,
        "asOf": state.get("latestSession"), "primary": str(primary) if primary else None,
        "minN": min_n, "minDates": min_dates, **info, "seededCohort": seeded, "newEntries": len(kept) - seeded,
        "horizons": hs,
        "coverage": {"firstSession": days[0] if days else None, "lastSession": days[-1] if days else None,
                     "observedSessions": len(days)},
    }


def stock_signals(state: Optional[dict], market: str, code: Any, horizons: tuple[int, ...] = HORIZONS) -> list[dict]:
    """한 종목의 신호 이력(그룹별 가장 이른 신호)과 사후 성과."""
    if not state:
        return []
    want = norm_code(market, code)
    out = []
    for group in GROUPS:
        kept, _ = dedupe_signals(state.get("signals") or [], market, group)
        for s in kept:
            if norm_code(market, s.get("code")) != want:
                continue
            outs = {}
            for h in horizons:
                o = _outcome(s, h)
                outs[str(h)] = {"status": o.get("status") or "pending", "returnPct": _num(o.get("returnPct")),
                                "excessPct": _num(o.get("excessPct")), "observedSessions": o.get("observedSessions")}
            out.append({"group": group, "date": s.get("date"), "originalClose": s.get("originalClose"), "outcomes": outs})
    return sorted(out, key=lambda x: (x["date"], x["group"]))


def context_for(market: str, root: Path = ROOT, state: Optional[dict] = None) -> dict:
    """personas.build_evidence(context=...) 에 넘길 시장 단위 컨텍스트."""
    state = state if state is not None else load_state(market, root)
    rates = {g: build_base_rate(state, market, g) for g in GROUPS}
    return {"baseRate": rates["TREND"], "baseRates": rates, "trackerState": state}


# ---------------------------------------------------------------------------
# 점검 (수집 누락 감시)
# ---------------------------------------------------------------------------
def audit(state: Optional[dict], market: str, *, today: Optional[dt.date] = None,
          calendar: Optional[Iterable[str]] = None) -> dict:
    """트래커가 데이터를 놓치고 있는지 점검한다.

    calendar: 실제 거래일 목록(YYYY-MM-DD). 없으면 평일 기준이라 휴장일이 '누락'으로 보일 수 있다.
    """
    today = today or _today_kst()
    issues: list[dict] = []

    def add(level: str, code: str, message: str, **extra: Any) -> None:
        issues.append({"level": level, "code": code, "message": message, **extra})

    if not state:
        add("error", "no_state", f"{market.upper()} 성과 트래커 파일(research/{market.lower()}.json)이 없음")
        return {"market": market.lower(), "ok": False, "issues": issues}

    days = sorted({d.get("date") for d in (state.get("days") or {}).values() if d.get("date")})
    if not days:
        add("error", "no_days", "관측일이 하나도 없음")
    else:
        first, last = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])
        expected = ([dt.date.fromisoformat(c) for c in calendar if first <= dt.date.fromisoformat(c) <= last]
                    if calendar else _weekdays(first, last))
        seen = set(days)
        missing = [d.isoformat() for d in expected if d.isoformat() not in seen]
        if missing:
            basis = "실제 거래일" if calendar else "평일 기준(휴장일 포함 가능)"
            add("warn", "missing_sessions", f"관측이 없는 세션 {len(missing)}일 ({basis}): {', '.join(missing)}", dates=missing)
        lag = len(_weekdays(last, today)) - 1
        if lag >= STALE_WEEKDAYS:
            add("warn", "stale", f"최신 관측 세션 {last.isoformat()} — 오늘보다 평일 {lag}일 뒤처짐", lastSession=last.isoformat())

    if state.get("deferredReason"):
        add("info", "deferred", f"마감 전 입력 제외 이력: {state.get('deferredAt')} — {state['deferredReason']}")
    if state.get("excludedObservations"):
        add("warn", "excluded", f"장중 등으로 제외된 관측 {len(state['excludedObservations'])}건(감사용 아카이브에만 남음)")

    sigs = state.get("signals") or []
    versions = {s.get("strategyId") for s in sigs}
    for group in GROUPS:
        kept, info = dedupe_signals(sigs, market, group)
        if info["duplicatesRemoved"] > 0:
            add("info", "duplicates",
                f"{group}: 원본 신호 {info['raw']}개 → 고유 종목 {info['unique']}개 (전략 버전 {info['strategyVersions']}개에서 중복 등록, 집계 시 제거)",
                group=group, **info)

    unavailable: dict[str, int] = {}
    suspect = 0
    for s in sigs:
        try:
            sig_date = dt.date.fromisoformat(s["date"])
        except (KeyError, ValueError):
            continue
        elapsed = len(_weekdays(sig_date, today)) - 1
        for h in HORIZONS:
            o = _outcome(s, h)
            if o.get("status") == "unavailable":
                unavailable[o.get("reason") or "사유 미기록"] = unavailable.get(o.get("reason") or "사유 미기록", 0) + 1
            elif (o.get("status") or "pending") == "pending" and elapsed >= h + PENDING_SLACK:
                suspect += 1
    if unavailable:
        add("warn", "unavailable", f"가격 누락으로 사후 성과를 못 구한 건 {sum(unavailable.values())}개: {unavailable}", reasons=unavailable)
    if suspect:
        add("warn", "pending_overdue", f"기간이 충분히 지났는데도 pending 인 성과 {suspect}건 — 벤치마크/가격 수집 누락 의심")

    tr = build_base_rate(state, market, "TREND")
    if tr["status"] != "ok":
        add("info", "thin_sample", f"TREND 사후 성과 표본이 얕음: {tr['reason']}", status=tr["status"])
    level_rank = {"error": 2, "warn": 1, "info": 0}
    return {"market": market.lower(), "ok": not any(i["level"] == "error" for i in issues),
            "worst": max((i["level"] for i in issues), key=level_rank.get, default="ok"),
            "observedSessions": len(days), "signals": len(sigs), "strategyVersions": len(versions), "issues": issues}


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------
def format_base_rate(br: dict) -> str:
    lines = [f"[{br['market'].upper()} {br['group']}] 상태={br['status']}"
             + (f" ({br['reason']})" if br.get("reason") else "")]
    if br.get("horizons"):
        lines.append(f"  원본 신호 {br['raw']} → 고유 종목 {br['unique']} (중복 {br['duplicatesRemoved']}, 전략버전 {br['strategyVersions']}), "
                     f"시드(추적 시작 시 이미 통과) {br['seededCohort']} / 신규 진입 {br['newEntries']}, 기준일 {br['asOf']}")
        for h, s in br["horizons"].items():
            mean = "-" if s["meanReturnPct"] is None else f"{s['meanReturnPct']:+.2f}%"
            exc = "-" if s["meanExcessPct"] is None else f"{s['meanExcessPct']:+.2f}%p"
            win = "-" if s["winRate"] is None else f"{s['winRate']:.0%}"
            lines.append(f"  +{h:>2}거래일: 완료 {s['n']:>3}(신호일 {s['distinctSignalDates']}일) | 대기 {s['pending']:>3} | 가격누락 {s['unavailable']:>2} | 평균 {mean:>8} | 지수대비 {exc:>9} | 승률 {win}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="성과 트래커 기저율/점검 (읽기 전용)")
    ap.add_argument("--market", choices=["kr", "us", "all"], default="all")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    for m in (["kr", "us"] if a.market == "all" else [a.market]):
        state = load_state(m)
        result = {"baseRates": {g: build_base_rate(state, m, g) for g in GROUPS}, "audit": audit(state, m)}
        if a.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
            continue
        print(format_base_rate(result["baseRates"]["TREND"]))
        for g in ("READY", "GO"):
            br = result["baseRates"][g]
            print(f"[{m.upper()} {g}] 상태={br['status']} — 원본 신호 {br.get('raw', 0)}개")
        for i in result["audit"]["issues"]:
            print(f"  ({i['level']}) {i['message']}")
        print()


if __name__ == "__main__":
    main()
