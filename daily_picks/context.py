"""v2 근거 자료: 업종(섹터) 강도, 시장 환경, 매크로 요약, 한국 장 마감 이슈. 모두 이미 있는 파일에서 읽는 수치이고, 없으면 해당 항목을 생략한다(0으로 채우지 않는다)."""
from __future__ import annotations

import datetime as dt
import json
import statistics
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
