"""가상 계좌 시뮬레이터. 에이전트 하나 = 계좌 하나. 가격은 일봉 종가(수정주가)만 쓴다.

규칙(config 참고): 신호 유효일 다음 거래일 종가에 진입, 종가 -STOP_PCT% 이하이면 그 종가에 청산,
HOLD_SESSIONS 거래일이 지나면 시간 청산, 동시 MAX_POSITIONS개, 편도 비용 차감. 같은 날 청산을 먼저 처리해 자리를 비운다.
"""
from __future__ import annotations

import bisect

import pandas as pd

from . import config


def _entry_date(series: pd.Series, signal_date: str) -> str | None:
    idx = list(series.index)
    i = bisect.bisect_right(idx, signal_date) - 1 + config.ENTRY_LAG
    return idx[i] if 0 <= i < len(idx) else None


def simulate(select, signals: list[dict], closes: dict[tuple[str, str], pd.Series], start: str, end: str | None = None) -> dict:
    cal = sorted({d for s in closes.values() for d in s.index if d >= start and (end is None or d <= end)})
    if not cal:
        return {"curve": [], "trades": [], "open": [], "skipped": [], "calendar": []}
    by_entry: dict[str, list[tuple[float, dict]]] = {}
    skipped: list[dict] = []
    for sig in signals:
        if sig["date"] < start:
            continue
        series = closes.get((sig["market"], sig["code"]))
        score = select(sig)
        if score is None:
            continue
        if series is None or series.empty:
            skipped.append({"id": sig["id"], "code": sig["code"], "name": sig.get("name"), "signalDate": sig["date"], "reason": "NO_PRICE_DATA"})
            continue
        ed = _entry_date(series, sig["date"])
        if ed is None:
            skipped.append({"id": sig["id"], "code": sig["code"], "name": sig.get("name"), "signalDate": sig["date"], "reason": "NO_ENTRY_BAR_YET"})
            continue
        by_entry.setdefault(ed, []).append((score, sig))

    cash = float(config.INITIAL_CAPITAL)
    held: dict[tuple[str, str], dict] = {}
    trades: list[dict] = []
    curve: list[dict] = []
    last_px: dict[tuple[str, str], float] = {}

    def equity() -> float:
        return cash + sum(p["shares"] * last_px[k] for k, p in held.items())

    for d in cal:
        for k, series in closes.items():
            if d in series.index:
                last_px[k] = float(series[d])
        for k in list(held):  # 1) 청산
            series, p = closes[k], held[k]
            if d not in series.index or d <= p["entryDate"]:
                continue
            p["sessions"] += 1
            px = float(series[d])
            reason = "STOP" if px <= p["entryPrice"] * (1 - config.STOP_PCT / 100) else "TIME" if p["sessions"] >= config.HOLD_SESSIONS else None
            if reason:
                cost = config.COST_BPS[k[0]] / 1e4
                cash += p["shares"] * px * (1 - cost)
                ret = (px * (1 - cost)) / (p["entryPrice"] * (1 + config.COST_BPS[k[0]] / 1e4)) - 1
                trades.append({**{x: p[x] for x in ("id", "market", "code", "name", "entryDate", "entryPrice", "sessions")},
                               "exitDate": d, "exitPrice": px, "reason": reason, "returnPct": round(ret * 100, 3)})
                del held[k]
        for score, sig in sorted(by_entry.get(d, []), key=lambda t: (-t[0], t[1]["id"])):  # 2) 진입
            k = (sig["market"], sig["code"])
            if k in held:
                continue
            if len(held) >= config.MAX_POSITIONS:
                skipped.append({"id": sig["id"], "code": sig["code"], "name": sig.get("name"), "signalDate": sig["date"], "reason": "NO_SLOT"})
                continue
            size = min(cash, equity() / config.MAX_POSITIONS)
            px = float(closes[k][d])
            if size <= 0:
                skipped.append({"id": sig["id"], "code": sig["code"], "name": sig.get("name"), "signalDate": sig["date"], "reason": "NO_CASH"})
                continue
            cost = config.COST_BPS[k[0]] / 1e4
            shares = size / (px * (1 + cost))
            cash -= size
            held[k] = {"id": sig["id"], "market": k[0], "code": k[1], "name": sig.get("name"), "entryDate": d,
                       "entryPrice": px, "shares": shares, "sessions": 0}
        curve.append({"date": d, "equity": round(equity(), 4), "positions": len(held)})
    open_pos = [{**{x: p[x] for x in ("id", "market", "code", "name", "entryDate", "entryPrice", "sessions")},
                 "lastPrice": last_px[k], "unrealizedPct": round((last_px[k] / p["entryPrice"] - 1) * 100, 3)}
                for k, p in held.items()]
    return {"curve": curve, "trades": trades, "open": open_pos, "skipped": skipped, "calendar": cal}
