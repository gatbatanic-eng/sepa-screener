"""v2 근거 자료: 업종(섹터) 강도, 시장 환경, 매크로 요약, 한국 장 마감 이슈. 모두 이미 있는 파일에서 읽는 수치이고, 없으면 해당 항목을 생략한다(0으로 채우지 않는다)."""
from __future__ import annotations

import datetime as dt
import json
import statistics

import numpy as np
from collections import Counter, defaultdict
from pathlib import Path

from . import config as C

ROOT = Path(__file__).resolve().parents[1]
US_SECTOR_CACHE = "research/daily_picks/sectors_us.json"
US_RETRY_DAYS = 7


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _strip_kr(sector: str | None) -> str | None:
    if not sector:
        return None
    for p in ("코스피 ", "코스닥 "):
        if sector.startswith(p):
            return sector[len(p):]
    return sector


def sector_map(market: str, root: Path = ROOT) -> dict[str, str]:
    """종목 코드 → 업종. 한국: NHPLUG 업종 + 세파 유니버스 밖 보완 파일. 미국: Yahoo 섹터 캐시."""
    if market == "kr":
        out = {}
        for rel in ("research/nhplug/kr_sector_extra.json", "research/nhplug/kr_extra.json"):
            for code, v in ((_json(root / rel, {}) or {}).get("stocks") or {}).items():
                s = _strip_kr(v.get("sector")) if isinstance(v, dict) else None
                if s:
                    out[code] = s
        return out
    cache = _json(root / US_SECTOR_CACHE, {}) or {}
    return {sym: v["sector"] for sym, v in cache.items() if isinstance(v, dict) and v.get("sector")}


