from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from .normalize import normalize_fmp_estimate

BASE_URL = "https://financialmodelingprep.com/stable/analyst-estimates"


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
                raise FMPError("FMP analyst-estimates response is not a JSON list")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep((2, 5, 15)[attempt])
    raise FMPError(f"FMP request failed after {retries} attempts: {last_error}") from last_error


def fetch_annual_estimates(
    symbol: str,
    api_key: str | None = None,
    limit: int = 10,
    sleep_seconds: float = 0.22,
) -> list[dict]:
    api_key = (api_key or os.environ.get("FMP_API_KEY", "")).strip()
    if not api_key:
        raise FMPError("FMP_API_KEY is required")
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "period": "annual",
        "page": 0,
        "limit": limit,
        "apikey": api_key,
    })
    payload = _request_json(f"{BASE_URL}?{params}")
    if sleep_seconds:
        time.sleep(sleep_seconds)
    return [normalize_fmp_estimate(symbol, row) for row in payload]
