"""전략 현황판: 전략마다 '오늘 잘 돌았나 · 무엇을 골랐나 · 성과가 어떤가'를 한 줄로 모은다. (docs/research/board.json)

읽기 전용이다. 각 전략의 기존 산출물과 원장 집계(ledger.json)를 읽어 한 곳에 모을 뿐 새로 계산하지 않는다.

상태(오늘 잘 돌았나)
  ok   정상: 기대하는 마지막 거래일(또는 주간 실행)까지 기록이 있다
  lag  지연: 한 거래일 뒤처졌거나, 판정 가능한 종목 비율이 낮다
  down 중단: 두 거래일 이상 멈췄거나 주간 실행을 놓쳤다
  wait 기록 대기: 이 현황판이 읽을 첫 기록이 아직 없다
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import config
from .signals import effective_date
from .store import read_json

KR_DUE_UTC = dt.time(13, 0)   # 한국 거래일 d의 기록이 '있어야 하는' 시각: d 13:00 UTC (22:00 KST). SEPA 20:00 KST·RANGE-MR 20:30 KST 실행과 지연 여유
US_DUE_UTC = dt.time(12, 0)   # 미국 거래일 d의 기록이 있어야 하는 시각: d+1 12:00 UTC. 예약 실행(05~12시 UTC)과 지연 여유
WEEKLY_DUE_UTC = dt.time(12, 0)  # 주간 실행(토 00:17 UTC)은 같은 날 12:00 UTC까지는 지연을 봐준다
MIN_OBSERVED_RATIO = 0.80     # 판정 가능한 종목 비율이 이 미만이면 지연으로 본다
TRACKER_FILE = {"sepa": "", "range": "range_", "aggressive": "aggressive_", "rebound": "rebound_"}

# 현황판 행. perf = (원장 전략, 성과를 대표하는 그룹 우선순위). groups = 오늘 선별 종목 수로 보여줄 그룹과 이름.
BOARD = [
    {"key": "sepa", "name": "SEPA 추세템플릿", "tier": "core", "cadence": "daily", "kind": "tracker", "markets": ("kr", "us"),
     "perf": ("sepa", ("TREND",)), "groups": {"TREND": "추세 통과", "READY": "진입 준비", "GO": "진입 신호"}},
    {"key": "funnel", "name": "실적 턴어라운드", "tier": "core", "cadence": "daily", "kind": "funnel", "markets": ("kr", "us"),
     "perf": ("funnel", ("TOP50", "T1_ON")), "groups": {}},
    {"key": "picks", "name": "오늘의 추천", "tier": "core", "cadence": "daily", "kind": "picks", "markets": ("kr", "us"),
     "perf": ("picks", ("PICK_V2", "PICK")), "groups": {"PICK_V2": "추천(v2)", "PICK": "추천(v1)"}},
    {"key": "multifactor", "name": "멀티팩터", "tier": "research", "cadence": "daily", "kind": "multifactor", "markets": ("kr", "us"),
     "perf": ("multifactor", ("BUY", "WATCH")), "groups": {"BUY": "매수", "WATCH": "관찰"}},
    {"key": "technical", "name": "기술적 신호", "tier": "research", "cadence": "daily", "kind": "technical", "markets": ("kr", "us"),
     "perf": ("technical", ("TREND_REVIEW", "REBOUND_REVIEW", "TREND_WATCH", "REBOUND_WATCH")),
     "groups": {"TREND_REVIEW": "추세 매수검토", "TREND_WATCH": "추세 관찰", "TREND_HOLD": "추세 보류",
                "REBOUND_REVIEW": "반등 매수검토", "REBOUND_WATCH": "반등 관찰", "REBOUND_HOLD": "반등 보류"}},
    {"key": "range", "name": "RANGE-MR", "tier": "research", "cadence": "daily", "kind": "tracker", "markets": ("kr", "us"),
     "perf": ("range", ("RANGE_GO", "RANGE_WATCH")), "groups": {"RANGE_GO": "진입", "RANGE_WATCH": "관찰"}},
    {"key": "aggressive", "name": "계좌복구(공격)", "tier": "research", "cadence": "daily", "kind": "tracker", "markets": ("kr", "us"),
     "perf": ("aggressive", ("AGGR_GO", "AGGR_WATCH")), "groups": {"AGGR_GO": "진입", "AGGR_WATCH": "관찰"}},
    {"key": "rebound", "name": "반등관찰", "tier": "research", "cadence": "daily", "kind": "tracker", "markets": ("kr",),
     "perf": ("rebound", ("REB_WATCH",)), "groups": {"REB_WATCH": "관찰"}},
    {"key": "fpd", "name": "FPD 컨센서스", "tier": "research", "cadence": "daily", "kind": "fpd", "markets": ("kr", "us"), "perf": None, "groups": {}},
    {"key": "accumulation", "name": "매집 필터", "tier": "research", "cadence": "daily", "kind": "accumulation", "markets": ("kr",), "perf": None, "groups": {}},
]
STATUS_LABEL = {"ok": "정상", "lag": "지연", "down": "중단", "wait": "기록 대기"}
JUDGE_LABEL = {"none": "아직 표본 없음", "thin": "표본 부족", "concentrated": "독립 구간 부족", "ok": "해석 가능"}


# --- 날짜 --------------------------------------------------------------------------------------------------------
def kr_holidays(bars: list[str] | None, checked_at: dt.datetime | None = None) -> set[str]:
    """NHPLUG 대표 종목의 개장일 목록으로 알 수 있는 한국 휴장일.
    ① 목록이 덮는 구간 안에서 평일인데 목록에 없는 날 ② 목록 확인 시각이 그날 장 마감(07:00 UTC) 뒤인데 목록에 아직 없는 평일."""
    if not bars:
        return set()
    have = set(bars)
    first, last = dt.date.fromisoformat(min(have)), dt.date.fromisoformat(max(have))
    out, d = set(), first
    while d <= last:
        if d.weekday() < 5 and d.isoformat() not in have:
            out.add(d.isoformat())
        d += dt.timedelta(days=1)
    if checked_at:
        d = last + dt.timedelta(days=1)
        while dt.datetime.combine(d, dt.time(7, 0), tzinfo=dt.timezone.utc) <= checked_at:
            if d.weekday() < 5:
                out.add(d.isoformat())
            d += dt.timedelta(days=1)
    return out


def expected_session(market: str, now: dt.datetime, holidays: set[str] | None = None) -> dt.date:
    """지금 시각에 기록이 있어야 하는 가장 최근 거래일. 한국 d는 d 13:00 UTC, 미국 d는 d+1일 12:00 UTC가 기한이다."""
    holidays = holidays or set()
    due, lag = (KR_DUE_UTC, 0) if market == "kr" else (US_DUE_UTC, 1)
    day = now.date()
    while True:
        deadline = dt.datetime.combine(day + dt.timedelta(days=lag), due, tzinfo=dt.timezone.utc)
        if day.weekday() < 5 and day.isoformat() not in holidays and now >= deadline:
            return day
        day -= dt.timedelta(days=1)


def sessions_behind(last: str, expected: dt.date, holidays: set[str] | None = None) -> int:
    """last 다음 날부터 expected까지 거래일(평일, 휴장 제외) 수. last가 expected 이후면 0."""
    holidays = holidays or set()
    d, n = dt.date.fromisoformat(last) + dt.timedelta(days=1), 0
    while d <= expected:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            n += 1
        d += dt.timedelta(days=1)
    return n


def last_saturday(now: dt.datetime) -> dt.date:
    """가장 최근 주간 실행일(토). 오늘이 토요일이어도 12:00 UTC 전이면 지난주 토요일."""
    day = now.date()
    if day.weekday() == 5 and now.time() < WEEKLY_DUE_UTC:
        day -= dt.timedelta(days=1)
    while day.weekday() != 5:
        day -= dt.timedelta(days=1)
    return day


def judge_status(last: str | None, cadence: str, market: str, now: dt.datetime, holidays: set[str] | None = None,
                 run_date: str | None = None) -> tuple[str, dict]:
    if not last:
        return "wait", {}
    if cadence == "weekly":
        sat = last_saturday(now)
        # 주간 전략은 마지막 실행이 그 주 토요일 이후여야 한다. 기록 날짜(시세일)가 아니라 실행한 날짜로 비교한다.
        late = (sat - dt.date.fromisoformat(run_date or last)).days
        if late <= 0:
            return "ok", {"expected": sat.isoformat(), "behind": 0}
        return ("lag" if late <= 3 else "down"), {"expected": sat.isoformat(), "behind": late, "unit": "일"}
    exp = expected_session(market, now, holidays)
    behind = sessions_behind(last, exp, holidays)
    return ("ok" if behind == 0 else "lag" if behind == 1 else "down"), {"expected": exp.isoformat(), "behind": behind, "unit": "거래일"}


# --- 전략별 읽기 -----------------------------------------------------------------------------------------------------
def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_tracker(strategy: str, market: str, root: Path) -> dict | None:
    doc = _json(root / "research" / f"{TRACKER_FILE[strategy]}{market}.json")
    if not doc or not doc.get("latestSession"):
        return None
    latest = doc["latestSession"]
    days = [d for d in (doc.get("days") or {}).values() if d.get("date") == latest]
    day = max(days, key=lambda d: d.get("recordedAt") or "") if days else None
    out = {"last": latest, "updatedAt": doc.get("updatedAt") or (day or {}).get("recordedAt"), "selected": {}}
    if day:
        obs = day.get("observedCodes")
        rows = day.get("rows")
        n_obs = len(obs) if isinstance(obs, list) else obs
        if rows and n_obs is not None:
            out["coverage"] = {"observed": n_obs, "rows": rows, "ratio": round(n_obs / rows, 3)}
        series = day.get("strategySeriesId")
        for key, flag in (doc.get("membership") or {}).items():
            if flag and series and key.startswith(series + ":"):
                out["selected"][key.rsplit(":", 1)[1]] = out["selected"].get(key.rsplit(":", 1)[1], 0) + 1
    return out


def read_funnel(market: str, root: Path) -> dict | None:
    doc = _json(root / "docs" / "research" / f"funnel_{market}.json")
    if not doc or not doc.get("recordedAt"):
        return None
    rec = doc["recordedAt"]
    out = {"last": doc.get("session") or effective_date(rec, market).isoformat(), "updatedAt": rec,
           "selected": {"TOP50": len(doc.get("top") or [])}}
    if doc.get("degraded"):
        out["degraded"] = list(doc["degraded"].values())
    return out


def _latest_ledger_file(strategy: str, market: str, root: Path):
    files = sorted((root / "research" / "ledger" / "signals" / strategy / market).glob("*.json.gz"))
    return files[-1] if files else None


def read_ledger_file(strategy: str, market: str, root: Path) -> dict | None:
    from .store import read_gz
    f = _latest_ledger_file(strategy, market, root)
    if not f:
        return None
    doc = read_gz(f)
    sel: dict[str, int] = {}
    for r in doc["rows"]:
        for g in r["groups"]:
            if g != config.CONTROL:
                sel[g] = sel.get(g, 0) + 1
    return {"last": doc["effectiveDate"], "updatedAt": doc["recordedAt"], "selected": sel}


def read_technical(market: str, root: Path) -> dict | None:
    """원장 기록이 있으면 그것을, 없으면 화면 데이터의 판정 건수를 쓴다(첫 원장 기록 전)."""
    out = read_ledger_file("technical", market, root)
    doc = _json(root / "docs" / "technical" / "data" / f"latest_{market}.json") or {}
    hist = _json(root / "docs" / "technical" / "data" / f"history_{market}.json") or []
    if out:
        return out
    if not hist:
        return None
    from .adapters import technical_groups
    sel: dict[str, int] = {}
    for r in doc.get("rows") or []:
        for g in technical_groups(r):
            sel[g] = sel.get(g, 0) + 1
    return {"last": hist[-1]["date"], "updatedAt": None, "selected": sel, "note": "원장 기록은 다음 실행부터 쌓입니다"}


def read_fpd(market: str, root: Path) -> dict | None:
    doc = _json(root / "docs" / "data" / "fpd" / f"signal_latest_{market}.json")
    if not doc or not doc.get("snapshotDate"):
        return None
    return {"last": doc["snapshotDate"], "updatedAt": None, "selected": {}, "note": f"추정치 수정 관찰 {len(doc.get('rows') or [])}종목 · 연구 중({doc.get('status')})"}


def read_accumulation(market: str, root: Path) -> dict | None:
    doc = _json(root / "docs" / "research" / "accumulation.json")
    recent = ((doc or {}).get("forward") or {}).get("recent") or []
    if not recent:
        return None
    norm = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(str(d)) == 8 and "-" not in str(d) else str(d)
    last = max(recent, key=lambda x: norm(x["date"]))
    return {"last": norm(last["date"]), "updatedAt": last.get("recordedAt"), "selected": {},
            "note": "후보 v1 FLOW {} · v2 {}".format((last.get("FLOW") if not isinstance(last.get("FLOW"), (list, dict)) else len(last["FLOW"])),
                                                    (last.get("V2") if not isinstance(last.get("V2"), (list, dict)) else len(last["V2"])))}


def read_row(spec: dict, market: str, root: Path) -> dict | None:
    kind = spec["kind"]
    if kind == "tracker":
        return read_tracker(spec["key"], market, root)
    if kind == "funnel":
        return read_funnel(market, root)
    if kind in ("multifactor", "picks"):
        return read_ledger_file(kind, market, root)
    if kind == "technical":
        return read_technical(market, root)
    if kind == "fpd":
        return read_fpd(market, root)
    return read_accumulation(market, root)


# --- 성과 -------------------------------------------------------------------------------------------------------------
def pick_perf(ledger: dict, spec: dict, market: str) -> dict | None:
    if not spec["perf"]:
        return None
    strategy, priority = spec["perf"]
    groups = ((ledger.get("strategies") or {}).get(strategy) or {}).get(market) or {}
    if not groups:
        return None
    # 완료 표본이 있는 첫 그룹, 없으면 우선순위 첫 그룹(대기 중인 표본만 있는 상태)
    chosen = next((g for g in priority if g in groups and (groups[g].get("5") or {}).get("n")), None) or \
        next((g for g in priority if g in groups), None)
    if not chosen:
        return None
    out = {"group": chosen}
    for h in ("5", "20"):
        r = groups[chosen].get(h) or {}
        out["h" + h] = {k: r.get(k) for k in ("n", "pending", "unavailable", "meanExcessPct", "excessCI95", "winRate", "distinctDates",
                                              "independentWindows", "status", "firstMature")}
        out["h" + h]["judge"] = JUDGE_LABEL.get(r.get("status"), "아직 표본 없음")
    return out


def perf_note(spec: dict, ledger: dict, market: str) -> str:
    """성과 칸이 비는 이유. 성과를 집계하는 전략인데 아직 신호가 안 쌓였으면 '원장 집계 대기', 집계 대상이 아니면 '연구 단계'."""
    if not spec["perf"]:
        return "성과 집계 없음(연구 단계)"
    strategy, _ = spec["perf"]
    return "" if (((ledger.get("strategies") or {}).get(strategy) or {}).get(market)) else "원장 집계 대기 (다음 갱신부터 표시)"


# --- 조립 -------------------------------------------------------------------------------------------------------------
def build(ledger: dict, now: dt.datetime | None = None, root: Path | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    root = root or config.ROOT
    bars, checked = None, None
    flow = _json(root / "research" / "nhplug" / "kr_flow.json") or {}
    try:
        bars = sorted({dt.datetime.strptime(str(b), "%Y%m%d").date().isoformat() for b in flow.get("krBars") or []})
        checked = dt.datetime.fromisoformat(str(flow.get("generatedAt")))
        checked = checked if checked.tzinfo else checked.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        bars = None
    holidays = {"kr": kr_holidays(bars, checked), "us": set()}
    rows = []
    for spec in BOARD:
        for market in spec["markets"]:
            data = read_row(spec, market, root)
            last = (data or {}).get("last")
            run_date = ((data or {}).get("updatedAt") or "")[:10] or None
            status, why = judge_status(last, spec["cadence"], market, now, holidays[market], run_date)
            notes = []
            cov = (data or {}).get("coverage")
            if status == "ok" and cov and cov["ratio"] < MIN_OBSERVED_RATIO:
                status = "lag"
                notes.append(f"판정 가능한 종목이 적습니다({cov['observed']}/{cov['rows']})")
            if data and data.get("degraded"):
                if status == "ok":
                    status = "lag"
                notes.extend(data["degraded"])
            if data and data.get("note"):
                notes.append(data["note"])
            if status in ("lag", "down") and why.get("behind"):
                notes.insert(0, f"기대 {why['expected']} 대비 {why['behind']}{why.get('unit', '')} 뒤처짐")
            selected = [{"group": g, "label": spec["groups"].get(g, g), "n": n}
                        for g, n in sorted(((data or {}).get("selected") or {}).items(), key=lambda kv: (-kv[1], kv[0]))]
            if spec["groups"]:
                order = list(spec["groups"])
                selected = sorted([s for s in selected if s["group"] in spec["groups"]], key=lambda s: order.index(s["group"]))
            rows.append({"key": spec["key"], "name": spec["name"], "tier": spec["tier"], "market": market, "cadence": spec["cadence"],
                         "status": status, "statusLabel": STATUS_LABEL[status], "lastRecord": last, "expected": why.get("expected"),
                         "updatedAt": (data or {}).get("updatedAt"), "coverage": cov, "selected": selected,
                         "perf": pick_perf(ledger, spec, market), "perfNote": perf_note(spec, ledger, market), "notes": notes})
    count = {s: sum(1 for r in rows if r["status"] == s) for s in STATUS_LABEL}
    return {"schemaVersion": 1, "generatedAt": now.isoformat(), "ledgerGeneratedAt": ledger.get("generatedAt"), "counts": count,
            "minN": config.MIN_N, "minWindows": config.MIN_WINDOWS, "rows": rows}