def fetch_us_sectors(symbols: list[str], root: Path = ROOT, today: dt.date | None = None, fetch=None, workers: int = 8) -> int:
    """캐시에 없거나(없음 표시는 7일 뒤 재시도) 못 받은 종목만 Yahoo에서 받아 캐시에 더한다. 받은 건수를 돌려준다."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    path = root / US_SECTOR_CACHE
    cache = _json(path, {}) or {}

    def due(sym):
        v = cache.get(sym)
        if not v:
            return True
        return not v.get("sector") and (today - dt.date.fromisoformat(v["at"])).days >= US_RETRY_DAYS

    todo = [s for s in dict.fromkeys(symbols) if due(s)]
    if not todo:
        return 0
    if fetch is None:
        def fetch(sym):
            import yfinance as yf
            return (yf.Ticker(sym).info or {}).get("sector")
    from concurrent.futures import ThreadPoolExecutor

    def one(sym):
        try:
            return sym, fetch(sym)
        except Exception:  # noqa: BLE001 — 한 종목 실패가 전체를 막지 않게
            return sym, None

    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(one, todo))
    for sym, sector in results:
        cache[sym] = {"sector": sector or None, "at": today.isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True, indent=0), encoding="utf-8")
    return sum(1 for _, s in results if s)


def sector_strength(sepa_rows: dict, sectors: dict[str, str]) -> dict[str, dict]:
    """세파 유니버스 종목을 업종별로 묶어 강도 계산. 강도 = (50일선 위 비중% + 평균 RS 순위) / 2. 종목 수 5개 미만 업종은 제외."""
    groups = defaultdict(list)
    for code, r in sepa_rows.items():
        sec = sectors.get(code)
        if sec and r.get("status") == "OK" and r.get("sma50") and r.get("close"):
            groups[sec].append(r)
    stats = {}
    for sec, rows in groups.items():
        if len(rows) < C.SECTOR_MIN_N:
            continue
        above = 100.0 * sum(r["close"] > r["sma50"] for r in rows) / len(rows)
        rs = statistics.mean((r.get("rsRank") or 0) for r in rows)
        stats[sec] = {"n": len(rows), "above50Pct": round(above, 1), "avgRs": round(rs, 1), "strength": round((above + rs) / 2, 1)}
    order = sorted(stats, key=lambda s: (-stats[s]["strength"], s))
    third = max(1, len(order) // 3)
    for i, sec in enumerate(order):
        stats[sec]["rank"] = i + 1
        stats[sec]["of"] = len(order)
        stats[sec]["adj"] = C.SECTOR_BONUS if i < third else -C.SECTOR_BONUS if i >= len(order) - third else 0
    return stats


def market_regimes(sepa_rows: dict) -> dict[str, str]:
    """거래소별 시장 국면(그 거래소 종목 행에 찍힌 값의 최빈값)."""
    by = defaultdict(Counter)
    for r in sepa_rows.values():
        if r.get("market") and r.get("regime"):
            by[r["market"]][r["regime"]] += 1
    return {m: c.most_common(1)[0][0] for m, c in by.items()}


def macro_summary(root: Path = ROOT) -> dict | None:
    m = _json(root / "docs" / "macro.json")
    if not m or not m.get("regime_label"):
        return None
    ind = m.get("indicators") or {}

    def val(k):
        v = ind.get(k)
        return v.get("value") if isinstance(v, dict) else v

    return {"label": m["regime_label"], "generatedAt": m.get("generated_at"),
            "vix": val("VIX"), "usdkrw": val("USDKRW"), "dxy": val("DXY"), "us10y": val("US10Y"),
            "signals": [s["message"] for s in (m.get("signals") or [])[:2] if s.get("message")]}


def kr_close_context(session: str | None, root: Path = ROOT) -> dict:
    """한국 장 마감 정리에서 기준일과 같은 날 보고서가 있을 때만 업종 강세/약세·수급 유입과 이슈 종목을 읽는다."""
    files = sorted((root / "docs" / "market" / "kr_close").glob("*.json"))
    if not files or not session:
        return {}
    d = _json(files[-1], {}) or {}
    if d.get("date") != session.replace("-", ""):
        return {}
    names = lambda key: {x["sector"]: x for x in d.get(key, []) if x.get("sector")}   # noqa: E731
    return {"strong": names("strongSectors"), "weak": names("weakSectors"), "flowIn": names("flowInSectors"), "flowOut": names("flowOutSectors"),
            "issues": {x["code"]: x for x in d.get("issues", []) if x.get("code")}}


def atr_pct_from_chart(code: str, root: Path = ROOT) -> float | None:
    """종목 차트 파일(docs/data/stock_charts/us)의 일봉으로 ATR14(Wilder)/종가. 백테스트(leader_backtest.rules)와 같은 정의. 이력이 모자라면 None."""
    d = _json(root / "docs" / "data" / "stock_charts" / "us" / f"{code}.json")
    if not d or len(d.get("close") or []) < 30:
        return None
    try:
        h, l, c = (np.array(d[k], dtype=float) for k in ("high", "low", "close"))
    except (KeyError, TypeError, ValueError):
        return None
    prev = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev), np.abs(l - prev)]), axis=0)
    tr = tr[~np.isnan(tr)]
    if len(tr) < 15 or not np.isfinite(c[-1]) or c[-1] <= 0:
        return None
    alpha, atr = 1.0 / C.ATR_PERIOD, tr[0]
    for x in tr[1:]:
        atr = alpha * x + (1 - alpha) * atr
    return float(atr / c[-1])


# --- 미국 컨센서스(야후 애널리스트 정보): 추천 후보에 대해 하루 한 번 받아 일별 스냅샷으로 남긴다(이력이 있어야 나중에 수정률을 계산할 수 있다) -------------
CONSENSUS_FIELDS = ("targetMeanPrice", "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions", "recommendationKey",
                    "earningsTimestampStart", "earningsTimestamp")


def consensus_path(root: Path, market: str, day: dt.date) -> Path:
    return root / "research" / "daily_picks" / "consensus" / market / f"{day.isoformat()}.json"


def fetch_us_consensus(symbols: list[str], root: Path = ROOT, today: dt.date | None = None, fetch=None, workers: int = 8) -> dict[str, dict]:
    """오늘(UTC 날짜) 스냅샷이 있으면 그대로 읽고, 없으면 받아서 한 번만 쓴다(수정 금지). 받지 못한 종목은 결과에서 빠진다(0으로 채우지 않음)."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    path = consensus_path(root, "us", today)
    snap = (_json(path, {}) or {}).get("symbols") if path.exists() else None
    if snap is not None:
        return snap
    if fetch is None:
        def fetch(sym):
            import yfinance as yf
            info = yf.Ticker(sym).info or {}
            return {k: info.get(k) for k in CONSENSUS_FIELDS}
    from concurrent.futures import ThreadPoolExecutor

    def one(sym):
        try:
            d = fetch(sym)
        except Exception:  # noqa: BLE001 — 한 종목 실패가 전체를 막지 않게
            return sym, None
        d = {k: v for k, v in (d or {}).items() if v not in (None, "", 0) and not (isinstance(v, float) and v != v)}
        return sym, (d or None)

    with ThreadPoolExecutor(workers) as ex:
        got = {s: d for s, d in ex.map(one, list(dict.fromkeys(symbols))) if d}
    if got:                                        # 하나도 못 받았으면 스냅샷을 남기지 않는다(빈 기록으로 굳지 않게)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": today.isoformat(), "source": "yahoo", "symbols": got}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return got


RATING_KO = {"strong_buy": "적극 매수", "buy": "매수", "hold": "중립", "underperform": "비중 축소", "sell": "매도", "none": None}


def consensus_line(c: dict | None, price: float | None, today: dt.date) -> str | None:
    """컨센서스 한 줄. 값이 없는 항목은 생략하고, 하나도 없으면 줄 자체를 만들지 않는다."""
    if not c:
        return None
    bits = []
    n, tgt = c.get("numberOfAnalystOpinions"), c.get("targetMeanPrice")
    if n:
        bits.append(f"애널리스트 {int(n)}명")
    if tgt:
        bits.append(f"평균 목표가 ${tgt:,.0f}" + (f" (현재가 대비 {(tgt / price - 1) * 100:+.0f}%)" if price else ""))
    rating = RATING_KO.get(str(c.get("recommendationKey") or "").lower())
    if rating:
        bits.append(f"투자의견 {rating}")
    ts = c.get("earningsTimestampStart") or c.get("earningsTimestamp")
    if ts:
        try:
            d = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).date()
            if d >= today:
                gap = (d - today).days
                bits.append(f"다음 실적 발표 {d:%m/%d} (D-{gap}{', 변동성 주의' if gap <= 14 else ''})")
        except (ValueError, OSError, OverflowError):
            pass
    return (f"컨센서스(야후, {today:%m/%d}): " + " · ".join(bits) + " (참고, 점수 아님)") if bits else None
