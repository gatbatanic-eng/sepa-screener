"""시세 최신성 확인. 정기 실행이 미국·한국 스크리닝보다 먼저 시작해도(둘 다 GitHub 예약 지연이 있다)
'그날의 기록'(보고서·메모·추천·권고안 스냅샷, 모두 한 번만 쓰고 수정 금지)을 낡은 시세로 확정하지 않는다.
기준일(session_date)의 장 마감 시세가 아직 없으면 기록을 미루고, 같은 날 뒤 실행(ledger.yml 재시도)이 쓴다.
기한(다음 날 18:00 UTC)까지 끝내 안 오면 휴장일로 보고 '시세가 이 날짜까지만 있음' 표시를 달아 기록한다.
한국은 휴장일을 미리 알 수 있다: NHPLUG 대표 종목의 최근 일봉 날짜 목록(research/nhplug/kr_flow.json의 krBars)을 기준일 한국 장 마감(07:00 UTC) 뒤에
확인했는데 기준일이 그 목록에 없으면(목록이 기준일을 덮는 구간일 때) 그날은 휴장이다. 이때 한국은 그 이전 마지막 개장일까지만 기다린다."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEADLINE = dt.timedelta(days=1, hours=18)   # 기준일 00:00 UTC + 1일 18시간 = 다음 날 18:00 UTC. 마지막 재시도(19:10)가 이 뒤에 돈다.
MARKETS = ("kr", "us")
KR_CLOSE_UTC = dt.time(7, 0)                # 한국 장 마감(06:30 UTC) + 집계 여유. 이 뒤에 확인한 '마지막 봉'만 휴장 판정에 쓴다.


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def latest_sessions(root: Path | None = None) -> dict[str, str]:
    """시장별 가장 최근 시세일. 계좌복구 갱신본과 SEPA 추적기 중 늦은 쪽. 모르면 빈 문자열."""
    root = root or ROOT
    rec = _read(root / "docs" / "recovery" / "data" / "latest.json").get("sessions") or {}
    out = {}
    for m in MARKETS:
        out[m] = max(str(rec.get(m) or ""), str(_read(root / "research" / f"{m}.json").get("latestSession") or ""))
    return out


def kr_last_bar(root: Path | None = None) -> dict | None:
    """{'bars': ['YYYY-MM-DD', ...], 'checkedAt': datetime} — NHPLUG 수급 수집이 남긴 한국 최근 개장일 목록. 없으면 None."""
    d = _read((root or ROOT) / "research" / "nhplug" / "kr_flow.json")
    try:
        bars = sorted({dt.datetime.strptime(str(b), "%Y%m%d").date().isoformat() for b in d.get("krBars") or []})
        at = dt.datetime.fromisoformat(str(d.get("generatedAt")))
    except (TypeError, ValueError):
        return None
    return {"bars": bars, "checkedAt": at if at.tzinfo else at.replace(tzinfo=dt.timezone.utc)} if bars else None


def check(today: dt.date, now: dt.datetime | None = None, sessions: dict[str, str] | None = None, kr_bar: dict | None = None) -> dict:
    """{'hold': 기록을 미뤄야 하나, 'forced': 기한이 지나 낡은 시세로 쓰나, 'stale': {시장: {have, need}}, 'note': 안내 문구}"""
    now = now or dt.datetime.now(dt.timezone.utc)
    sessions = sessions if sessions is not None else latest_sessions()
    need = today.isoformat()
    needs = {m: need for m in MARKETS}
    kr_bar = kr_bar if kr_bar is not None else kr_last_bar()
    closed = None
    if kr_bar and kr_bar["bars"] and kr_bar["bars"][0] <= need and kr_bar["checkedAt"] >= dt.datetime.combine(today, KR_CLOSE_UTC, dt.timezone.utc):
        prior = max(b for b in kr_bar["bars"] if b <= need)
        if prior < need:   # 기준일이 개장일 목록(그 앞뒤 날짜가 있는 구간)에 없다 = 한국 휴장. 그 이전 마지막 개장일까지만 기다린다
            needs["kr"], closed = prior, prior
    stale = {m: {"have": sessions.get(m) or "", "need": needs[m]} for m in MARKETS if (sessions.get(m) or "") < needs[m]}
    if not stale:
        return {"hold": False, "forced": False, "stale": {}, "note": None, "krClosed": closed}
    deadline = dt.datetime.combine(today, dt.time(0, 0), dt.timezone.utc) + DEADLINE
    names = ", ".join(f"{'미국' if m == 'us' else '한국'} {v['have'] or '없음'}" for m, v in stale.items())
    if now >= deadline:
        return {"hold": False, "forced": True, "stale": stale, "krClosed": closed,
                "note": f"기준일 {need}의 시세가 끝내 오지 않아(휴장 또는 수집 지연) 가장 최근 시세로 썼다: {names}까지"}
    return {"hold": True, "forced": False, "stale": stale, "krClosed": closed, "note": f"기준일 {need}의 시세가 아직 없다: {names}까지. 기록을 미룬다"}
