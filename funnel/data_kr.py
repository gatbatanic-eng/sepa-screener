"""한국(코스피+코스닥) 전 종목 → 깔때기 표준 레코드.

- 유니버스·시총: FinanceDataReader KRX 목록 (결측 시 screening 모듈의 네이버/pykrx 보완)
- 분기 실적: OpenDART 다중회사 주요계정 (100개사 단위 일괄 조회)
- 증자·CB·BW·분할 공시: OpenDART 공시검색 (주요사항보고, 3개월 단위)
- 정밀 조회(최종 후보만): 영업현금흐름, 발행주식총수 전년 대비
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from publish_fundamentals import Dart, account, number

log = logging.getLogger("funnel.kr")

REPORTS = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
MULTI_ACCOUNTS = {
    "revenue": {"매출액", "수익(매출액)", "매출액(수익)", "영업수익", "매출", "수익"},
    "operatingProfit": {"영업이익", "영업이익(손실)", "영업손익"},
    "netIncome": {"당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"},
    "liabilities": {"부채총계"},
    "equity": {"자본총계"},
}
FLOW = ("revenue", "operatingProfit", "netIncome")
DILUTION = ("유상증자결정", "전환사채권발행결정", "신주인수권부사채권발행결정")
SPLIT = ("회사분할결정", "물적분할")
MIN_MARCAP = 500e8  # 500억 원
DART_WORKERS = 4


def load_universe(min_marcap: float = MIN_MARCAP):
    import pandas as pd
    import screening
    from sepa.universe import is_preferred_kr, is_spac_kr

    listing = screening.fetch_stock_listing("KRX")
    listing = listing[listing["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])].copy()
    listing["Market"] = listing["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    listing = screening.enrich_kr_listing_market_data(listing)
    listing["Marcap"] = pd.to_numeric(listing["Marcap"], errors="coerce")
    listing = listing[listing["Marcap"] >= min_marcap]
    listing = listing[~listing["Name"].map(is_preferred_kr) & ~listing["Name"].map(is_spac_kr)]
    rows = []
    for r in listing.itertuples(index=False):
        code = str(r.Code).zfill(6)
        rows.append({
            "market": "kr", "symbol": code, "name": r.Name, "exchange": r.Market,
            "marcap": float(r.Marcap), "currency": "KRW",
            "yahoo": code + (".KS" if r.Market == "KOSPI" else ".KQ"),
        })
    log.info("KR 유니버스 %d종목 (시총 %.0f억 원 이상, 우선주·스팩 제외)", len(rows), min_marcap / 1e8)
    return rows


def _periods(today: dt.date, count: int = 11) -> list[tuple[int, int]]:
    """공시가 끝났을 가능성이 있는 최근 분기부터 과거로 ``count``개."""
    # 분기 종료 후 45일(연간 90일) 지나야 공시 완료로 본다.
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    while len(out) < count:
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        end = dt.date(y, q * 3, 28)
        lag = 90 if q == 4 else 45
        if (today - end).days >= lag:
            out.append((y, q))
    return sorted(out)


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def parse_multi(rows: list[dict]) -> dict[str, dict]:
    """다중회사 주요계정 응답 → {stock_code: {field: (thstrm, add)}} (연결 우선)."""
    by_code: dict[str, dict[str, dict]] = {}
    for r in rows:
        code = str(r.get("stock_code") or "").strip()
        if not code:
            continue
        basis = r.get("fs_div")
        name = _norm(r.get("account_nm"))
        for field, names in MULTI_ACCOUNTS.items():
            if name in names:
                slot = by_code.setdefault(code, {}).setdefault(basis, {})
                slot.setdefault(field, (number(r.get("thstrm_amount")), number(r.get("thstrm_add_amount"))))
    out = {}
    for code, bases in by_code.items():
        basis = "CFS" if "CFS" in bases else "OFS"
        out[code] = {"basis": basis, **bases[basis]}
    return out


def build_quarters(raw: dict[tuple[int, int], dict]) -> list[dict]:
    """보고서별 값 → 3개월 분기값. 4분기 = 연간 − 3분기 누적(같은 기준일 때만)."""
    quarters = []
    for (y, q), rec in sorted(raw.items()):
        row = {"year": y, "quarter": q, "basis": rec["basis"]}
        for field in MULTI_ACCOUNTS:
            cur = rec.get(field, (None, None))[0]
            if q == 4 and field in FLOW:
                q3 = raw.get((y, 3))
                ytd = q3.get(field, (None, None))[1] if q3 and q3["basis"] == rec["basis"] else None
                cur = cur - ytd if cur is not None and ytd is not None else None
            if cur is None and q in (1, 2) and field in FLOW:
                cur = _derive_from_next(raw, y, q, field, rec["basis"])
            row[field] = cur
        quarters.append(row)
    return quarters


def _derive_from_next(raw: dict, y: int, q: int, field: str, basis: str) -> float | None:
    """계정명이 달라 빠진 분기값을 다음 분기 누적 − 다음 분기 3개월 − 앞선 분기로 보충(같은 기준일 때만)."""
    nxt = raw.get((y, q + 1))
    if not nxt or nxt["basis"] != basis:
        return None
    three, ytd = nxt.get(field, (None, None))
    if three is None or ytd is None:
        return None
    earlier = [raw.get((y, k), {}).get(field, (None, None))[0] for k in range(1, q)]
    if any(v is None for v in earlier):
        return None
    return ytd - three - sum(earlier)


def _cache_path(cache_dir: Path, year: int, q: int) -> Path:
    return cache_dir / f"{year}Q{q}.json.gz"


def _load_cache(path: Path) -> dict[str, dict] | None:
    if not path.exists():
        return None
    data = json.loads(gzip.open(path, "rt", encoding="utf-8").read())
    return {code: {k: (tuple(v) if isinstance(v, list) else v) for k, v in rec.items()} for code, rec in data.items()}


def _save_cache(path: Path, parsed: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, separators=(",", ":"))


def _fetch_period(api: Dart, corp_list: list[str], corp_to_code: dict, year: int, q: int) -> tuple[dict[str, dict], int]:
    """한 분기 보고서를 100개사 단위로 병렬 조회. 반환: (파싱 결과, 실패 배치 수)."""
    def one(batch):
        rows = api.request("fnlttMultiAcnt.json", corp_code=",".join(batch), bsns_year=year, reprt_code=REPORTS[q])
        for r in rows:
            if not r.get("stock_code") and r.get("corp_code") in corp_to_code:
                r["stock_code"] = corp_to_code[r["corp_code"]]
        return parse_multi(rows)

    parsed: dict[str, dict] = {}
    failed = 0
    batches = [corp_list[i:i + 100] for i in range(0, len(corp_list), 100)]
    with ThreadPoolExecutor(max_workers=DART_WORKERS) as pool:
        for fut in as_completed([pool.submit(one, b) for b in batches]):
            try:
                parsed.update(fut.result())
            except RuntimeError as exc:
                failed += 1
                log.warning("DART 다중계정 %s년 %d분기 배치 실패: %s", year, q, exc)
    return parsed, failed


def fetch_quarters(api: Dart, corps: dict[str, str], codes: list[str], today: dt.date,
                   cache_dir: Path | None = None) -> dict[str, list[dict]]:
    """분기 실적. 확정된 과거 분기는 캐시를 쓰고, 최근 2개 분기와 캐시 없는 분기만 조회한다."""
    periods = _periods(today)
    wanted = sorted({(y, q) for y, q in periods} | {(y, 3) for y, q in periods if q == 4})
    refresh = set(periods[-2:])
    selected = {c: corps[c] for c in codes if c in corps}
    corp_list = sorted(selected.values())
    corp_to_code = {v: k for k, v in selected.items()}
    raw: dict[str, dict[tuple[int, int], dict]] = {}
    for year, q in wanted:
        cached = _load_cache(_cache_path(cache_dir, year, q)) if cache_dir else None
        missing = [c for c in corp_list if corp_to_code[c] not in (cached or {})]
        if cached is not None and (year, q) not in refresh and len(missing) < 0.1 * len(corp_list):
            parsed, source = cached, "캐시"
        else:
            parsed, failed = _fetch_period(api, corp_list, corp_to_code, year, q)
            source = "조회"
            if cached:
                parsed = {**cached, **parsed}
            if cache_dir and not failed:
                _save_cache(_cache_path(cache_dir, year, q), parsed)
        for code, rec in parsed.items():
            if code in selected:
                raw.setdefault(code, {})[(year, q)] = rec
        log.info("DART 다중계정 %s년 %d분기 %s (누적 %d개사)", year, q, source, len(raw))
    return {code: build_quarters(v) for code, v in raw.items()}


def classify_disclosure(title: str) -> str | None:
    t = _norm(title)
    if "정정" in t or "철회" in t:
        return None
    if any(k in t for k in DILUTION):
        return "dilution"
    if any(k in t for k in SPLIT) and "분할합병" not in t:
        return "split"
    return None


def fetch_events(api: Dart, today: dt.date) -> dict[str, Counter]:
    """최근 12개월 주요사항보고 → 종목별 {dilution, split} 건수."""
    counts: dict[str, Counter] = {}
    end = today
    for _ in range(4):  # corp_code 없이 조회하면 기간이 3개월로 제한된다
        start = end - dt.timedelta(days=90)
        page = 1
        while True:
            try:
                obj = api.request("list.json", bgn_de=start.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                                  pblntf_ty="B", page_no=page, page_count=100)
            except RuntimeError as exc:
                log.warning("DART 공시검색 실패 %s~%s p%d: %s", start, end, page, exc)
                break
            for r in obj:
                kind = classify_disclosure(r.get("report_nm", ""))
                code = str(r.get("stock_code") or "").strip()
                if kind and code:
                    counts.setdefault(code, Counter())[kind] += 1
            if len(obj) < 100:
                break
            page += 1
        end = start - dt.timedelta(days=1)
    log.info("DART 주요사항 공시: 증자·CB·BW/분할 해당 %d개사", len(counts))
    return counts


def fetch_industry(api: Dart, corp_code: str) -> str | None:
    """DART 기업개황의 업종코드(KSIC)."""
    try:
        return (api.request("company.json", corp_code=corp_code) or {}).get("induty_code")
    except RuntimeError:
        return None


def enrich_detail(api: Dart, corp_code: str, year: int, quarter: int) -> dict:
    """최종 후보용: 발행주식총수(전년 동기 대비)와 영업현금흐름/순이익(누적 기준)."""
    report = REPORTS[quarter]
    out: dict = {"sharesNow": None, "sharesYearAgo": None, "ocfToNi": None, "ocfTTMPositive": None}

    def shares(y: int):
        try:
            rows = api.request("stockTotqySttus.json", corp_code=corp_code, bsns_year=y, reprt_code=report)
        except RuntimeError:
            return None
        for label in ("합계", "보통주"):
            for r in rows:
                if _norm(r.get("se")) == label:
                    val = number(r.get("istc_totqy"))
                    if val:
                        return val
        return None

    out["sharesNow"], out["sharesYearAgo"] = shares(year), shares(year - 1)
    for fs in ("CFS", "OFS"):
        try:
            rows = api.request("fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=year, reprt_code=report, fs_div=fs)
        except RuntimeError:
            rows = []
        if not rows:
            continue
        ocf = number(account(rows, "operatingCashFlow").get("thstrm_amount"))
        ni_row = account(rows, "netIncome")
        ni = number(ni_row.get("thstrm_add_amount")) if quarter in (2, 3) else None
        ni = ni if ni is not None else number(ni_row.get("thstrm_amount"))
        if ocf is not None:
            out["ocfTTMPositive"] = ocf > 0
            if ni is not None and ni > 0:
                out["ocfToNi"] = ocf / ni
        break
    return out


def collect(api: Dart, today: dt.date, limit: int | None = None, cache_dir: Path | None = None) -> tuple[list[dict], dict[str, str]]:
    universe = load_universe()
    if limit:
        universe = sorted(universe, key=lambda r: -r["marcap"])[:limit]
    corps = api.corporations()
    quarters = fetch_quarters(api, corps, [r["symbol"] for r in universe], today, cache_dir)
    events = fetch_events(api, today)
    for r in universe:
        r["quarters"] = quarters.get(r["symbol"], [])
        ev = events.get(r["symbol"], Counter())
        r["dilutionEvents12m"] = ev.get("dilution", 0)
        r["splitEvents12m"] = ev.get("split", 0)
        r["sharesNow"] = r["sharesYearAgo"] = r["ocfToNi"] = r["ocfTTMPositive"] = None
        r["corpCode"] = corps.get(r["symbol"])
    return universe, corps
