"""momentum_signals/signals.py — 종목별 causal 지표 계산(RS 제외).

RS_SCORE는 유니버스 전체의 수익률을 모아야 계산되는 cross-sectional 값이라
rs.py에서 따로 계산해 여기 결과에 나중에 합친다(pipeline.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import config as cfg
from indicators import (atr, avg_trading_value, clv, disparity, rolling_high, rsi, sma,
                         swing_low, volume_sma)


@dataclass
class StockMetrics:
    code: str = ""
    name: str = ""
    market: str = ""
    status: str = "OK"
    reason: str | None = None

    close: float | None = None
    volume: float | None = None
    volume_sma50: float | None = None
    volume_ratio: float | None = None
    avg_trading_value20: float | None = None

    high20: float | None = None
    pivot_distance_pct: float | None = None   # (close-high20)/high20*100

    clv_value: float | None = None
    rsi_value: float | None = None
    disparity20: float | None = None

    atr_value: float | None = None
    swing_low_price: float | None = None
    stop_price: float | None = None            # ATR*2 손절가와 스윙로우 중 "더 타이트한"(가격에 더 가까운) 쪽
    risk_pct: float | None = None               # (close-stop)/close*100

    return_by_period: dict = field(default_factory=dict)  # rs.py 입력용 원자료
    rs_score: float | None = None                # pipeline.py가 rs.py 결과로 채움


def evaluate(code: str, name: str, market: str, ohlcv: pd.DataFrame) -> StockMetrics:
    m = StockMetrics(code=code, name=name, market=market)
    n = len(ohlcv)
    if n < cfg.MIN_TRADING_DAYS:
        m.status, m.reason = "확인불가", f"데이터 부족 ({n}봉 < {cfg.MIN_TRADING_DAYS})"
        return m

    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)
    close = ohlcv["Close"].astype(float)
    volume = ohlcv["Volume"].astype(float)

    m.close = float(close.iloc[-1])

    vol_sma = volume_sma(volume, cfg.VOLUME_SMA_PERIOD)
    if pd.notna(vol_sma.iloc[-1]) and vol_sma.iloc[-1] > 0:
        m.volume = float(volume.iloc[-1])
        m.volume_sma50 = round(float(vol_sma.iloc[-1]), 2)
        m.volume_ratio = round(m.volume / m.volume_sma50, 4)

    atv = avg_trading_value(close, volume, cfg.LIQUIDITY_LOOKBACK_DAYS)
    if pd.notna(atv.iloc[-1]):
        m.avg_trading_value20 = round(float(atv.iloc[-1]), 2)

    high20 = rolling_high(high, cfg.PIVOT_LOOKBACK)
    if pd.notna(high20.iloc[-1]) and high20.iloc[-1] > 0:
        m.high20 = round(float(high20.iloc[-1]), 4)
        m.pivot_distance_pct = round((m.close - m.high20) / m.high20 * 100.0, 3)

    clv_series = clv(high, low, close)
    if pd.notna(clv_series.iloc[-1]):
        m.clv_value = round(float(clv_series.iloc[-1]), 4)

    rsi_series = rsi(close, cfg.RSI_PERIOD)
    if pd.notna(rsi_series.iloc[-1]):
        m.rsi_value = round(float(rsi_series.iloc[-1]), 2)

    disp = disparity(close, cfg.DISPARITY_MA_PERIOD)
    if pd.notna(disp.iloc[-1]):
        m.disparity20 = round(float(disp.iloc[-1]), 3)

    atr_series = atr(high, low, close, cfg.ATR_PERIOD)
    swing_series = swing_low(low, cfg.SWING_LOW_LOOKBACK)
    if pd.notna(atr_series.iloc[-1]) and pd.notna(swing_series.iloc[-1]):
        m.atr_value = round(float(atr_series.iloc[-1]), 4)
        m.swing_low_price = round(float(swing_series.iloc[-1]), 4)
        atr_stop = m.close - cfg.ATR_STOP_MULT * m.atr_value
        # "더 타이트한 쪽" = 진입가에 더 가까운(=더 높은) 손절가.
        candidates = [v for v in (atr_stop, m.swing_low_price) if v > 0]
        if candidates:
            m.stop_price = round(max(candidates), 4)
            if m.close > 0:
                m.risk_pct = round((m.close - m.stop_price) / m.close * 100.0, 3)

    for lb in cfg.RS_TREND_LOOKBACK:
        base = close.shift(lb)
        val = (close.iloc[-1] / base.iloc[-1] - 1.0) * 100.0 if len(base) > lb and pd.notna(base.iloc[-1]) else None
        m.return_by_period[lb] = None if val is None or pd.isna(val) else float(val)

    return m
