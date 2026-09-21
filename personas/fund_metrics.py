"""personas/fund_metrics.py — DART/SEC 분기 재무 JSON → 페르소나 규칙이 쓰는 지표 dict.

입력은 ``docs/data/fundamentals/{kr,us}/{code}.json`` 형태(분기 리스트)이며, 여기서는
"공시된 실제 값"만 계산한다. 컨센서스·추정치는 다루지 않는다.

한계(호출부가 그대로 사용자에게 알려야 하는 것들)
- PER: 미국은 최근 4분기 EPS 합(모두 존재할 때만), 한국은 시가총액/최근 4분기 순이익.
  분기 중 하나라도 결측이면 계산하지 않는다(추정으로 채우지 않음).
- PBR(한국만): 시가총액/최근 자본총계. 자본총계에 비지배지분이 섞여 있을 수 있다.
- 업종 평균·컨센서스는 없다. 절대 기준으로만 해석 가능.
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
from typing import Any, Optional

CONSEC_GROWTH_PCT = 15.0  # 연속 성장 분기 판정 기준(매출 YoY %)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def quarter_end(q: dict, market: str) -> Optional[dt.date]:
    if market == "us":
        s = q.get("periodEnd")
        try:
            return dt.date.fromisoformat(s) if s else None
        except ValueError:
            return None
    y, n = q.get("year"), q.get("quarter")
    if not (isinstance(y, int) and isinstance(n, int) and 1 <= n <= 4):
        return None
    m = n * 3
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def marcap_krw(v: Any) -> Optional[float]:
    """한국 시가총액을 원 단위로 정규화. 네이버 목록은 억원 단위(예: 삼성전자 ≈ 1.6e7)로 오기 때문에
    1e9 미만이면 억원으로 보고 1e8을 곱한다(상장사 시총은 원 단위로 항상 1e9를 넘는다)."""
    x = _num(v)
    if x is None or x <= 0:
        return None
    return x * 1e8 if x < 1e9 else x


def _yoy(q: dict, key: str) -> tuple[Optional[float], str]:
    d = q.get(key + "YoY") or {}
    return _num(d.get("pct")), str(d.get("label") or "")


def fund_metrics(fund: Optional[dict], market: str, *, close: Optional[float] = None,
                 marcap: Any = None, today: Optional[dt.date] = None) -> Optional[dict]:
    """재무 지표 묶음. 재무 데이터가 없거나 사용할 수 없으면 None."""
    if not fund or fund.get("status") not in (None, "ok"):
        return None
    market = market.lower()
    qs = [q for q in (fund.get("quarters") or []) if quarter_end(q, market)]
    qs.sort(key=lambda q: quarter_end(q, market))
    if not qs:
        return None

    today = today or dt.date.today()
    latest = qs[-1]
    end = quarter_end(latest, market)
    m: dict[str, Any] = {
        "latestPeriod": end.isoformat(),
        "ageDays": (today - end).days,
        "nQuarters": len(qs),
        "basis": latest.get("basis"),
        "notes": [],
    }

    last4 = qs[-4:]
    contiguous = len(last4) == 4 and all(
        70 <= (quarter_end(b, market) - quarter_end(a, market)).days <= 110
        for a, b in zip(last4, last4[1:])
    )
    ttm: dict[str, Optional[float]] = {}
    if contiguous:
        for k in ("revenue", "operatingProfit", "netIncome", "operatingCashFlow", "freeCashFlow", "eps"):
            vals = [_num(q.get(k)) for q in last4]
            ttm[k] = sum(vals) if all(v is not None for v in vals) else None
    else:
        m["notes"].append("최근 4분기가 연속이 아니어서 TTM 합계를 계산하지 않음")
    m["ttm"] = ttm

    m["opMargin"] = _num(latest.get("operatingMargin"))
    if m["opMargin"] is None:
        op, rev = _num(latest.get("operatingProfit")), _num(latest.get("revenue"))
        if op is not None and rev:
            m["opMargin"] = op / rev * 100.0
    m["opMarginChangePP"] = None
    if len(qs) >= 5:
        yago = qs[-5]
        gap_days = (end - quarter_end(yago, market)).days
        ym = _num(yago.get("operatingMargin"))
        if 350 <= gap_days <= 380 and ym is not None and m["opMargin"] is not None:
            m["opMarginChangePP"] = m["opMargin"] - ym

    de = _num(latest.get("debtToEquity"))
    if de is None:
        liab, eq = _num(latest.get("liabilities")), _num(latest.get("equity"))
        if liab is not None and eq and eq > 0:
            de = liab / eq * 100.0
    m["debtToEquity"] = de  # 총부채/자본총계(%) — 순차입 기준이 아니다

    ni, ocf = ttm.get("netIncome"), ttm.get("operatingCashFlow")
    m["ocfToNi"] = (ocf / ni) if (ni and ni > 0 and ocf is not None) else None
    m["fcfTtm"] = ttm.get("freeCashFlow")

    m["yoy"] = {}
    for key in ("revenue", "operatingProfit", "netIncome", "eps"):
        pct, label = _yoy(latest, key)
        m["yoy"][key] = {"pct": pct, "label": label}
    m["revYoySeries"] = [_yoy(q, "revenue")[0] for q in qs[-4:]]
    m["opYoySeries"] = [_yoy(q, "operatingProfit")[0] for q in qs[-4:]]
    consec = 0
    for q in reversed(qs):
        pct = _yoy(q, "revenue")[0]
        if pct is not None and pct >= CONSEC_GROWTH_PCT:
            consec += 1
        else:
            break
    m["consecRevGrowth"] = consec

    val: dict[str, Any] = {}
    if market == "us":
        eps = ttm.get("eps")
        if close and eps is not None:
            if eps > 0:
                val["per"] = close / eps
            else:
                val["perNote"] = "최근 4분기 EPS 합계가 0 이하"
        elif eps is None:
            val["perNote"] = "최근 4분기 EPS가 하나라도 결측이라 PER 산출 불가"
    else:
        mc = marcap_krw(marcap)
        if mc is None:
            val["perNote"] = "시가총액 데이터 없음"
        elif ni is not None and ni > 0:
            val["per"] = mc / ni
            eq = _num(latest.get("equity"))
            if eq and eq > 0:
                val["pbr"] = mc / eq
        elif ni is not None:
            val["perNote"] = "최근 4분기 순이익 합계가 0 이하"
        else:
            val["perNote"] = "최근 4분기 순이익 결측"
    m["valuation"] = val
    return m
