"""입력 조립: 종목별 재무 파일 + 가격 → 시장·신호일별 적격 종목 표(이벤트). 신호일 이후 데이터는 쓰지 않는다."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from . import config, factors, fin
from .book import PriceBook


def load_fin(market: str, fin_dir: Path) -> dict[str, dict]:
    out = {}
    d = fin_dir / market
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("quarters"):
            out[str(rec.get("code") or p.stem)] = rec
    return out


def kr_universe(extra_path: Path) -> dict[str, dict]:
    """NHPLUG 수집 종목(시총순 상위 약 700). {코드: {name, marcapEok, asOf(YYYY-MM-DD)}}."""
    try:
        d = json.loads(extra_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for code, r in (d.get("stocks") or {}).items():
        try:
            as_of = dt.datetime.strptime(str(r.get("asOf")), "%Y%m%d").date().isoformat()
        except ValueError:
            continue
        if isinstance(r.get("marcapEok"), (int, float)) and r["marcapEok"] > 0:
            out[str(code).zfill(6)] = {"name": r.get("name", ""), "marcapEok": float(r["marcapEok"]), "asOf": as_of}
    return out


def kr_shares(universe: dict[str, dict], book: PriceBook) -> dict[str, float]:
    """주식 수 ≈ NHPLUG 시총 ÷ 같은 날 종가. 이후 시총 = 신호일 종가 × 이 수(분할·증자는 반영하지 못하는 근사)."""
    out = {}
    for code, u in universe.items():
        c = book.close_on_or_before(code, u["asOf"])
        if c:
            out[code] = u["marcapEok"] * 1e8 / c[1]
    return out


def month_pairs(calendar: list[str], first_signal: str) -> list[tuple[str, str]]:
    """(신호일 S = 월 마지막 거래일, 체결일 E = 다음 달 첫 거래일). 다음 달 봉이 아직 없으면 만들지 않는다."""
    by_month: dict[str, list[str]] = {}
    for d in calendar:
        by_month.setdefault(d[:7], []).append(d)
    months = sorted(by_month)
    return [(by_month[a][-1], by_month[b][0]) for a, b in zip(months, months[1:]) if by_month[a][-1] >= first_signal]


def build_panel(market: str, signal: str, symbols: dict[str, dict], book: PriceBook) -> dict:
    """symbols: {sym: {name, industry, quarters, mcapShares?(KR: 주식 수) | sharesSeries(US)}} → {rows(적격), df, diag, universe}"""
    rows, diag = [], {}
    for sym, info in symbols.items():
        px = book.features(sym, signal)
        f, reason = fin.ttm(market, info["quarters"], signal)
        shares = info.get("shares") if market == "kr" else fin.shares_us(info.get("sharesSeries") or [], signal)
        mcap = shares * px["price"] if shares and px else None
        row = {"symbol": sym, "name": info.get("name", ""), "industry": info.get("industry"), "mcap": mcap, "fin": f,
               "finReason": reason, "px": px}
        why = "no_price" if px is None else factors.gate(row, market, signal)
        if why:
            diag[why] = diag.get(why, 0) + 1
        else:
            rows.append(row)
    df = factors.score(rows) if rows else pd.DataFrame()
    return {"rows": rows, "df": df, "diag": diag, "universe": len(symbols)}


def build_events(market: str, symbols: dict[str, dict], book: PriceBook, asof: str, first_signal: str) -> list[dict]:
    cal = book.calendar(symbols.keys(), asof)
    events = []
    for s, e in month_pairs(cal, first_signal):
        panel = build_panel(market, s, symbols, book)
        events.append({"market": market, "signal": s, "exec": e, "df": panel["df"], "diag": panel["diag"], "universe": panel["universe"],
                       "skipped": len(panel["rows"]) < config.MIN_ELIGIBLE,
                       "industry": {r["symbol"]: factors.industry2(r.get("industry")) for r in panel["rows"]}})
    return events
