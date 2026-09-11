"""
sepa/exit.py — EXIT 엔진
========================

매도를 하나의 조건으로 처리하지 않고 유형별로 분리한다 (스펙 10조).

- FAST_FAIL     : 돌파 직후 실패 → 빠른 손절
- STOP          : 구조적 손절가 이탈 (진입가/포지션 필요)
- TREND_BREAK   : SMA50 대량거래 이탈 등 추세 훼손
- TIME_STOP     : 진입 후 무성과 + RS 약화 (진입일/포지션 필요)
- PROFIT_ALERT  : 클라이맥스/과이격 — **경고만**, 강제매도 아님

포지션(진입가·진입일)이 없으면 :func:`detect_exit_warnings` 로 **가격행동 기반
경고**까지만 낸다. 포지션이 생기면 :func:`evaluate_exit` 가 STOP/TIME_STOP/MFE
까지 판정한다. 없는 포지션 상태를 임의로 만들지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from sepa import states
from sepa.config import SepaConfig
from sepa.indicators import atr, ema, pct_return, rolling_mean, sma, upper_wick_ratio
from sepa.swings import detect_swings


@dataclass
class Position:
    symbol: str
    entry_price: float
    entry_date: date | datetime | pd.Timestamp
    peak_price: float | None = None       # 진입 이후 최고가 (없으면 가격이력에서 계산)
    entry_pivot: float | None = None


@dataclass
class ExitResult:
    exit_state: str | None = None                 # 가장 심각한 경고 (없으면 HOLD 또는 None)
    warnings: list[str] = field(default_factory=list)
    structural_stop_price: float | None = None
    swing_low_price: float | None = None
    initial_risk_pct: float | None = None         # (기준가 - 손절가)/기준가 * 100
    entry_risk_flag: str | None = None            # None | "ENTRY_RISK_TOO_HIGH"
    mfe_pct: float | None = None
    days_held: int | None = None
    reasons: list[str] = field(default_factory=list)


def _consecutive_below(series: pd.Series, level: pd.Series | float, days: int) -> bool:
    if len(series) < days:
        return False
    tail_s = series.iloc[-days:]
    tail_l = level.iloc[-days:] if isinstance(level, pd.Series) else pd.Series(level, index=tail_s.index)
    if tail_s.isna().any() or tail_l.isna().any():
        return False
    return bool((tail_s < tail_l).all())


def structural_stop(ohlcv: pd.DataFrame, cfg: SepaConfig) -> tuple[float | None, float | None]:
    """
    마지막 의미있는 스윙 저점 약간 아래를 구조적 손절가로 삼는다.
    반환: (stop_price, swing_low_price). 스윙 저점을 못 찾으면 (None, None).
    """
    x = cfg.exit
    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)
    close = ohlcv["Close"].astype(float)
    n = len(close)
    lb = min(cfg.swing.base_lookback, n)
    swings = detect_swings(high.iloc[n - lb:], low.iloc[n - lb:],
                           cfg.swing.fractal_left, cfg.swing.fractal_right)
    lows = [s for s in swings if s.kind == "L"]
    if not lows:
        return None, None
    swing_low = float(lows[-1].price)

    a20 = atr(high, low, close, x.trend_exit_ema_fast * 2)  # ATR20 근사
    atr_buf = float(a20.iloc[-1]) * x.stop_buffer_atr_mult if not pd.isna(a20.iloc[-1]) else 0.0
    pct_buf = swing_low * x.stop_buffer_pct / 100.0
    stop = swing_low - max(atr_buf, pct_buf)
    return round(stop, 4), round(swing_low, 4)


def _profit_alert(ohlcv: pd.DataFrame, cfg: SepaConfig) -> tuple[bool, list[str]]:
    x = cfg.exit
    close = ohlcv["Close"].astype(float)
    open_ = ohlcv["Open"].astype(float) if "Open" in ohlcv else close
    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)
    volume = ohlcv["Volume"].astype(float) if "Volume" in ohlcv else pd.Series(np.nan, index=close.index)
    n = len(close)
    hits: list[str] = []

    if n > x.profit_runup_days:
        ru = pct_return(close, x.profit_runup_days).iloc[-1]
        if not pd.isna(ru) and ru * 100.0 >= x.profit_runup_pct:
            hits.append(f"{x.profit_runup_days}일 +{ru * 100:.0f}% 급등")

    e10 = ema(close, x.trend_exit_ema_fast).iloc[-1]
    if not pd.isna(e10) and e10 > 0:
        ext = (float(close.iloc[-1]) / float(e10) - 1.0) * 100.0
        if ext >= x.profit_ext_from_ema10_pct:
            hits.append(f"EMA10 대비 +{ext:.0f}% 이격")

    av50 = rolling_mean(volume, cfg.setup.vol_dryup_slow).iloc[-1]
    if not pd.isna(av50) and av50 > 0 and not pd.isna(volume.iloc[-1]):
        vr = float(volume.iloc[-1]) / float(av50)
        if vr >= x.profit_climax_volume_mult:
            hits.append(f"당일 거래량 {vr:.1f}x")

    uw = upper_wick_ratio(float(open_.iloc[-1]), float(high.iloc[-1]),
                          float(low.iloc[-1]), float(close.iloc[-1]))
    if uw is not None and uw >= x.profit_upper_wick_ratio:
        hits.append("긴 윗꼬리")

    if n >= 2:
        prev_close = float(close.iloc[-2])
        o = float(open_.iloc[-1])
        c = float(close.iloc[-1])
        if prev_close > 0 and o >= prev_close * (1 + x.profit_gap_up_pct / 100.0) and c < o:
            hits.append("갭상승 후 종가약세")

    return (len(hits) >= 2), hits


def detect_exit_warnings(ohlcv: pd.DataFrame, cfg: SepaConfig, *,
                         pivot_price: float | None = None,
                         recent_breakout_days_ago: int | None = None,
                         rs_change_20d: float | None = None) -> ExitResult:
    """포지션 없이, 가격행동만으로 감지 가능한 매도 경고."""
    x = cfg.exit
    res = ExitResult()
    close = ohlcv["Close"].astype(float)
    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)
    volume = ohlcv["Volume"].astype(float) if "Volume" in ohlcv else pd.Series(np.nan, index=close.index)
    n = len(close)
    if n < x.trend_break_sma + 5:
        return res

    c = float(close.iloc[-1])
    sma50 = sma(close, x.trend_break_sma)
    ema10 = ema(close, x.trend_exit_ema_fast)
    ema20 = ema(close, x.trend_exit_ema_slow)
    av50 = rolling_mean(volume, cfg.setup.vol_dryup_slow).iloc[-1]
    vr_today = (float(volume.iloc[-1]) / float(av50)
                if not pd.isna(av50) and av50 > 0 and not pd.isna(volume.iloc[-1]) else None)

    warnings: list[str] = []

    # --- FAST_FAIL ---
    if (pivot_price is not None and recent_breakout_days_ago is not None
            and recent_breakout_days_ago <= x.fast_fail_window):
        cond_a = c < pivot_price and vr_today is not None and vr_today > x.fast_fail_volume_mult
        cond_b = _consecutive_below(close, float(pivot_price), x.fast_fail_consec_below)
        if cond_a or cond_b:
            warnings.append(states.FAST_FAIL)
            res.reasons.append(
                f"돌파 {recent_breakout_days_ago}일 후 피벗({pivot_price:.2f}) 재하회"
                + (f" + 거래량 {vr_today:.1f}x" if cond_a else " (2일 연속)"))

    # --- TREND_BREAK ---
    broke_sma50 = _consecutive_below(close, sma50, x.confirm_days)
    if broke_sma50 and vr_today is not None and vr_today >= x.trend_break_volume_mult:
        warnings.append(states.TREND_BREAK)
        res.reasons.append(f"SMA50 이탈 + 거래량 {vr_today:.1f}x ({x.confirm_days}일 확인)")
    elif broke_sma50:
        warnings.append(states.TREND_BREAK)
        res.reasons.append(f"SMA50 종가 이탈 ({x.confirm_days}일 확인)")

    # --- WATCH_EXIT (EMA10/EMA20 이탈, 아직 SMA50 위) ---
    if states.TREND_BREAK not in warnings:
        if _consecutive_below(close, ema20, x.confirm_days):
            warnings.append(states.WATCH_EXIT)
            res.reasons.append(f"EMA20 종가 이탈 ({x.confirm_days}일)")
        elif _consecutive_below(close, ema10, x.confirm_days):
            warnings.append(states.WATCH_EXIT)
            res.reasons.append(f"EMA10 종가 이탈 ({x.confirm_days}일)")

    # --- PROFIT_ALERT ---
    pa, pa_reasons = _profit_alert(ohlcv, cfg)
    if pa:
        warnings.append(states.PROFIT_ALERT)
        res.reasons.append("클라이맥스 신호: " + ", ".join(pa_reasons))

    # --- 구조적 손절가 (참고용, 포지션 없어도 레벨은 계산) ---
    stop, swing_low = structural_stop(ohlcv, cfg)
    res.structural_stop_price = stop
    res.swing_low_price = swing_low
    if stop is not None and c > 0:
        risk = (c - stop) / c * 100.0
        res.initial_risk_pct = round(risk, 2)
        if risk > x.max_initial_risk_pct:
            res.entry_risk_flag = "ENTRY_RISK_TOO_HIGH"

    res.warnings = warnings
    if warnings:
        res.exit_state = max(warnings, key=lambda w: states.EXIT_STATE_SEVERITY.get(w, 0))
    else:
        in_uptrend = (not pd.isna(sma50.iloc[-1]) and c > float(sma50.iloc[-1]))
        res.exit_state = states.HOLD if in_uptrend else None
    return res


def evaluate_exit(position: Position, ohlcv: pd.DataFrame, cfg: SepaConfig, *,
                  rs_change_20d: float | None = None,
                  pivot_price: float | None = None,
                  recent_breakout_days_ago: int | None = None) -> ExitResult:
    """포지션(진입가·진입일)을 알 때의 완전한 EXIT 판정."""
    x = cfg.exit
    res = detect_exit_warnings(ohlcv, cfg, pivot_price=pivot_price,
                               recent_breakout_days_ago=recent_breakout_days_ago,
                               rs_change_20d=rs_change_20d)
    close = ohlcv["Close"].astype(float)
    c = float(close.iloc[-1])

    # 보유 일수 / MFE
    entry_ts = pd.Timestamp(position.entry_date)
    held_idx = close.index[close.index >= entry_ts]
    if len(held_idx):
        res.days_held = int(len(held_idx))
        peak = position.peak_price
        if peak is None:
            peak = float(ohlcv.loc[held_idx, "High"].astype(float).max())
        if position.entry_price > 0:
            res.mfe_pct = round((peak / position.entry_price - 1.0) * 100.0, 2)

    # 진입가 기준 초기 리스크 재계산 (structural stop 대비)
    if res.structural_stop_price is not None and position.entry_price > 0:
        risk = (position.entry_price - res.structural_stop_price) / position.entry_price * 100.0
        res.initial_risk_pct = round(risk, 2)
        res.entry_risk_flag = "ENTRY_RISK_TOO_HIGH" if risk > x.max_initial_risk_pct else None

    # --- STOP ---
    if res.structural_stop_price is not None and c < res.structural_stop_price:
        res.warnings.append(states.STOP)
        res.reasons.append(f"구조적 손절가({res.structural_stop_price:.2f}) 이탈")

    # --- TIME_STOP ---
    if (res.days_held is not None and res.days_held >= x.time_stop_days
            and res.mfe_pct is not None and res.mfe_pct < x.time_stop_mfe_min
            and (rs_change_20d is None or rs_change_20d <= x.time_stop_rs_change_max)):
        res.warnings.append(states.TIME_STOP)
        res.reasons.append(
            f"{res.days_held}일 경과, MFE {res.mfe_pct}% < {x.time_stop_mfe_min}%"
            + (f", RS Δ20d {rs_change_20d}" if rs_change_20d is not None else ""))

    if res.warnings:
        res.exit_state = max(res.warnings, key=lambda w: states.EXIT_STATE_SEVERITY.get(w, 0))
    return res
