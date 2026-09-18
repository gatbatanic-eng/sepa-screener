"""
technical_signals/indicators.py — 인과적(causal) 기술 지표
=============================================================

`sepa/indicators.py`와 동일한 원칙: rolling/ewm/shift만 사용해 인덱스 ``i``의
값이 ``i`` 이전(포함) 데이터만으로 결정되게 한다. 미래 봉이 과거 시점 값에
영향을 주지 않는다(`tests/test_indicators.py`의 look-ahead 회귀 테스트로
강제한다). 데이터가 부족하면 해당 구간은 NaN으로 남기고 억지로 채우지 않는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def macd(close: pd.Series, fast: int, slow: int, signal: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD 라인, 시그널선, 히스토그램(라인-시그널)을 반환한다."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder 지수평활 RSI. window 미만은 NaN."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0.0, 100.0)          # 손실이 0(계속 상승)이면 RSI=100
    out = out.where(~((avg_loss == 0.0) & (avg_gain == 0.0)), 50.0)  # 가격이 아예 안 움직였으면 중립값
    return out


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                k_period: int, k_smooth: int, d_smooth: int) -> tuple[pd.Series, pd.Series]:
    """슬로우 스토캐스틱(%K, %D). raw %K를 k_smooth로 한 번 평활한 것이 %K, 그걸
    다시 d_smooth로 평활한 것이 %D — 표준 "slow stochastic" 정의."""
    lowest_low = low.rolling(k_period, min_periods=k_period).min()
    highest_high = high.rolling(k_period, min_periods=k_period).max()
    rng = (highest_high - lowest_low).replace(0.0, np.nan)
    raw_k = (close - lowest_low) / rng * 100.0
    k = raw_k.rolling(k_smooth, min_periods=k_smooth).mean()
    d = k.rolling(d_smooth, min_periods=d_smooth).mean()
    return k, d


def bollinger(close: pd.Series, period: int, num_std: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    """중심선(SMA), 상단, 하단 밴드를 반환한다."""
    mid = sma(close, period)
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return mid, upper, lower


def bollinger_bandwidth(mid: pd.Series, upper: pd.Series, lower: pd.Series) -> pd.Series:
    """밴드폭 = (상단-하단)/중심선. 값이 작을수록 변동성 수축."""
    return (upper - lower) / mid.replace(0.0, np.nan)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume. 전일 대비 상승이면 +거래량, 하락이면 -거래량 누적."""
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range. 첫 봉은 전일 종가가 없어 (고가-저가)로 대체된다."""
    prev_close = close.shift(1)
    return pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ATR (Wilder 지수평활). window 미만은 NaN."""
    return true_range(high, low, close).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Wilder ADX. (ADX, +DI, -DI)를 반환한다."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr_ = atr(high, low, close, period)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_.replace(0.0, np.nan)

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan) * 100.0
    adx_ = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return adx_, plus_di, minus_di


def clv(high: float, low: float, close: float) -> float | None:
    """Close Location Value = (종가-저가)/(고가-저가). 고가==저가(상하한가 등)면 0.5.
    입력이 결측이면 None. 돌파 확인(당일 세게 끝까지 밀어올렸는지) 품질 체크용."""
    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in (high, low, close)):
        return None
    rng = high - low
    if rng <= 0:
        return 0.5
    return float((close - low) / rng)


def swing_low(low: pd.Series, lookback: int) -> pd.Series:
    """최근 lookback거래일(당일 포함) 중 최저 저가 — 지지선/손절 기준 근사치."""
    return low.rolling(lookback, min_periods=1).min()


def rolling_pivot_high(high: pd.Series, lookback: int) -> pd.Series:
    """전일까지의 최근 lookback거래일 고가 최고치(당일 제외, 인과적) — 돌파 판정용 피벗.
    당일 자기참조를 피한다(당일이 확인된 돌파일이면 그날의 고가 확장 자체가
    같은 날 피벗에 들어가 "돌파했는가" 판정을 스스로 깎아먹는 자기모순을 방지
    — SEPA sepa/setup.py의 "당일 제외" 원칙과 동일)."""
    return high.rolling(lookback, min_periods=lookback).max().shift(1)


def disparity(close: pd.Series, ma: pd.Series) -> pd.Series:
    """이격도(%) = (종가/이평선 - 1) * 100."""
    return (close / ma.replace(0.0, np.nan) - 1.0) * 100.0


def crossed_up_recent(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    """a가 b를 최근 window거래일 내(오늘 포함) 상향 돌파했으면 True.
    각 시점은 그 시점까지의 데이터만 본다(인과적)."""
    was_below_or_eq = (a.shift(1) <= b.shift(1))
    now_above = (a > b)
    crossed_today = (was_below_or_eq & now_above).astype(float)
    return crossed_today.rolling(window, min_periods=1).max().fillna(0.0) > 0


def crossed_down_recent(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    """a가 b를 최근 window거래일 내(오늘 포함) 하향 돌파했으면 True."""
    return crossed_up_recent(b, a, window)
