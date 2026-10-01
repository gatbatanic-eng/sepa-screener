"""일봉 종가(수정주가) 조회. Yahoo는 Actions에서만 열린다. 실패한 종목은 결과에서 빠지고 호출한 쪽이 '조회 불가'로 센다."""
from __future__ import annotations

import logging
import time

import pandas as pd

from . import config
from .store import read_json, write_json

log = logging.getLogger("ledger.prices")
CHUNK = 200


def _download(symbols: list[str], start: str) -> dict[str, pd.Series]:
    import yfinance as yf

    out: dict[str, pd.Series] = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        frame = None
        for attempt in range(3):
            try:
                frame = yf.download(chunk, start=start, interval="1d", auto_adjust=True, progress=False,
                                    threads=True, group_by="column")
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("Yahoo 다운로드 실패 (%d/3): %s", attempt + 1, exc)
                time.sleep(5 * (attempt + 1))
        if frame is None or frame.empty:
            continue
        closes = frame["Close"] if isinstance(frame.columns, pd.MultiIndex) else frame[["Close"]].rename(columns={"Close": chunk[0]})
        for sym in chunk:
            if sym in closes.columns:
                s = pd.to_numeric(closes[sym], errors="coerce").dropna()
                s = s[s > 0]
                if not s.empty:
                    s.index = [pd.Timestamp(d).date().isoformat() for d in s.index]
                    out[sym] = s
        time.sleep(1)
    return out


def fetch_closes(market: str, symbols: list[str], start: str, exchange_hint: dict[str, str] | None = None) -> tuple[dict[str, pd.Series], dict[str, str]]:
    """{종목: 일봉 종가(날짜 문자열 인덱스)}, {종목: 거래소}. 한국은 거래소를 모르면 .KS → .KQ 순으로 확인하고 캐시한다."""
    if market == "us":
        data = _download([s.replace(".", "-") for s in symbols], start)
        return {s: data[s.replace(".", "-")] for s in symbols if s.replace(".", "-") in data}, {s: "US" for s in symbols}
    cache = read_json(config.EXCHANGE_CACHE, {})
    exch = {**cache, **(exchange_hint or {})}
    suffix = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
    closes: dict[str, pd.Series] = {}
    for label, sfx in (("KOSPI", ".KS"), ("KOSDAQ", ".KQ")):
        # 거래소를 아는 종목은 그 접미사로, 모르는 종목은 두 접미사 모두 시도한다.
        todo = [s for s in symbols if s not in closes and exch.get(s, label) == label]
        got = _download([s + sfx for s in todo], start)
        for s in todo:
            if s + sfx in got:
                closes[s] = got[s + sfx]
                exch[s] = label
    write_json(config.EXCHANGE_CACHE, {s: exch[s] for s in sorted(exch) if exch[s] in suffix})
    return closes, {s: exch[s] for s in closes}


def fetch_benchmarks(start: str) -> dict[str, pd.Series]:
    """거래소 이름 → 지수 일봉 종가."""
    data = _download(sorted(set(config.BENCHMARKS.values())), start)
    return {name: data[sym] for name, sym in config.BENCHMARKS.items() if sym in data}
