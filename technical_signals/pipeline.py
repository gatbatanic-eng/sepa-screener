"""
technical_signals/pipeline.py — 유니버스 조회 → OHLCV 조회 → 신호 계산 → 국면 반영
=======================================================================================

흐름: 유니버스 조회 → (병렬) 종목별 OHLCV 조회 + evaluate_signals() →
전 종목 계산 후 breadth 집계 → 지수 OHLCV로 시장 국면(regime.py) 판정 →
종목별로 compute_verdict()를 호출해 trend_verdict/rebound_verdict를 채운다.
개별 종목 계산 시점엔 유니버스 전체의 breadth를 아직 모르므로 국면 판정은
반드시 전 종목 계산 이후에 한다.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

import config as cfg
import data
import regime as regime_mod
from signals import SignalResult, compute_verdict, evaluate_signals

logger = logging.getLogger(__name__)

MAX_WORKERS = 8
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.6

MIN_TRADING_VALUE = {"KR": cfg.MIN_TRADING_VALUE_KRW, "US": cfg.MIN_TRADING_VALUE_USD}
INDEX_CODES = {"KR": "KS11", "US": cfg.US_INDEX_CODE}  # KR은 코스피 지수를 대표값으로 쓴다


@dataclass
class StockRecord:
    code: str
    name: str
    market: str
    status: str = "OK"
    reason: Optional[str] = None
    changePct: Optional[float] = None
    signals: SignalResult = field(default_factory=SignalResult)


def _fetch_with_retry(code: str, start: dt.date) -> pd.DataFrame:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            df = data.fetch_ohlcv(code, start)
            if df is None or df.empty:
                raise ValueError("빈 OHLCV")
            return df
        except Exception as exc:  # noqa: BLE001 - 재시도 루프에서 원인 무관하게 재시도
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_BASE ** attempt)
    raise last_exc if last_exc else RuntimeError("OHLCV 조회 실패")


def _evaluate_one(code: str, name: str, market: str, start: dt.date, min_trading_value: float) -> StockRecord:
    try:
        df = _fetch_with_retry(code, start)
    except Exception as exc:  # noqa: BLE001
        return StockRecord(code=code, name=name, market=market, status="확인불가",
                            reason=f"OHLCV 조회 실패: {exc}")

    if len(df) < 2:
        return StockRecord(code=code, name=name, market=market, status="확인불가",
                            reason="데이터 부족")

    change_pct = None
    try:
        change_pct = float(df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1.0)
    except Exception:  # noqa: BLE001
        pass

    sig = evaluate_signals(df, min_trading_value=min_trading_value)
    status = "OK" if sig.close is not None else "확인불가"
    reason = None if status == "OK" else "; ".join(sig.reasons) or "지표 계산 불가"
    return StockRecord(code=code, name=name, market=market, status=status, reason=reason,
                        changePct=change_pct, signals=sig)


def run(market: str, limit: int | None = None) -> tuple[list[StockRecord], regime_mod.RegimeResult]:
    """market: 'KR' 또는 'US'. 반환: (종목별 결과, 시장 국면)."""
    if market == "KR":
        universe = data.fetch_kr_universe()
    elif market == "US":
        universe = data.fetch_us_universe()
    else:
        raise ValueError(f"알 수 없는 market: {market}")

    if limit:
        universe = universe.head(limit)

    start = data.history_start_date()
    min_trading_value = MIN_TRADING_VALUE[market]
    records: list[StockRecord] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_evaluate_one, row.Code, row.Name, row.Market, start, min_trading_value): row.Code
            for row in universe.itertuples()
        }
        done = 0
        total = len(futures)
        for fut in as_completed(futures):
            records.append(fut.result())
            done += 1
            if done % 50 == 0 or done == total:
                logger.info("%s: %d/%d 완료", market, done, total)

    order = {row.Code: i for i, row in enumerate(universe.itertuples())}
    records.sort(key=lambda r: order.get(r.code, 1_000_000))

    # --- breadth: OK 판정 종목 중 SMA50 위에 있는 비율 ---
    ok_records = [r for r in records if r.status == "OK"]
    above_sma50 = [r.signals.close > r.signals.sma_fast for r in ok_records
                   if r.signals.close is not None and r.signals.sma_fast is not None]
    breadth = regime_mod.compute_breadth(above_sma50)

    # --- 시장 국면: 지수 OHLCV + breadth ---
    index_code = INDEX_CODES[market]
    index_df = None
    try:
        index_df = _fetch_with_retry(index_code, start)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: 지수(%s) 조회 실패, 시장 국면 판정 불가 — %s", market, index_code, exc)
    regime_result = regime_mod.evaluate_regime(index_df, breadth)

    for r in records:
        compute_verdict(r.signals, regime_result.regime)

    return records, regime_result


def record_to_dict(r: StockRecord) -> dict:
    d = {"code": r.code, "name": r.name, "market": r.market, "status": r.status,
         "reason": r.reason, "changePct": r.changePct}
    d.update({_camel(k): v for k, v in dataclasses.asdict(r.signals).items() if k != "reasons"})
    d["signalReasons"] = r.signals.reasons
    return d


def regime_to_dict(reg: regime_mod.RegimeResult) -> dict:
    return {_camel(k): v for k, v in dataclasses.asdict(reg).items()}


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(p.title() for p in rest)
