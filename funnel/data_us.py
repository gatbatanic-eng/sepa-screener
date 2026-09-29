"""미국(NYSE·나스닥) 전 종목 → 깔때기 표준 레코드.

- 유니버스: SEC company_tickers_exchange (거래소 상장 보통주, OTC 제외)
- 분기 실적: SEC XBRL frames API — 개념·분기별로 전 기업 값을 한 번에 받는다.
  10-K만 내는 4분기는 연간 − (1~3분기 합)으로 계산한다(같은 태그일 때만).
- 시총: 주간 종가 × 희석 가중평균 주식 수(추정치, 표에 '추정'으로 표시)
- 정밀 조회(최종 후보만): companyfacts로 영업현금흐름/순이익, 발행주식 수 전년 대비
"""
from __future__ import annotations

import datetime as dt
import logging

from publish_us_fundamentals import fetch, normalize

log = logging.getLogger("funnel.us")

EXCHANGES = {"Nasdaq", "NYSE", "NYSE American"}
MIN_MARCAP = 3e8  # 3억 달러
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues", "SalesRevenueNet",
]
FLOW_TAGS = {"operatingProfit": ["OperatingIncomeLoss"], "netIncome": ["NetIncomeLoss", "ProfitLoss"]}
SHARES_TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"


def load_universe() -> list[dict]:
    data = fetch("https://www.sec.gov/files/company_tickers_exchange.json")
    fields = data["fields"]
    seen, rows = set(), []
    for rec in data["data"]:
        r = dict(zip(fields, rec))
        cik, ticker = int(r["cik"]), str(r["ticker"]).upper()
        if r.get("exchange") not in EXCHANGES or cik in seen:
            continue
        seen.add(cik)
        rows.append({"market": "us", "symbol": ticker, "name": r["name"], "exchange": r["exchange"],
                     "cik": cik, "currency": "USD", "yahoo": ticker.replace(".", "-")})
    log.info("US 유니버스 %d종목 (NYSE·나스닥, CIK당 1개)", len(rows))
    return rows


def _periods(today: dt.date, count: int = 11) -> list[tuple[int, int]]:
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    while len(out) < count:
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        if (today - dt.date(y, q * 3, 28)).days >= 45:
            out.append((y, q))
    return sorted(out)


