"""종목별 분기 재무 → 기준일 시점의 TTM. 공시일(filedAt)이 기준일 이전인 분기만 쓴다(미래 정보 누수 방지).

한국(DART): 분기 레코드는 손익이 단일 분기, 현금흐름은 연초 누적, 재무상태는 기말. 미국(SEC): 모두 단일 분기(현금흐름은 누적 차감)·기말.
반환: (dict | None, 사유). 결측은 None 으로 두고 0으로 채우지 않는다.
"""
from __future__ import annotations

import datetime as dt
import math

from . import config


def _day(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _back(y: int, q: int, n: int) -> tuple[int, int]:
    i = y * 4 + (q - 1) - n
    return i // 4, i % 4 + 1


def _period_end(y: int, q: int) -> dt.date:
    return dt.date(y + q // 4, q % 4 * 3 + 1, 1) - dt.timedelta(days=1)


def _sum(vals):
    return None if any(v is None for v in vals) else float(sum(vals))


def ttm_kr(quarters: list[dict], asof: str):
    known = {}
    for q in quarters:
        f = q.get("filedAt")
        if f and f <= asof and isinstance(q.get("year"), int) and q.get("quarter") in (1, 2, 3, 4):
            known[(q["year"], q["quarter"])] = q
    if not known:
        return None, "no_fin"
    ly, lq = max(known)
    keys = [_back(ly, lq, i) for i in (3, 2, 1, 0)]
    if any(k not in known for k in keys):
        return None, "no_fin"
    rows = [known[k] for k in keys]
    if len({r.get("basis") for r in rows}) != 1:
        return None, "mixed_basis"
    if (_day(asof) - _period_end(ly, lq)).days > config.MAX_FIN_AGE_DAYS:
        return None, "stale_fin"
    ni, op = _sum([num(r.get("netIncome")) for r in rows]), _sum([num(r.get("operatingProfit")) for r in rows])
    if ni is None or op is None:
        return None, "no_fin"
    last = known[(ly, lq)]
    ocf_now = num(last.get("operatingCashFlow"))
    if lq == 4:
        ocf = ocf_now
    else:
        prev_year, prev_same = known.get((ly - 1, 4)), known.get((ly - 1, lq))
        a = num(prev_year.get("operatingCashFlow")) if prev_year else None
        b = num(prev_same.get("operatingCashFlow")) if prev_same else None
        ocf = ocf_now + a - b if None not in (ocf_now, a, b) else None
    liab, eq = num(last.get("liabilities")), num(last.get("equity"))
    if ocf is None or liab is None or eq is None:
        return None, "no_fin"
    return {"ni": ni, "opinc": op, "ocf": ocf, "capex": None, "liabilities": liab, "equity": eq,
            "assets": liab + eq, "period": last.get("period"), "filedAt": last.get("filedAt")}, None


def ttm_us(quarters: list[dict], asof: str):
    by_end = {}
    for q in quarters:
        f, e = q.get("filedAt"), q.get("periodEnd")
        if f and e and f <= asof and e <= asof:
            by_end[e] = q
    known = [by_end[e] for e in sorted(by_end)]
    if len(known) < 4:
        return None, "no_fin"
    last4 = known[-4:]
    gaps = [(_day(b["periodEnd"]) - _day(a["periodEnd"])).days for a, b in zip(last4, last4[1:])]
    if any(not 70 <= g <= 110 for g in gaps):
        return None, "no_fin"
    last = last4[-1]
    if (_day(asof) - _day(last["periodEnd"])).days > config.MAX_FIN_AGE_DAYS:
        return None, "stale_fin"
    ni, op = _sum([num(r.get("netIncome")) for r in last4]), _sum([num(r.get("operatingProfit")) for r in last4])
    ocf = _sum([num(r.get("operatingCashFlow")) for r in last4])
    capex = _sum([num(r.get("capex")) for r in last4])   # 없으면 None → 현금흐름수익률은 영업현금흐름만 씀
    liab, eq = num(last.get("liabilities")), num(last.get("equity"))
    if None in (ni, op, ocf, liab, eq):
        return None, "no_fin"
    return {"ni": ni, "opinc": op, "ocf": ocf, "capex": capex, "liabilities": liab, "equity": eq,
            "assets": liab + eq, "period": last.get("period"), "filedAt": last.get("filedAt")}, None


def ttm(market: str, quarters: list[dict], asof: str):
    return (ttm_kr if market == "kr" else ttm_us)(quarters, asof)


def shares_us(series: list[dict], asof: str):
    """공시일 이전 가장 최근 발행주식 수 [{'end','filedAt','val'}, ...]. 없으면 None."""
    ok = [s for s in series if s.get("filedAt") and s["filedAt"] <= asof and num(s.get("val"))]
    if not ok:
        return None
    last = max(ok, key=lambda s: (s["filedAt"], s.get("end", "")))
    return float(last["val"])
