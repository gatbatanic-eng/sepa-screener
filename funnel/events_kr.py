"""2단계: 최종 후보의 이벤트 공시 조회 (한국, OpenDART 공시검색).

숫자 깔때기를 통과한 후보만 종목별로 최근 12개월 공시 목록을 읽고, 제목으로 이벤트를 분류한다.
점수·관문에는 반영하지 않는다(검증 기록을 바꾸지 않기 위해). 사람이 후보를 읽을 때 쓰는 참고 정보다.

한계: 제목만 보므로 계약 금액·증자 규모·최대주주 변경 사유는 모른다. 링크로 원문을 확인해야 한다.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("funnel.events")

WINDOW_DAYS = 365
MAX_PAGES = 10
MAX_ITEMS = 8
WORKERS = 4
DART_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

# (분류, 심각도, 제목에 들어 있어야 할 문구 중 하나). 위에서부터 먼저 맞는 것을 쓴다.
# 심각도: risk(상장·감사·횡령 등), caution(희석·지배구조·계약 해지), info(계약·자사주·투자)
RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("contractCancel", "caution", ("공급계약해지", "공급계약종료")),
    ("contract", "info", ("단일판매", "공급계약체결")),
    ("dilution", "caution", ("유상증자결정", "전환사채권발행결정", "신주인수권부사채권발행결정", "교환사채권발행결정")),
    ("refixing", "caution", ("전환가액의조정", "행사가액의조정", "전환가액조정", "행사가액조정")),
    ("holderChange", "caution", ("최대주주변경",)),
    ("risk", "risk", ("관리종목", "상장폐지", "감사의견", "횡령", "배임", "불성실공시", "투자주의환기",
                      "매매거래정지", "회생절차", "부도발생", "상장적격성", "자본잠식")),
    ("buyback", "info", ("자기주식취득결정", "자기주식처분결정", "자기주식취득신탁")),
    ("capex", "info", ("유형자산취득결정", "타법인주식및출자증권취득결정", "신규시설투자")),
]
LABEL = {"contract": "계약", "contractCancel": "계약해지", "dilution": "증자·CB", "refixing": "전환가 조정",
         "holderChange": "최대주주변경", "risk": "위험공시", "buyback": "자사주", "capex": "투자"}
SEVERITY = {kind: sev for kind, sev, _ in RULES}


def classify_filing(title: str) -> str | None:
    """공시 제목 → 이벤트 분류. 정정·철회 공시는 원공시와 겹치므로 세지 않는다(None)."""
    t = re.sub(r"[\sㆍ·]", "", title or "")  # 공백·가운뎃점 표기 차이를 없앤다
    if "정정" in t or "철회" in t:
        return None
    for kind, _, words in RULES:
        if any(w in t for w in words):
            return kind
    return None


def _date(value: str) -> str | None:
    value = (value or "").strip()
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if re.fullmatch(r"\d{8}", value) else None


def summarize(rows: list[dict]) -> dict:
    """공시 행 → {counts, latest, items, severity, line}. 같은 접수번호는 한 번만 센다."""
    seen, found = set(), []
    for r in rows:
        rcept = str(r.get("rcept_no") or "")
        kind = classify_filing(r.get("report_nm", ""))
        if not kind or (rcept and rcept in seen):
            continue
        seen.add(rcept)
        found.append({"date": _date(r.get("rcept_dt")), "kind": kind, "severity": SEVERITY[kind],
                      "title": (r.get("report_nm") or "").strip(), "url": DART_VIEW + rcept if rcept else None})
    found.sort(key=lambda x: x["date"] or "", reverse=True)
    counts = Counter(x["kind"] for x in found)
    latest = {}
    for x in found:
        latest.setdefault(x["kind"], x["date"])
    order = {"risk": 0, "caution": 1, "info": 2}
    notable = sorted(found, key=lambda x: (order[x["severity"]], -int((x["date"] or "0").replace("-", "") or 0)))
    worst = min((order[x["severity"]] for x in found), default=None)
    return {
        "counts": dict(counts), "latest": latest, "items": notable[:MAX_ITEMS],
        "severity": None if worst is None else ("risk", "caution", "info")[worst],
        "line": line(counts),
    }


def line(counts: Counter | dict) -> str:
    """리포트 표에 넣는 한 줄 요약. 위험·주의 항목을 앞에 둔다."""
    order = ("risk", "contractCancel", "dilution", "refixing", "holderChange", "contract", "buyback", "capex")
    parts = []
    for kind in order:
        n = counts.get(kind, 0)
        if n:
            mark = "⚠" if SEVERITY[kind] in ("risk", "caution") else ""
            parts.append(f"{mark}{LABEL[kind]} {n}")
    return " · ".join(parts) or "—"


def fetch_filings(api, corp_code: str, today: dt.date) -> list[dict]:
    """한 종목의 최근 12개월 공시 목록 전체(유형 무관). corp_code를 주면 3개월 제한이 없다. 실패하면 RuntimeError."""
    start = today - dt.timedelta(days=WINDOW_DAYS)
    rows, page = [], 1
    while page <= MAX_PAGES:
        obj = api.request("list.json", corp_code=corp_code, bgn_de=start.strftime("%Y%m%d"),
                          end_de=today.strftime("%Y%m%d"), page_no=page, page_count=100)
        rows.extend(obj)
        if len(obj) < 100:
            break
        page += 1
    return rows


def collect(api, records: list[dict], today: dt.date) -> dict:
    """records 각각에 ``filings`` (summarize 결과 또는 조회 실패 시 None)를 붙인다. 반환: 상태 {asked, failed}."""
    targets = [r for r in records if r.get("corpCode")]

    def one(r: dict) -> bool:
        try:
            r["filings"] = summarize(fetch_filings(api, r["corpCode"], today))
            return True
        except RuntimeError as exc:
            log.warning("이벤트 공시 조회 실패 %s: %s", r["symbol"], exc)
            r["filings"] = None
            return False

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        ok = list(pool.map(one, targets))
    status = {"asked": len(targets), "failed": ok.count(False)}
    log.info("이벤트 공시 조회 %d종목 (실패 %d)", status["asked"], status["failed"])
    return status
