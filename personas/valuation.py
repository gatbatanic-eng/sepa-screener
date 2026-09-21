"""personas/valuation.py — Yahoo Finance(yfinance) 밸류에이션 수집.

SEC 재무의 분기 EPS 가 대부분 비어 있어 미국 종목은 공시 기반 PER 을 거의 못 낸다(추세통과 42개 중 1개).
그 공백을 외부 소스(Yahoo)로 메운다. **출처가 공시가 아니라는 점을 항상 라벨링**한다.

원칙
----
- 값이 없거나 비정상이면 None. 추정·보정하지 않는다.
- 수집에 실패한 종목은 **이전 캐시 값을 지우지 않고** ``stale=True`` 와 오류 사유를 남긴다(조용히 사라지지 않음).
- ``forwardPE`` / ``forwardEps`` 는 애널리스트 추정 기반이다 — 공시 실적이 아니다.
- 적자 기업은 trailingPE 가 없는 게 정상이다(``lossMaking``). 값을 만들어 채우지 않는다.

캐시: ``docs/data/valuation_us.json`` (공개 시장 데이터, 저장소에 커밋되어 다음 실행이 이어받음).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATHS = {"us": ROOT / "docs" / "data" / "valuation_us.json"}
SOURCE = "Yahoo Finance (yfinance)"
MAX_AGE_HOURS = 20          # 이보다 신선한 캐시는 다시 받지 않는다
PRUNE_DAYS = 30             # 이 기간 동안 요청되지 않은 종목은 캐시에서 정리
PE_INCONSISTENT = 0.25      # trailingPE 와 (현재가/trailingEps) 가 이 비율 넘게 다르면 표시

# fdr 의 S&P500 목록은 복수클래스 종목의 점(.)을 없애서 내려주는데 야후는 하이픈을 쓴다.
_US_OVERRIDES = {"BRKB": "BRK-B", "BFB": "BF-B"}


def yf_symbol(code: Any) -> str:
    text = str(code).strip().upper()
    return _US_OVERRIDES.get(text, text.replace(".", "-"))


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _pos(v: Any) -> Optional[float]:
    x = _num(v)
    return x if x is not None and x > 0 else None


def parse_info(info: Optional[dict]) -> Optional[dict]:
    """yfinance ``Ticker.info`` → 필요한 필드만. 유효한 값이 하나도 없으면 None."""
    if not info:
        return None
    qt = info.get("quoteType")
    if qt not in (None, "EQUITY"):
        return None
    rec: dict[str, Any] = {
        "trailingPE": _pos(info.get("trailingPE")),
        "forwardPE": _pos(info.get("forwardPE")),          # 애널리스트 추정 기반
        "priceToBook": _pos(info.get("priceToBook")),
        "trailingEps": _num(info.get("trailingEps")),
        "forwardEps": _num(info.get("forwardEps")),        # 애널리스트 추정 기반
        "marketCap": _pos(info.get("marketCap")),
        "currentPrice": _pos(info.get("currentPrice") or info.get("regularMarketPrice")),
        "sector": str(info["sector"]) if info.get("sector") else None,
        "industry": str(info["industry"]) if info.get("industry") else None,
        # Yahoo가 제공할 때만 보존. 미래 실적일 이벤트 리스크 보조용이며 없으면 None.
        "earningsTimestamp": _num(info.get("earningsTimestamp")),
        "earningsTimestampStart": _num(info.get("earningsTimestampStart")),
        "earningsTimestampEnd": _num(info.get("earningsTimestampEnd")),
    }
    if all(rec[k] is None for k in ("trailingPE", "forwardPE", "priceToBook", "trailingEps", "marketCap", "currentPrice")):
        return None
    eps = rec["trailingEps"]
    rec["lossMaking"] = eps is not None and eps <= 0
    rec["peInconsistent"] = False
    if rec["trailingPE"] and rec["currentPrice"] and eps and eps > 0:
        implied = rec["currentPrice"] / eps
        rec["peInconsistent"] = abs(implied / rec["trailingPE"] - 1) > PE_INCONSISTENT
    return rec


def fetch_one(symbol: str, *, retries: int = 3, backoff: float = 2.0,
              sleep: Callable[[float], None] = time.sleep) -> tuple[Optional[dict], Optional[str]]:
    """(레코드, 오류사유). 429 는 더 오래 기다리며 재시도한다."""
    import yfinance as yf

    last = "알 수 없는 오류"
    for attempt in range(1, retries + 1):
        try:
            rec = parse_info(yf.Ticker(symbol).info or {})
            if rec is None:
                return None, "Yahoo가 유효한 밸류에이션 필드를 반환하지 않음"
            return rec, None
        except Exception as exc:  # noqa: BLE001 - 종목 하나의 실패가 전체를 죽이면 안 됨
            msg = str(exc)
            last = f"{type(exc).__name__}: {msg[:120]}"
            rate_limited = "429" in msg or "too many" in msg.lower()
            if attempt < retries:
                sleep(backoff * attempt + (5.0 if rate_limited else 0.0))
    return None, last


def load_cache(path: Path) -> dict:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("symbols"), dict):
                return data
        except (OSError, ValueError):
            pass
    return {"schemaVersion": 1, "source": SOURCE, "symbols": {}}


def _write_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _age_hours(iso: Optional[str], now: dt.datetime) -> Optional[float]:
    if not iso:
        return None
    try:
        return (now - dt.datetime.fromisoformat(iso)).total_seconds() / 3600.0
    except ValueError:
        return None


def collect(symbols: Iterable[str], cache_path: Path, *, fetcher: Callable[[str], tuple[Optional[dict], Optional[str]]] = fetch_one,
            now: Optional[dt.datetime] = None, delay: float = 0.25,
            sleep: Callable[[float], None] = time.sleep, write: bool = True) -> dict:
    """요청한 종목의 밸류에이션을 캐시에 갱신하고 요약을 돌려준다."""
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat()
    cache = load_cache(cache_path)
    book: dict[str, dict] = cache["symbols"]
    wanted = list(dict.fromkeys(yf_symbol(s) for s in symbols))
    summary = {"requested": len(wanted), "fetched": 0, "cached": 0, "failed": 0, "staleKept": 0, "failures": {}}

    for sym in wanted:
        entry = book.get(sym)
        age = _age_hours(entry.get("fetchedAt"), now) if entry else None
        if entry and not entry.get("stale") and age is not None and age < MAX_AGE_HOURS:
            summary["cached"] += 1
            continue
        rec, err = fetcher(sym)
        if rec is not None:
            book[sym] = {**rec, "fetchedAt": stamp, "stale": False, "error": None}
            summary["fetched"] += 1
        else:
            summary["failed"] += 1
            summary["failures"][sym] = err
            if entry and entry.get("fetchedAt"):
                entry.update(stale=True, error=err, lastAttemptAt=stamp)      # 이전 값은 유지
                summary["staleKept"] += 1
            else:
                book[sym] = {"fetchedAt": None, "stale": True, "error": err, "lastAttemptAt": stamp}
        sleep(delay)

    for sym in [s for s in book if s not in wanted]:                            # 오래 요청 안 된 종목만 정리
        ref = book[sym].get("fetchedAt") or book[sym].get("lastAttemptAt")
        age = _age_hours(ref, now)
        if age is not None and age > PRUNE_DAYS * 24:
            del book[sym]

    cache["updatedAt"] = stamp
    cache["source"] = SOURCE
    if write:
        _write_cache(cache_path, cache)
    return summary


def get(cache: Optional[dict], code: Any) -> Optional[dict]:
    """페르소나 로직에 넘길 레코드. 값이 하나도 없는 실패 항목은 None."""
    if not cache:
        return None
    entry = (cache.get("symbols") or {}).get(yf_symbol(code))
    if not entry or entry.get("fetchedAt") is None:
        return None
    return entry


def trend_symbols(market: str, root: Path = ROOT) -> list[str]:
    """추세 통과(TREND_OK) 종목 코드 목록."""
    path = root / "docs" / "data" / f"latest_{market}.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [str(r["code"]) for r in rows if r.get("trendOk") in (True, "True", "TRUE")]
