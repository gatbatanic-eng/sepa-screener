"""
sepa/regime.py — 시장 국면 (Market Regime)
==========================================

시장이 안 좋다고 모든 종목을 무조건 금지하는 binary gate 는 피한다 (스펙 11조).
국면을 4단계로 나누고, 국면 + breadth 로 **권장 신규진입 exposure(0~1)** 를
계산해 리스크에 연결한다. 실제 주문 기능은 없다 — recommendation 만.

국면
----
- GREEN     : index > SMA50 > SMA200
- YELLOW    : index > SMA200 이지만 SMA50 이 약화(SMA50 <= SMA200 또는 SMA50 하락)
- RED       : index < SMA200
- RECOVERY  : 오늘은 RED 아님 + 최근 ``recovery_lookback`` 거래일 내 RED 존재
             (RED 직후 회복 초기의 단순·인과적 근사)

우선순위: RED > RECOVERY > YELLOW > GREEN
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sepa import states
from sepa.config import RegimeConfig
from sepa.indicators import sma


@dataclass
class RegimeResult:
    regime: str | None = None
    breadth_50: float | None = None            # universe 중 close > SMA50 비율 (0~1)
    breadth_label: str | None = None
    entry_size_factor: float | None = None     # 권장 신규진입 exposure (0~1)
    index_above_sma50: bool | None = None
    index_above_sma200: bool | None = None
    sma50_weakening: bool | None = None
    days_since_red: int | None = None
    reasons: list[str] = None                  # type: ignore[assignment]

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


def _base_regime_series(index_close: pd.Series, cfg: RegimeConfig) -> pd.Series:
    s50 = sma(index_close, cfg.sma_fast)
    s200 = sma(index_close, cfg.sma_slow)
    s50_past = s50.shift(cfg.sma_fast_weak_lookback)

    out = pd.Series(index=index_close.index, dtype=object)
    for i in range(len(index_close)):
        c, a, b, ap = index_close.iloc[i], s50.iloc[i], s200.iloc[i], s50_past.iloc[i]
        if pd.isna(a) or pd.isna(b):
            out.iloc[i] = None
            continue
        if c < b:
            out.iloc[i] = states.RED
        elif a > b and not (not pd.isna(ap) and a < ap):
            out.iloc[i] = states.GREEN
        else:
            out.iloc[i] = states.YELLOW
    return out


def market_regime(index_close: pd.Series, cfg: RegimeConfig,
                  breadth_ratio: float | None = None) -> RegimeResult:
    r = RegimeResult()
    if index_close is None or len(index_close) < cfg.sma_slow + 5:
        r.reasons.append("지수 데이터 부족")
        r.breadth_50 = breadth_ratio
        return r

    base = _base_regime_series(index_close, cfg)
    today = base.iloc[-1]
    if today is None:
        r.reasons.append("SMA 계산 불가")
        return r

    s50 = sma(index_close, cfg.sma_fast)
    s200 = sma(index_close, cfg.sma_slow)
    c = float(index_close.iloc[-1])
    r.index_above_sma50 = bool(c > s50.iloc[-1]) if not pd.isna(s50.iloc[-1]) else None
    r.index_above_sma200 = bool(c > s200.iloc[-1]) if not pd.isna(s200.iloc[-1]) else None
    s50_past = s50.shift(cfg.sma_fast_weak_lookback).iloc[-1]
    r.sma50_weakening = bool(not pd.isna(s50_past) and s50.iloc[-1] < s50_past)

    # RECOVERY: 오늘 RED 아님 + 최근 recovery_lookback 내 RED
    regime = today
    if today != states.RED:
        recent = base.iloc[-(cfg.recovery_lookback + 1):-1]
        reds = [k for k, v in enumerate(recent[::-1], start=1) if v == states.RED]
        if reds:
            r.days_since_red = reds[0]
            regime = states.RECOVERY
            r.reasons.append(f"{reds[0]}거래일 전 RED → 회복 초기")

    r.regime = regime
    r.reasons.append(f"지수 {'>' if r.index_above_sma200 else '<'} SMA200, "
                     f"SMA50 {'약화' if r.sma50_weakening else '유지'}")

    # --- breadth ---
    r.breadth_50 = breadth_ratio
    if breadth_ratio is not None:
        if breadth_ratio >= cfg.breadth_strong:
            r.breadth_label = states.BREADTH_STRONG
        elif breadth_ratio >= cfg.breadth_normal:
            r.breadth_label = states.BREADTH_NORMAL
        elif breadth_ratio >= cfg.breadth_weak:
            r.breadth_label = states.BREADTH_WEAK
        else:
            r.breadth_label = states.BREADTH_RISK_OFF

    # --- exposure ---
    base_factor = cfg.exposure.get(regime, 0.5)
    if r.breadth_label == states.BREADTH_RISK_OFF:
        base_factor *= 0.5
    elif r.breadth_label == states.BREADTH_WEAK:
        base_factor *= 0.8
    r.entry_size_factor = round(min(1.0, max(0.0, base_factor)), 2)
    return r


def breadth_ratio_from_flags(above_sma50_flags: list[bool | None]) -> float | None:
    """[True/False/None, ...] → close>SMA50 비율. 유효값이 없으면 None."""
    valid = [f for f in above_sma50_flags if f is not None]
    if not valid:
        return None
    return round(sum(1 for f in valid if f) / len(valid), 4)