def _frame(tag: str, unit: str, period: str) -> dict[int, float]:
    try:
        data = fetch(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/{period}.json")
    except RuntimeError:
        return {}
    return {int(d["cik"]): float(d["val"]) for d in data.get("data", []) if isinstance(d.get("val"), (int, float))}


def quarterly_series(tag: str, periods: list[tuple[int, int]], unit: str = "USD") -> dict[int, dict[tuple[int, int], float]]:
    """태그 하나의 분기값. 4분기 누락은 연간 − 1~3분기로 보충."""
    out: dict[int, dict[tuple[int, int], float]] = {}
    for y, q in periods:
        for cik, val in _frame(tag, unit, f"CY{y}Q{q}").items():
            out.setdefault(cik, {})[(y, q)] = val
    for y in sorted({y for y, q in periods if q == 4}):
        annual = _frame(tag, unit, f"CY{y}")
        for cik, total in annual.items():
            got = out.setdefault(cik, {})
            if (y, 4) in got:
                continue
            parts = [got.get((y, q)) for q in (1, 2, 3)]
            if None not in parts:
                got[(y, 4)] = total - sum(parts)
    return out


def best_tag_series(tags: list[str], periods) -> dict[int, dict[tuple[int, int], float]]:
    """기업마다 가장 많은 분기를 채우는 태그 하나만 쓴다(태그 혼용으로 성장률 왜곡 방지)."""
    per_tag = [quarterly_series(t, periods) for t in tags]
    out = {}
    for cik in set().union(*[s.keys() for s in per_tag]):
        best = max(per_tag, key=lambda s: (len(s.get(cik, {})), -per_tag.index(s)))
        out[cik] = best.get(cik, {})
    return out


def instant_latest(tag: str, periods) -> dict[int, float]:
    out: dict[int, float] = {}
    for y, q in periods[-2:]:
        out.update(_frame(tag, "USD", f"CY{y}Q{q}I"))
    return out


def collect(today: dt.date, limit: int | None = None) -> list[dict]:
    universe = load_universe()
    periods = _periods(today)
    revenue = best_tag_series(REVENUE_TAGS, periods)
    if limit:
        universe = sorted(universe, key=lambda r: -len(revenue.get(r["cik"], {})))[:limit]
    flows = {k: best_tag_series(v, periods) for k, v in FLOW_TAGS.items()}
    shares = quarterly_series(SHARES_TAG, periods, unit="shares")
    liabilities = instant_latest("Liabilities", periods)
    total = instant_latest("LiabilitiesAndStockholdersEquity", periods)
    equity = instant_latest("StockholdersEquity", periods)
    for r in universe:
        cik = r["cik"]
        rows = []
        for key in periods:
            rows.append({"year": key[0], "quarter": key[1],
                         "revenue": revenue.get(cik, {}).get(key),
                         "operatingProfit": flows["operatingProfit"].get(cik, {}).get(key),
                         "netIncome": flows["netIncome"].get(cik, {}).get(key),
                         "liabilities": None, "equity": None})
        eq = equity.get(cik)
        li = liabilities.get(cik)
        if li is None and eq is not None and cik in total:
            li = total[cik] - eq
        if rows:
            rows[-1]["equity"], rows[-1]["liabilities"] = eq, li
        r["quarters"] = rows
        sh = shares.get(cik, {})
        latest = max((k for k in sh if (k[0] - 1, k[1]) in sh), default=None)
        r["sharesNow"] = sh.get(latest) if latest else (sh[max(sh)] if sh else None)
        r["sharesYearAgo"] = sh.get((latest[0] - 1, latest[1])) if latest else None
        r["dilutionEvents12m"] = r["splitEvents12m"] = None
        r["ocfToNi"] = r["ocfTTMPositive"] = None
    log.info("SEC frames 수집 완료: 매출 보유 %d개사", sum(1 for r in universe if any(q["revenue"] for q in r["quarters"])))
    return universe


def fetch_sic(cik: int) -> str | None:
    """SEC submissions의 SIC 업종코드."""
    try:
        return str(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").get("sic") or "") or None
    except RuntimeError:
        return None


def companyfacts_quarters(normalized: list[dict]) -> list[dict]:
    """companyfacts 정규화 분기(실제 회계기간) → 표준 분기 레코드. 분기 끝 달로 달력 분기를 정한다."""
    out: dict[tuple[int, int], dict] = {}
    for rec in normalized:
        end = dt.date.fromisoformat(rec["periodEnd"])
        # 월초에 끝나는 회계분기(예: 8월 2일)는 직전 달 분기로 본다
        ref = end - dt.timedelta(days=10)
        key = (ref.year, (ref.month - 1) // 3 + 1)
        out[key] = {"year": key[0], "quarter": key[1], "revenue": rec.get("revenue"),
                    "operatingProfit": rec.get("operatingProfit"), "netIncome": rec.get("netIncome"),
                    "liabilities": rec.get("liabilities"), "equity": rec.get("equity")}
    return [out[k] for k in sorted(out)]


def enrich_detail(cik: int, today: dt.date) -> dict:
    """최종 후보용 companyfacts 정밀 조회."""
    out = {"ocfToNi": None, "ocfTTMPositive": None, "sharesNow": None, "sharesYearAgo": None}
    try:
        data = fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    except RuntimeError:
        return out
    normalized = normalize(data, today.isoformat())
    cf_quarters = companyfacts_quarters(normalized)
    if len(cf_quarters) >= 5:
        out["quarters"] = cf_quarters
    quarters = normalized[-4:]
    ocf = [q.get("operatingCashFlow") for q in quarters]
    ni = [q.get("netIncome") for q in quarters]
    if len(quarters) == 4 and None not in ocf:
        out["ocfTTMPositive"] = sum(ocf) > 0
        if None not in ni and sum(ni) > 0:
            out["ocfToNi"] = sum(ocf) / sum(ni)
    facts = data.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    facts = sorted((f for f in facts if f.get("end") and isinstance(f.get("val"), (int, float))), key=lambda f: f["end"])
    if facts:
        last = facts[-1]
        end = dt.date.fromisoformat(last["end"])
        prior = [f for f in facts if 330 <= (end - dt.date.fromisoformat(f["end"])).days <= 400]
        out["sharesNow"] = float(last["val"])
        if prior:
            out["sharesYearAgo"] = float(min(prior, key=lambda f: abs((end - dt.date.fromisoformat(f["end"])).days - 365))["val"])
    return out
