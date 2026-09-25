from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

from .normalize import normalize_fmp_estimate

BASE_URL = "https://financialmodelingprep.com/stable"
ESTIMATES_URL = f"{BASE_URL}/analyst-estimates"


class FMPError(RuntimeError):
    pass


def _request_json(url: str, retries: int = 3) -> list[dict]:
    last_error = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "GoldenCode-FPD/0.1"},
            )
            with urllib.request.urlopen(req, timeout=45) as response:
                payload = json.load(response)
            if not isinstance(payload, list):
                raise FMPError("FMP response is not a JSON list")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep((2, 5, 15)[attempt])
    raise FMPError(f"FMP request failed after {retries} attempts: {last_error}") from last_error


def _api_key(api_key: str | None = None) -> str:
    value = (api_key or os.environ.get("FMP_API_KEY", "")).strip()
    if not value:
        raise FMPError("FMP_API_KEY is required")
    return value


def fetch_annual_estimates(
    symbol: str,
    api_key: str | None = None,
    limit: int = 10,
    sleep_seconds: float = 0.22,
    retries: int = 3,
) -> list[dict]:
    key = _api_key(api_key)
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "period": "annual",
        "page": 0,
        "limit": limit,
        "apikey": key,
    })
    payload = _request_json(f"{ESTIMATES_URL}?{params}", retries=retries)
    if sleep_seconds:
        time.sleep(sleep_seconds)
    return [normalize_fmp_estimate(symbol, row) for row in payload]


def _calendar_chunk(
    endpoint: str,
    start: date,
    end: date,
    api_key: str | None = None,
    max_pages: int = 10,
) -> list[dict]:
    key = _api_key(api_key)
    rows: list[dict] = []
    for page in range(max_pages):
        params = urllib.parse.urlencode({
            "from": start.isoformat(),
            "to": end.isoformat(),
            "page": page,
            "apikey": key,
        })
        batch = _request_json(f"{BASE_URL}/{endpoint}?{params}")
        rows.extend(batch)
        if len(batch) < 4000:
            break
    else:
        raise FMPError(f"FMP {endpoint} exceeded {max_pages} pages")
    return rows


def fetch_calendar_range(
    endpoint: str,
    start: date,
    end: date,
    api_key: str | None = None,
) -> list[dict]:
    """Fetch a calendar range in <=90-day chunks, preserving provider rows."""
    if end < start:
        raise ValueError("calendar end precedes start")
    rows: list[dict] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=89))
        rows.extend(_calendar_chunk(endpoint, cursor, chunk_end, api_key=api_key))
        cursor = chunk_end + timedelta(days=1)
    return rows


def fetch_recent_splits(session_date: date, api_key: str | None = None, lookback_days: int = 100) -> list[dict]:
    return fetch_calendar_range(
        "splits-calendar",
        session_date - timedelta(days=lookback_days),
        session_date,
        api_key=api_key,
    )


def fetch_recent_earnings(session_date: date, api_key: str | None = None, lookback_days: int = 100) -> list[dict]:
    return fetch_calendar_range(
        "earnings-calendar",
        session_date - timedelta(days=lookback_days),
        session_date,
        api_key=api_key,
    )
