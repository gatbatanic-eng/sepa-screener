"""검증 팀. 추천 팀(picks.py)과 **다른 입력·다른 기준**으로 각 추천 종목을 감사한다. 이 모듈은 picks를 가져오지 않는다.

입력: 추천 종목의 (시장, 코드)와 추천 팀이 쓴 가격(교차 대조용)만 받는다. 판단 근거는 스크리너 본 결과(latest_*.json)·종목 차트(stock_charts)·
계좌복구 행의 위험 정보·페르소나 증거를 직접 읽는다.
감사관 6명 → 각자 OK/WARN/FAIL. FAIL이 하나라도 있으면 '기각', WARN이 있으면 '조건부', 모두 OK면 '통과'.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config

EXIT_WARN = ("WATCH_EXIT", "PROFIT_ALERT", "TREND_BREAK", "EXIT")


def _n(v):
    return v if isinstance(v, (int, float)) and v == v else None


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Screener:
    """스크리너 본 결과를 (시장, 정규화 코드)로 찾는 인덱스."""

    def __init__(self, root: Path):
        self.root = root
        self.rows: dict[tuple[str, str], dict] = {}
        for m in ("kr", "us"):
            for r in _read(root / "docs" / "data" / f"latest_{m}.json") or []:
                self.rows[(m, self.norm(m, r.get("code")))] = r

    @staticmethod
    def norm(market: str, code) -> str:
        return str(code).zfill(6) if market == "kr" else str(code)

    def get(self, market: str, code):
        return self.rows.get((market, self.norm(market, code)))


def _a(name, level, text):
    return {"auditor": name, "level": level, "text": text}


def audit(pick: dict, scr: Screener, ctx: dict) -> dict:
    """pick: {market('kr'|'us'), code, name, price(추천 팀이 쓴 종가), row(계좌복구 행)}, ctx: {macroRegime, expectedSession(시장→날짜)}"""
    m, code, rec = pick["market"], pick["code"], pick.get("row") or {}
    row = scr.get(m, code)
    notes: list[dict] = []

    # 1) 데이터 감사관 — 서로 다른 두 파일의 종가 대조, 신선도
    if row is None:
        notes.append(_a("데이터", "FAIL", "스크리너 본 결과에 이 종목이 없음"))
    else:
        if row.get("status") != "OK":
            notes.append(_a("데이터", "FAIL", f"스크리너가 분석 불가 처리: {row.get('reason')}"))
        close, price = _n(row.get("close")), _n(pick.get("price"))
        if close and price:
            gap = abs(price / close - 1)
            notes.append(_a("데이터", "FAIL" if gap > config.VERIFY_PRICE_TOL else "OK",
                            f"추천 데이터 종가와 스크리너 본 결과 종가 차이 {gap:.2%}" + (" — 허용(1%) 초과" if gap > config.VERIFY_PRICE_TOL else "")))
    chart = _read(scr.root / "docs" / "data" / "stock_charts" / m / f"{Screener.norm(m, code)}.json")
    if chart and chart.get("close") and _n(pick.get("price")):
        last = _n(chart["close"][-1])
        if last:
            gap = abs(pick["price"] / last - 1)
            notes.append(_a("데이터", "WARN" if gap > config.VERIFY_PRICE_TOL else "OK", f"종목 차트 마지막 종가와 차이 {gap:.2%} (차트 기준일 {chart.get('priceAsOf')})"))
    elif not chart:
        notes.append(_a("데이터", "OK", "종목 차트 파일 없음 — 교차 대조 한 건 생략"))
    if rec.get("dataFreshness") not in (None, "CURRENT"):
        notes.append(_a("데이터", "FAIL", f"시세 신선도 {rec.get('dataFreshness')}"))
    exp = (ctx.get("expectedSession") or {}).get(m)
    as_of = rec.get("priceAsOf")
    if exp and as_of and str(as_of)[:10] < str(exp)[:10]:
        notes.append(_a("데이터", "WARN", f"시세 기준일 {as_of}이 최신 거래일 {exp}보다 이전"))

    # 2) 리스크 감사관 — SEPA 구조적 손절폭, 청산 경고, 유동성
    if row:
        sepa_risk = _n(row.get("initRisk"))
        if sepa_risk is not None:
            lvl = "FAIL" if sepa_risk > config.VERIFY_SEPA_RISK_FAIL else "WARN" if sepa_risk > config.VERIFY_SEPA_RISK_WARN else "OK"
            notes.append(_a("리스크", lvl, f"SEPA 구조적 손절폭 {sepa_risk:.1f}% (경고 > {config.VERIFY_SEPA_RISK_WARN:.0f}%, 기각 > {config.VERIFY_SEPA_RISK_FAIL:.0f}%)"))
        if row.get("exitState") in EXIT_WARN:
            notes.append(_a("리스크", "FAIL", f"청산 경고 {row['exitState']}"))
        if row.get("riskFlag"):
            notes.append(_a("리스크", "WARN", f"위험 플래그 {row['riskFlag']}"))
    if rec.get("liquidityOk") is False:
        notes.append(_a("리스크", "FAIL", "유동성(20일 거래대금) 기준 미달"))

    # 3) 추세 감사관 — 정식 SEPA 기준과의 관계
    if row:
        if row.get("passAll") is not True:
            notes.append(_a("추세", "WARN", "정식 SEPA 추세 템플릿 8개 조건 미통과(공격 모멘텀 기준만 충족)"))
        if row.get("zone") == "EXTENDED":
            notes.append(_a("추세", "WARN", "SEPA 구간이 EXTENDED(이미 많이 오른 상태)"))

    # 4) 실적·이벤트 감사관
    ev, fu, es = rec.get("eventRisk") or {}, rec.get("fundamental") or {}, rec.get("estimate") or {}
    if ev.get("status") == "BLOCK":
        notes.append(_a("실적", "FAIL", f"실적 발표 임박({ev.get('date')})"))
    elif ev.get("status") not in (None, "OK"):
        notes.append(_a("실적", "OK", f"실적 발표일 확인 불가({ev.get('status')}) — 직접 확인 필요"))
    if fu.get("risk") == "BLOCK":
        notes.append(_a("실적", "FAIL", "최근 실적 급악화"))
    if es.get("trend") == "DOWN":
        notes.append(_a("실적", "WARN", f"forward EPS 추정 하향({es.get('deltaPct')}%, Yahoo 누적 변화·정식 컨센서스 아님)"))

    # 5) 국면·갭 감사관
    mr = rec.get("marketRisk") or {}
    if mr.get("blocked"):
        br = _n(mr.get("breadth"))
        notes.append(_a("국면", "WARN", f"시장 가드가 신규 진입 금지 구간({mr.get('blockReason')}, breadth {f'{br:.0%}' if br is not None else '–'})"))
    elif rec.get("regime") == "RED":
        notes.append(_a("국면", "WARN", "해당 시장 국면 RED"))
    if ctx.get("macroRegime") == "risk_off":
        notes.append(_a("국면", "WARN", "매크로 국면이 리스크오프"))
    gp = _n(rec.get("gapPct"))
    if (rec.get("gapRisk") or {}).get("chase"):
        notes.append(_a("국면", "FAIL", "갭/급등 추격 구간"))
    elif gp is not None and abs(gp) > config.VERIFY_GAP_WARN:
        notes.append(_a("국면", "WARN", f"당일 갭 {gp:+.1f}%"))

    # 6) 반론 검토관 — 페르소나 증거에서 심각한 우려
    notes.extend(_contrarian(pick, row, scr.root))

    levels = [n["level"] for n in notes]
    verdict = "기각" if "FAIL" in levels else "조건부" if "WARN" in levels else "통과"
    return {"verdict": verdict, "grade": {"통과": "A", "조건부": "B", "기각": "C"}[verdict], "notes": notes,
            "fail": [n["text"] for n in notes if n["level"] == "FAIL"], "warn": [n["text"] for n in notes if n["level"] == "WARN"]}


def _contrarian(pick, row, root: Path) -> list[dict]:
    if row is None:
        return []
    try:
        import datetime as dt
        from personas import build_evidence, track_record, valuation
        m, code = pick["market"], Screener.norm(pick["market"], pick["code"])
        fund = _read(root / "docs" / "data" / "fundamentals" / m / f"{code}.json")
        cache = valuation.load_cache(root / "docs" / "data" / "valuation_us.json")
        ev = build_evidence(row, fund, today=dt.date.today(), context=track_record.context_for(m, root),
                            valuation=valuation.get(cache, code) if m == "us" else None)
        severe = [f["text"] for p in ev["personas"] if p["id"] in ("contrarian", "risk", "trend", "technical") for f in p["concerns"] if f["severity"] >= 3]
        if len(severe) >= config.VERIFY_SEVERE_CONCERNS:
            return [_a("반론", "WARN", f"심각한 우려 {len(severe)}건 — 예: {severe[0]}")]
        if severe:
            return [_a("반론", "OK", f"심각한 우려 1건: {severe[0]}")]
        return [_a("반론", "OK", "심각한 우려 없음")]
    except Exception as exc:  # noqa: BLE001
        return [_a("반론", "OK", f"페르소나 근거 생성 불가({type(exc).__name__}) — 반론 검토 생략")]
