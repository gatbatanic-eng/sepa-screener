"""일일 스크리닝 오케스트레이션.

종목 단위 처리(`process_symbol`)는 순수하게 이미 가져온 데이터로만
동작해 네트워크 없이 테스트할 수 있다. 실제 데이터 수집·전체 유니버스
순회(`run_daily_screen`)는 `src/data/loader.py`의 fetch_* 함수를 쓰므로
네트워크가 필요하며, 그 함수들과 마찬가지로 단위 테스트 대상에서
제외한다(대신 `process_symbol`을 철저히 검증한다).

백테스트(Phase 7)와 동일하게 `src/pipeline.py`의 evaluate_range_mr/
evaluate_v_rebound를 그대로 쓴다 — 스펙 33조 "동일한 전략 엔진" 원칙.
"""
from __future__ import annotations

import logging
from datetime import date as date_
from datetime import timedelta
from typing import Optional

import pandas as pd

from src.config import StrategyConfig, load_config
from src.data.loader import (
    KOSDAQ_INDEX_CODE,
    KOSPI_INDEX_CODE,
    fetch_index_ohlcv,
    fetch_kr_universe,
    fetch_ohlcv,
)
from src.market.regime import compute_regime_series
from src.models.market_data import OHLCVBar
from src.models.signal import Signal
from src.pipeline import evaluate_range_mr, evaluate_v_rebound, range_mr_row_to_signal, v_rebound_row_to_signal
from src.storage import get_engine, get_session_factory, save_signal

logger = logging.getLogger(__name__)


def bars_to_frame(bars: list[OHLCVBar]) -> pd.DataFrame:
    records = [
        {"date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
        for b in bars
    ]
    df = pd.DataFrame(records).set_index("date").sort_index()
    df.index = pd.to_datetime(df.index)
    return df


def _align_to_index(series: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """다른 시계열(벤치마크/레짐)을 종목의 거래일 인덱스에 맞춘다.

    공휴일 차이 등으로 날짜가 완전히 일치하지 않을 수 있어 reindex 후
    직전 값으로 채운다(ffill) — 과거 값만 사용하므로 미래 데이터를
    끌어오지 않는다(스펙 2.3조).
    """
    aligned = series.copy()
    aligned.index = pd.to_datetime(aligned.index)
    return aligned.reindex(target_index).ffill()


def process_symbol(
    symbol: str,
    bars: list[OHLCVBar],
    benchmark_close: pd.Series,
    market_regime_series: Optional[pd.Series],
    config: StrategyConfig,
    name: Optional[str] = None,
) -> tuple[Optional[Signal], Optional[Signal]]:
    """RANGE-MR과 V-REBOUND를 각각 평가해 오늘(마지막 거래일)자 Signal을
    반환한다. 데이터가 부족하면 (None, None)이다.
    """
    if len(bars) < config.data.min_trading_days:
        return None, None

    df = bars_to_frame(bars)
    benchmark_aligned = _align_to_index(benchmark_close, df.index)
    regime_aligned = _align_to_index(market_regime_series, df.index) if market_regime_series is not None else None

    range_mr_df = evaluate_range_mr(
        df["open"], df["high"], df["low"], df["close"], df["volume"], benchmark_aligned, config.range_mr,
        market_regime=regime_aligned, regime_gate=config.regime_gates,
    )
    v_rebound_df = evaluate_v_rebound(
        df["open"], df["high"], df["low"], df["close"], df["volume"], benchmark_aligned, config.v_rebound,
        market_regime=regime_aligned, regime_gate=config.regime_gates,
    )

    last_date = df.index[-1].date()
    range_mr_signal = range_mr_row_to_signal(symbol, last_date, range_mr_df.iloc[-1], name=name)
    v_rebound_signal = v_rebound_row_to_signal(symbol, last_date, v_rebound_df.iloc[-1], name=name)
    return range_mr_signal, v_rebound_signal


def run_daily_screen(
    top_n: Optional[int] = None,
    limit: Optional[int] = None,
    db_path: Optional[str] = None,
) -> dict:
    """유니버스 조회 -> 지수/종목 데이터 조회 -> 신호 계산 -> 저장까지 전체
    배치를 실행한다 (네트워크 호출 — 단위 테스트 대상 아님).

    한 종목 처리 중 오류가 나도 전체가 죽지 않고 건너뛴다(개발 원칙 33조
    "오류를 조용히 무시하지 말고 명확하게 로그를 남긴다" — 로그는 남기되
    나머지 종목은 계속 처리한다).
    """
    config = load_config()
    top_n = top_n or config.universe.top_n_by_market_cap

    end = date_.today()
    start = end - timedelta(days=config.data.history_calendar_days)

    logger.info("코스피/코스닥 지수 데이터 조회 중...")
    kospi_close = pd.Series(
        {b.date: b.close for b in fetch_index_ohlcv(KOSPI_INDEX_CODE, start, end)}
    )
    kosdaq_close = pd.Series(
        {b.date: b.close for b in fetch_index_ohlcv(KOSDAQ_INDEX_CODE, start, end)}
    )
    kospi_close.index = pd.to_datetime(kospi_close.index)
    kosdaq_close.index = pd.to_datetime(kosdaq_close.index)

    kospi_regime = compute_regime_series(kospi_close, config.market_regime)["regime"]
    kosdaq_regime = compute_regime_series(kosdaq_close, config.market_regime)["regime"]

    logger.info("유니버스(시총 상위 %d) 조회 중...", top_n)
    universe = fetch_kr_universe(top_n)
    if limit:
        universe = universe.head(limit)

    engine = get_engine(db_path) if db_path else get_engine()
    session_factory = get_session_factory(engine)
    session = session_factory()

    summary = {"processed": 0, "errors": 0, "range_mr_signals": 0, "v_rebound_signals": 0}
    try:
        for row in universe.itertuples(index=False):
            symbol = row.Code
            name = getattr(row, "Name", None)
            market = getattr(row, "Market", "KOSPI")
            benchmark_close = kosdaq_close if market == "KOSDAQ" else kospi_close
            regime_series = kosdaq_regime if market == "KOSDAQ" else kospi_regime
            try:
                bars = fetch_ohlcv(symbol, start, end)
                range_mr_signal, v_rebound_signal = process_symbol(
                    symbol, bars, benchmark_close, regime_series, config, name=name
                )
                if range_mr_signal is not None:
                    save_signal(session, range_mr_signal)
                    summary["range_mr_signals"] += 1
                if v_rebound_signal is not None:
                    save_signal(session, v_rebound_signal)
                    summary["v_rebound_signals"] += 1
                summary["processed"] += 1
            except Exception:
                logger.exception("%s 처리 중 오류 발생 — 건너뜀", symbol)
                summary["errors"] += 1
        session.commit()
    finally:
        session.close()

    logger.info("완료: %s", summary)
    return summary
