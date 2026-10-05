"""KRX 종목 목록(data.krx.co.kr)이 점검·장애로 막혔을 때 쓰는 네이버 금융 대체 목록.

이 파일은 하위 시스템끼리 코드를 import하지 않는 이 저장소 관례에 맞춰 같은 내용으로 복사해 둔다
(루트, technical_signals/, momentum_signals/, range_vrebound/src/data/).
사본이 달라지지 않도록 tests/test_naver_listing.py가 네 파일이 같은지 검사한다. 고칠 때는 모두 같이 고친다.

네이버 모바일 시총 순위(KOSPI·KOSDAQ)에서 종목코드·이름·시장·시총·거래대금을 읽는다.
시총·거래대금은 표시 문자열을 파싱한 값이라 순위용으로만 쓴다(절대값은 KRX 목록과 단위가 다를 수 있다).
"""
from __future__ import annotations

import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

MIN_LISTED = 1500          # 코스피+코스닥 상장 종목은 2,500개 안팎이다. 이보다 적으면 응답 이상으로 본다.
PAGE_SIZE = 100
MAX_PAGES = 30
SOURCE = "naver-fallback"
_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "Referer": "https://m.stock.naver.com/",
}
_CODE_KEYS = ("itemCode", "itemcode", "code", "stockCode")
_NAME_KEYS = ("stockName", "itemName", "name", "companyName")
_MARCAP_KEYS = ("marketValue", "marketCap", "marketValueHangeul", "market_sum", "marketSum")
_AMOUNT_KEYS = ("accumulatedTradingValue", "tradingValue")


def _first(row: dict, keys) -> object:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def parse_number(value) -> float:
    """숫자 또는 '1조 2,345억' 같은 한국 단위 문자열을 원 단위 숫자로. 못 읽으면 NaN."""
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or text in ("-", "N/A"):
        return float("nan")
    jo, eok = re.search(r"([0-9.]+)조", text), re.search(r"([0-9.]+)억", text)
    if jo or eok:
        return (float(jo.group(1)) * 1e12 if jo else 0.0) + (float(eok.group(1)) * 1e8 if eok else 0.0)
    cleaned = re.sub(r"[^0-9.+-]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return float("nan")


def find_rows(payload) -> list[dict]:
    """응답 스키마가 바뀌어도 종목코드가 든 가장 큰 배열을 찾는다."""
    found: list[list[dict]] = []

    def walk(obj):
        if isinstance(obj, list):
            rows = [x for x in obj if isinstance(x, dict)]
            if rows and any(any(k in row for k in _CODE_KEYS) for row in rows):
                found.append(rows)
            for item in obj:
                walk(item)
        elif isinstance(obj, dict):
            for value in obj.values():
                walk(value)

    walk(payload)
    return max(found, key=len) if found else []


def parse_page(payload, market: str) -> list[dict]:
    out = []
    for row in find_rows(payload):
        code = str(_first(row, _CODE_KEYS) or "").split(".")[0].zfill(6)
        name = _first(row, _NAME_KEYS)
        if not (code.isdigit() and len(code) == 6) or not name:
            continue
        out.append({"Code": code, "Name": str(name).strip(), "Market": market,
                    "Marcap": parse_number(_first(row, _MARCAP_KEYS)),
                    "Amount": parse_number(_first(row, _AMOUNT_KEYS))})
    return out


def naver_kr_listing(get=None) -> pd.DataFrame:
    """코스피·코스닥 전체 목록. 컬럼: Code, Name, Market, Marcap, Amount. 실패하면 예외."""
    if get is None:
        import requests
        get = requests.get
    seen: dict[str, dict] = {}
    pages: dict[str, int] = {}
    last_keys: list[str] = []
    for market in ("KOSPI", "KOSDAQ"):
        for page in range(1, MAX_PAGES + 1):
            response = get(_URL.format(market=market), params={"page": page, "pageSize": PAGE_SIZE},
                           headers=_HEADERS, timeout=15)
            response.raise_for_status()
            payload = response.json()
            raw = find_rows(payload)           # 파싱에서 걸러진 행이 있어도 페이지가 찼는지는 원본 행 수로 판단한다
            rows = parse_page(payload, market)
            new = [r for r in rows if r["Code"] not in seen]
            seen.update({r["Code"]: r for r in new})
            pages[market] = page
            if raw:
                last_keys = sorted(raw[0])[:12]
            if not new or len(raw) < PAGE_SIZE:
                break
    if len(seen) < MIN_LISTED:
        raise RuntimeError(f"네이버 한국 종목 목록이 {len(seen)}종목뿐입니다(최소 {MIN_LISTED}) — 응답 형식 변경 가능성 "
                           f"(읽은 페이지 {pages}, 행 키 {last_keys})")
    frame = pd.DataFrame(list(seen.values()))
    frame.attrs["source"] = SOURCE
    logger.warning("네이버 대체 목록 %d종목 사용 (코스피 %d, 코스닥 %d)", len(frame),
                   int((frame["Market"] == "KOSPI").sum()), int((frame["Market"] == "KOSDAQ").sum()))
    return frame


if __name__ == "__main__":  # python naver_listing.py : 대체 목록이 살아 있는지 점검 (종료 코드로 알림)
    logging.basicConfig(level=logging.INFO)
    listing = naver_kr_listing()
    named = int(listing["Name"].astype(str).str.len().gt(0).sum())
    print(f"naver listing OK: {len(listing)}종목, 이름 {named}개, 시총 유효 {int((listing['Marcap'] > 0).sum())}개")
