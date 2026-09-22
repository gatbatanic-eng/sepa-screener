"""momentum_signals/indicators.py — 순수 causal 지표 함수.

다른 서브시스템(technical_signals 등)과 동일한 관례: 서브시스템 간 import
없이 독립적으로 재구현한다. 모든 함수는 index i 시점 값이 i 이전 데이터에만
의존하도록 `.rolling()` / `.shift()` 만 쓴다(look-ahead 금지).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    a = high - low
    b = (high - prev_close).abs()
    c = (low - prev_close).abs()
    return pd.concat([a, b, c], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.where(avg_loss != 0.0, 100.0).where(avg_gain != 0.0, out).fillna(out)


def disparity(close: pd.Series, ma_period: int) -> pd.Series:
    ma = sma(close, ma_period)
    return (close - ma) / ma * 100.0


def clv(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    rng = (high - low).replace(0.0, np.nan)
    val = ((close - low) - (high - close)) / rng
    return val.fillna(0.0)


def rolling_high(high: pd.Series, lookback: int) -> pd.Series:
    """당일을 포함한 최근 lookback일 고점(오늘 종가의 피벗 대비 위치 계산용).
    당일을 포함해야 '오늘 신고가 돌파'를 감지할 수 있어, technical_signals의
    피벗(전일까지)과 달리 shift 하지 않는다 — look-ahead가 아니라 당일 확정
    데이터(장 마감 후 스크리닝)만 쓰므로 문제 없다."""
    return high.rolling(lookback, min_periods=lookback).max()


def swing_low(low: pd.Series, lookback: int) -> pd.Series:
    return low.rolling(lookback, min_periods=1).min()


def volume_sma(volume: pd.Series, period: int) -> pd.Series:
    return volume.rolling(period, min_periods=period).mean()


def avg_trading_value(close: pd.Series, volume: pd.Series, lookback: int) -> pd.Series:
    return (close * volume).rolling(lookback, min_periods=lookback).mean()


def period_return_pct(close: pd.Series, lookback: int) -> pd.Series:
    """lookback 거래일 전 대비 수익률(%). RS percentile 계산의 원재료."""
    base = close.shift(lookback)
    return (close / base - 1.0) * 100.0
