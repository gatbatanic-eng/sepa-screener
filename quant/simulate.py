"""월 리밸런싱 가상 계좌. 신호일(S) 종가까지의 정보로 순위를 매기고 다음 거래일(E) 종가에 체결한다. 주문은 하지 않는다.

신규 편입은 매도 후 현금을 편입 종목 수로 균등 배분(상한 = E 직전 평가금액 ÷ TOTAL_SLOTS × CASH_SPLIT_CAP). 유지 종목은 매매하지 않는다(가중치 되돌리기 없음). 시장별 비용은 편도로 매 체결에 부과.
"""
from __future__ import annotations

from . import config, factors
from . import portfolio as pf
from .book import PriceBook

MARKET_ORDER = {"kr": 0, "us": 1}


def _value(pos: dict, book: PriceBook, date: str) -> float:
    total = 0.0
    for sym, p in pos.items():
        c = book.close_on_or_before(sym, date)
        total += p["shares"] * (c[1] if c else p["entryPrice"])
    return total


def simulate(events: list[dict], book: PriceBook, calendar: list[str], *, variant: str, capital: float = config.INITIAL_CAPITAL,
             freeze_after: str | None = None) -> dict:
    """events: [{market, signal, exec, skipped, df, industry}] — exec 오름차순. calendar: 평가 가능한 모든 거래일."""
    events = sorted(events, key=lambda e: (e["exec"], MARKET_ORDER[e["market"]]))
    cash, pos, trades, snaps, pre = float(capital), {}, [], [], []
    dates = sorted({e["exec"] for e in events if not freeze_after or e["exec"] <= freeze_after})
    decisions = []
    for d in dates:
        eq_before = cash + _value(pos, book, d)
        pre.append((d, eq_before))
        todo = [e for e in events if e["exec"] == d]
        plans = []
        for e in todo:
            m = e["market"]
            held = {s for s, p in pos.items() if p["market"] == m}
            if e["skipped"]:
                plans.append((e, None))
                continue
            ranked = factors.order(e["df"], variant, e["signal"], m)
            sel = pf.select(ranked, e["industry"], held, config.SLOTS[m])
            plans.append((e, sel))
            decisions.append({"market": m, "signal": e["signal"], "exec": d, "variant": variant, **sel})
        for e, sel in plans:                       # 매도 먼저(같은 날 두 시장의 현금을 함께 쓴다)
            if not sel:
                continue
            for s in sel["exits"]:
                p = pos.pop(s, None)
                c = book.close_on_or_before(s, d)
                if p is None:
                    continue
                px = c[1] if c else p["entryPrice"]
                cash += p["shares"] * px * (1 - config.COST_BPS[p["market"]] / 1e4)
                trades.append({"symbol": s, "market": p["market"], "entryDate": p["entryDate"], "entryPrice": round(p["entryPrice"], 4),
                               "exitDate": d, "exitPrice": round(px, 4), "returnPct": round((px / p["entryPrice"] - 1) * 100, 2)})
        remaining = sum(len(sel["entries"]) for _, sel in plans if sel)
        for e, sel in plans:
            if not sel:
                continue
            m = e["market"]
            for s in sel["entries"]:
                fill = book.first_on_or_after(s, d, "9999-12-31")
                # 신규 편입은 현금을 균등 배분한다(상한 = 평가금액 ÷ 30 의 CASH_SPLIT_CAP배). 현금이 쌓여 놀지 않게 하기 위함
                alloc = min(cash / remaining, config.CASH_SPLIT_CAP * eq_before / config.TOTAL_SLOTS) if remaining else 0.0
                remaining -= 1
                if fill is None or alloc <= 0:
                    continue
                pos[s] = {"market": m, "shares": alloc / (fill[1] * (1 + config.COST_BPS[m] / 1e4)),
                          "entryDate": fill[0], "entryPrice": fill[1]}
                cash -= alloc
        snaps.append((d, cash, {s: (p["shares"], p["entryPrice"]) for s, p in pos.items()}))
    curve = []
    if snaps:
        i = 0
        for c in (x for x in calendar if x >= snaps[0][0]):
            while i + 1 < len(snaps) and snaps[i + 1][0] <= c:
                i += 1
            _, sc, sh = snaps[i]
            vd = min(c, freeze_after) if freeze_after else c
            curve.append((c, sc + sum(n * (book.close_on_or_before(s, vd) or (None, ep))[1] for s, (n, ep) in sh.items())))
    eq_at = dict(curve)
    periods = []
    for (d0, _), (d1, e1) in zip(pre, pre[1:]):
        start = eq_at.get(d0)
        if start:
            periods.append({"start": d0, "end": d1, "returnPct": (e1 / start - 1) * 100})
    holdings = []
    last = curve[-1][0] if curve else None
    total = curve[-1][1] if curve else float(capital)
    for s, p in pos.items():
        c = book.close_on_or_before(s, min(last, freeze_after) if freeze_after and last else last) if last else None
        px = c[1] if c else p["entryPrice"]
        holdings.append({"symbol": s, "market": p["market"], "entryDate": p["entryDate"], "entryPrice": round(p["entryPrice"], 4),
                         "price": round(px, 4), "returnPct": round((px / p["entryPrice"] - 1) * 100, 2),
                         "weightPct": round(p["shares"] * px / total * 100, 2) if total else None})
    holdings.sort(key=lambda h: -(h["weightPct"] or 0))
    return {"variant": variant, "curve": [(d, round(v, 2)) for d, v in curve], "periods": periods, "trades": trades,
            "holdings": holdings, "cash": round(cash, 2), "decisions": decisions}


def pool_periods(events: list[dict], book: PriceBook) -> dict[str, float]:
    """ALL 기준: 적격 종목 동일가중(비용 없음)의 월 수익률(%). 키 = 체결 월(YYYY-MM). 시장별 평균을 다시 평균."""
    by_m: dict[str, list[dict]] = {}
    for e in events:
        if not e["skipped"]:
            by_m.setdefault(e["market"], []).append(e)
    month_vals: dict[str, list[float]] = {}
    for m, evs in by_m.items():
        evs.sort(key=lambda e: e["exec"])
        for a, b in zip(evs, evs[1:]):
            rets = []
            for s in a["df"]["symbol"]:
                f0, c1 = book.first_on_or_after(s, a["exec"], "9999-12-31"), book.close_on_or_before(s, b["exec"])
                if f0 and c1 and c1[0] >= b["exec"]:
                    rets.append(c1[1] / f0[1] - 1)
            if rets:
                month_vals.setdefault(a["exec"][:7], []).append(sum(rets) / len(rets) * 100)
    return {k: sum(v) / len(v) for k, v in month_vals.items()}
