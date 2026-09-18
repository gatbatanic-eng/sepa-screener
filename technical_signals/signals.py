"""
technical_signals/signals.py — 지표 시계열 → 신호 판정 + 복합점수
=====================================================================

각 지표는 독립된 불리언 "최근 신호 발생"으로 판정하며, 그 자체로 매수를
권유하지 않는다(SEPA `sepa/setup.py`의 setup_quality_score와 동일한 철학 —
compositeScore는 랭킹용일 뿐 하드 게이트가 아니다).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import config as cfg
from indicators import (
    adx, bollinger, bollinger_bandwidth, crossed_down_recent, crossed_up_recent,
    disparity, macd, obv, rsi, sma, stochastic,
)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class SignalResult:
    close: float | None = None

    sma_fast: float | None = None
    sma_slow: float | None = None
    golden_cross: bool | None = None
    dead_cross: bool | None = None

    macd_line: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    macd_bull_cross: bool | None = None
    macd_bear_cross: bool | None = None

    rsi_value: float | None = None
    rsi_oversold_exit: bool | None = None
    rsi_overbought: bool | None = None

    stoch_k: float | None = None
    stoch_d: float | None = None
    stoch_bull_cross: bool | None = None

    bb_mid: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    bb_bandwidth: float | None = None
    bb_lower_revert: bool | None = None
    bb_squeeze: bool | None = None
    bb_upper_breakout: bool | None = None

    obv_rising: bool | None = None

    adx_value: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    adx_trending: bool | None = None

    disparity20: float | None = None

    momentum_trigger: bool | None = None
    volume_confirm: bool | None = None
    go_signal: bool | None = None
    go_reasons: list[str] = field(default_factory=list)

    composite_score: float | None = None
    reasons: list[str] = field(default_factory=list)


def evaluate_signals(ohlcv: pd.DataFrame) -> SignalResult:
    """ohlcv: 컬럼 Open/High/Low/Close/Volume, DatetimeIndex 오름차순."""
    r = SignalResult()
    n = len(ohlcv)
    if n < cfg.MIN_TRADING_DAYS:
        r.reasons.append(f"데이터 부족 ({n}봉 < {cfg.MIN_TRADING_DAYS})")
        return r

    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)
    close = ohlcv["Close"].astype(float)
    volume = ohlcv["Volume"].astype(float)
    r.close = float(close.iloc[-1])

    # --- 골든/데드크로스 ---
    sma_fast = sma(close, cfg.MA_FAST)
    sma_slow = sma(close, cfg.MA_SLOW)
    if not pd.isna(sma_fast.iloc[-1]) and not pd.isna(sma_slow.iloc[-1]):
        r.sma_fast = round(float(sma_fast.iloc[-1]), 4)
        r.sma_slow = round(float(sma_slow.iloc[-1]), 4)
        r.golden_cross = bool(crossed_up_recent(sma_fast, sma_slow, cfg.CROSS_RECENT_WINDOW).iloc[-1])
        r.dead_cross = bool(crossed_down_recent(sma_fast, sma_slow, cfg.CROSS_RECENT_WINDOW).iloc[-1])

    # --- MACD ---
    macd_line, macd_signal, macd_hist = macd(close, cfg.MACD_FAST, cfg.MACD_SLOW, cfg.MACD_SIGNAL)
    if not pd.isna(macd_line.iloc[-1]) and not pd.isna(macd_signal.iloc[-1]):
        r.macd_line = round(float(macd_line.iloc[-1]), 4)
        r.macd_signal = round(float(macd_signal.iloc[-1]), 4)
        r.macd_hist = round(float(macd_hist.iloc[-1]), 4)
        r.macd_bull_cross = bool(crossed_up_recent(macd_line, macd_signal, cfg.MACD_RECENT_WINDOW).iloc[-1])
        r.macd_bear_cross = bool(crossed_down_recent(macd_line, macd_signal, cfg.MACD_RECENT_WINDOW).iloc[-1])

    # --- RSI ---
    rsi_series = rsi(close, cfg.RSI_PERIOD)
    if not pd.isna(rsi_series.iloc[-1]):
        r.rsi_value = round(float(rsi_series.iloc[-1]), 2)
        oversold_line = pd.Series(cfg.RSI_OVERSOLD, index=rsi_series.index)
        r.rsi_oversold_exit = bool(crossed_up_recent(rsi_series, oversold_line, cfg.RSI_RECENT_WINDOW).iloc[-1])
        r.rsi_overbought = r.rsi_value >= cfg.RSI_OVERBOUGHT

    # --- 스토캐스틱 ---
    stoch_k, stoch_d = stochastic(high, low, close, cfg.STOCH_K_PERIOD, cfg.STOCH_K_SMOOTH, cfg.STOCH_D_SMOOTH)
    if not pd.isna(stoch_k.iloc[-1]) and not pd.isna(stoch_d.iloc[-1]):
        r.stoch_k = round(float(stoch_k.iloc[-1]), 2)
        r.stoch_d = round(float(stoch_d.iloc[-1]), 2)
        crossed = crossed_up_recent(stoch_k, stoch_d, cfg.STOCH_RECENT_WINDOW)
        was_oversold = (stoch_d.rolling(cfg.STOCH_RECENT_WINDOW, min_periods=1).min() <= cfg.STOCH_OVERSOLD)
        r.stoch_bull_cross = bool(crossed.iloc[-1] and was_oversold.iloc[-1])

    # --- 볼린저밴드 ---
    bb_mid, bb_upper, bb_lower = bollinger(close, cfg.BB_PERIOD, cfg.BB_STD)
    if not pd.isna(bb_mid.iloc[-1]) and not pd.isna(bb_upper.iloc[-1]) and not pd.isna(bb_lower.iloc[-1]):
        r.bb_mid = round(float(bb_mid.iloc[-1]), 4)
        r.bb_upper = round(float(bb_upper.iloc[-1]), 4)
        r.bb_lower = round(float(bb_lower.iloc[-1]), 4)
        bandwidth = bollinger_bandwidth(bb_mid, bb_upper, bb_lower)
        if not pd.isna(bandwidth.iloc[-1]):
            r.bb_bandwidth = round(float(bandwidth.iloc[-1]), 4)
            recent_bw = bandwidth.iloc[-cfg.BB_SQUEEZE_LOOKBACK:]
            r.bb_squeeze = bool(recent_bw.notna().sum() >= cfg.BB_PERIOD and
                                 bandwidth.iloc[-1] <= recent_bw.min())
        r.bb_lower_revert = bool(crossed_up_recent(close, bb_lower, cfg.CROSS_RECENT_WINDOW).iloc[-1])

        avg_vol20 = volume.rolling(20, min_periods=20).mean()
        if not pd.isna(avg_vol20.iloc[-1]) and avg_vol20.iloc[-1] > 0:
            vol_ratio = volume.iloc[-1] / avg_vol20.iloc[-1]
            r.bb_upper_breakout = bool(close.iloc[-1] > bb_upper.iloc[-1] and
                                        vol_ratio >= cfg.BB_BREAKOUT_VOL_RATIO)

    # --- OBV ---
    obv_series = obv(close, volume)
    obv_sma = sma(obv_series, cfg.OBV_SMA_PERIOD)
    if not pd.isna(obv_sma.iloc[-1]):
        r.obv_rising = bool(obv_series.iloc[-1] > obv_sma.iloc[-1])

    # --- ADX ---
    adx_series, plus_di, minus_di = adx(high, low, close, cfg.ADX_PERIOD)
    if not pd.isna(adx_series.iloc[-1]):
        r.adx_value = round(float(adx_series.iloc[-1]), 2)
        r.plus_di = round(float(plus_di.iloc[-1]), 2) if not pd.isna(plus_di.iloc[-1]) else None
        r.minus_di = round(float(minus_di.iloc[-1]), 2) if not pd.isna(minus_di.iloc[-1]) else None
        r.adx_trending = r.adx_value >= cfg.ADX_TRENDING_MIN

    # --- 이격도(정보용) ---
    disp_ma = sma(close, cfg.DISPARITY_MA_PERIOD)
    disp = disparity(close, disp_ma)
    if not pd.isna(disp.iloc[-1]):
        r.disparity20 = round(float(disp.iloc[-1]), 2)

    r.composite_score = _composite_score(r)
    r.go_signal, r.momentum_trigger, r.volume_confirm, r.go_reasons = _go_signal(r)
    return r


def _go_signal(r: SignalResult) -> tuple[bool | None, bool | None, bool | None, list[str]]:
    """4개 카테고리(추세/모멘텀/거래량/추세강도)를 전부 AND로 묶은 하드 조합
    신호. 모멘텀·거래량 카테고리 내부는 OR(서로 상관관계 높은 지표를 전부
    요구하면 사실상 안 뜨므로) — 사용자와 논의해 정한 조합이다:
      추세: 골든크로스(최근)
      모멘텀: MACD매수돌파 / RSI회복 / 스토캐스틱매수 중 하나
      거래량: OBV상승 / 볼린저상단돌파 중 하나
      필터: ADX>=25 (추세 있을 때만 신뢰, 횡보장 속임수 신호 배제용)
    SEPA GO_BREAKOUT과 같은 철학(하드 AND)이지만, 이 역시 '여러 각도에서
    동시에 확인된 후보'일 뿐 매수 확정 신호가 아니다. 4개 카테고리 중
    하나라도 판정 불가(None)면 전체가 None(억지로 False 아님)."""
    momentum_inputs = [r.macd_bull_cross, r.rsi_oversold_exit, r.stoch_bull_cross]
    momentum = any(v is True for v in momentum_inputs) if any(v is not None for v in momentum_inputs) else None

    volume_inputs = [r.obv_rising, r.bb_upper_breakout]
    volume = any(v is True for v in volume_inputs) if any(v is not None for v in volume_inputs) else None

    parts = {"golden_cross": r.golden_cross, "momentum": momentum,
             "volume": volume, "adx_trending": r.adx_trending}
    if any(v is None for v in parts.values()):
        return None, momentum, volume, []

    reasons = []
    if r.golden_cross:
        reasons.append("골든크로스")
    if r.macd_bull_cross:
        reasons.append("MACD매수돌파")
    if r.rsi_oversold_exit:
        reasons.append("RSI회복")
    if r.stoch_bull_cross:
        reasons.append("스토캐스틱매수")
    if r.obv_rising:
        reasons.append("OBV상승")
    if r.bb_upper_breakout:
        reasons.append("볼린저상단돌파")
    if r.adx_trending:
        reasons.append("ADX추세확인")

    return all(parts.values()), momentum, volume, reasons


def _composite_score(r: SignalResult) -> float | None:
    w = cfg.COMPOSITE_WEIGHTS
    subs: dict[str, float] = {}

    if r.golden_cross is not None:
        subs["golden_cross"] = 1.0 if r.golden_cross else 0.0
    if r.macd_bull_cross is not None:
        subs["macd_bull"] = 1.0 if r.macd_bull_cross else 0.0
    if r.rsi_oversold_exit is not None:
        subs["rsi_recover"] = 1.0 if r.rsi_oversold_exit else 0.0
    if r.stoch_bull_cross is not None:
        subs["stoch_bull"] = 1.0 if r.stoch_bull_cross else 0.0
    if r.bb_lower_revert is not None or r.bb_upper_breakout is not None:
        subs["bb_signal"] = 1.0 if (r.bb_lower_revert or r.bb_upper_breakout) else 0.0
    if r.obv_rising is not None:
        subs["obv_rising"] = 1.0 if r.obv_rising else 0.0
    if r.adx_trending is not None:
        subs["adx_trending"] = 1.0 if r.adx_trending else 0.0

    if not subs:
        return None
    total_w = sum(w[k] for k in subs)
    if total_w <= 0:
        return None
    score = sum(subs[k] * w[k] for k in subs) / total_w * 100.0
    return round(score, 1)
