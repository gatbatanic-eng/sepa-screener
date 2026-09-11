"""
sepa/setup.py — SETUP 엔진
==========================

"완전한 Minervini VCP 재현" 이 아니다. 일봉 OHLCV 로 구현 가능한 deterministic
heuristic 이다 (스펙 5조). 스윙/수축 탐지는 :mod:`sepa.swings` 의 독립 함수를
쓰므로 나중에 교체하기 쉽다.

계산 지표
---------
base_length, range_10_pct, ATR20/ATR60, atr_contraction_ratio,
avg_volume_10/20/50, volume_dryup_ratio, pivot_price, pivot_distance_pct,
contraction_count, contraction_widths, setup_quality_score(0~100), setup_ready

SETUP_READY (hard 조건)
-----------------------
trend_ok AND base_length>=20 AND atr_contraction_ratio<=0.75
AND volume_dryup_ratio<=0.70 AND contraction_count>=2
(range_10 은 기본적으로 quality factor. cfg.setup.range10_hard_filter=True 면 hard)

setup_quality_score 는 **랭킹용** 이며 GO_BREAKOUT hard rule 을 대체하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from sepa.config import SepaConfig
from sepa.indicators import atr, rolling_high, rolling_low, rolling_mean
from sepa.pivot import compute_pivot
from sepa.swings import detect_contractions


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _lin(x: float, lo: float, hi: float) -> float:
    """x 를 [lo,hi] → [0,1] 로 선형 사상 후 clamp. lo>hi 면 반대 방향."""
    if lo == hi:
        return 0.0
    return _clamp01((x - lo) / (hi - lo))


@dataclass
class SetupResult:
    base_length: int | None = None
    range_10_pct: float | None = None
    atr20: float | None = None
    atr60: float | None = None
    atr_contraction_ratio: float | None = None
    avg_volume_10: float | None = None
    avg_volume_20: float | None = None
    avg_volume_50: float | None = None
    volume_dryup_ratio: float | None = None
    pivot_price: float | None = None
    pivot_distance_pct: float | None = None
    pivot_source: str | None = None
    contraction_count: int | None = None
    contraction_widths: list[float] = field(default_factory=list)
    setup_quality_score: float | None = None
    setup_ready: bool | None = None
    reasons: list[str] = field(default_factory=list)


def _base_length(high: pd.Series, pivot_price: float | None,
                 contraction_base_idx: int | None, cap: int) -> int | None:
    n = len(high)
    if contraction_base_idx is not None:
        return int(min(n - 1 - contraction_base_idx, cap))
    if pivot_price is None:
        return None
    # 피벗(저항) 아래에 머문 최근 연속 봉 수
    h = high.to_numpy(dtype=float)
    cnt = 0
    for v in h[::-1]:
        if np.isnan(v) or v >= pivot_price:
            break
        cnt += 1
        if cnt >= cap:
            break
    return cnt


def evaluate_setup(ohlcv: pd.DataFrame, cfg: SepaConfig, *,
                   trend_ok: bool | None,
                   high_proximity_ratio: float | None,
                   rs_score: float | None,
                   rs_change_20d: float | None,
                   rs_line_new_high: bool | None) -> SetupResult:
    """ohlcv: 컬럼 Open/High/Low/Close/Volume, DatetimeIndex 오름차순."""
    r = SetupResult()
    s = cfg.setup

    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)
    close = ohlcv["Close"].astype(float)
    volume = ohlcv["Volume"].astype(float) if "Volume" in ohlcv else pd.Series(dtype=float)
    n = len(close)
    if n < max(s.atr_slow, cfg.trend.week52_window) // 2:
        r.reasons.append("데이터 부족")
        return r

    # --- 피벗 ---
    piv = compute_pivot(high, low, close, cfg.pivot, cfg.swing)
    r.pivot_price = piv.pivot_price
    r.pivot_distance_pct = piv.pivot_distance_pct
    r.pivot_source = piv.source

    # --- 수축 ---
    contr = detect_contractions(high, low, cfg.swing)
    r.contraction_count = contr.count
    r.contraction_widths = contr.widths

    # --- base 길이 ---
    r.base_length = _base_length(high, r.pivot_price, contr.base_start_idx, s.max_base_length)

    # --- range_10 ---
    if n >= s.range10_window:
        hh = float(rolling_high(high, s.range10_window).iloc[-1])
        ll = float(rolling_low(low, s.range10_window).iloc[-1])
        if ll > 0:
            r.range_10_pct = round((hh / ll - 1.0) * 100.0, 2)

    # --- ATR 수축 ---
    a_fast = atr(high, low, close, s.atr_fast)
    a_slow = atr(high, low, close, s.atr_slow)
    if not pd.isna(a_fast.iloc[-1]) and not pd.isna(a_slow.iloc[-1]) and a_slow.iloc[-1] > 0:
        r.atr20 = round(float(a_fast.iloc[-1]), 4)
        r.atr60 = round(float(a_slow.iloc[-1]), 4)
        r.atr_contraction_ratio = round(r.atr20 / r.atr60, 3)

    # --- 거래량 dry-up ---
    if not volume.empty and not (volume.iloc[-s.vol_dryup_slow:] <= 0).all():
        av10 = rolling_mean(volume, s.vol_dryup_fast).iloc[-1]
        av20 = rolling_mean(volume, s.vol_dryup_mid).iloc[-1]
        av50 = rolling_mean(volume, s.vol_dryup_slow).iloc[-1]
        if not pd.isna(av10):
            r.avg_volume_10 = round(float(av10), 2)
        if not pd.isna(av20):
            r.avg_volume_20 = round(float(av20), 2)
        if not pd.isna(av50):
            r.avg_volume_50 = round(float(av50), 2)
        if r.avg_volume_10 is not None and r.avg_volume_50 not in (None, 0):
            r.volume_dryup_ratio = round(r.avg_volume_10 / r.avg_volume_50, 3)

    # --- SETUP_READY (hard) ---
    hard_inputs = {
        "trend_ok": trend_ok,
        "base_length": r.base_length,
        "atr_contraction_ratio": r.atr_contraction_ratio,
        "volume_dryup_ratio": r.volume_dryup_ratio,
        "contraction_count": r.contraction_count,
    }
    if any(v is None for v in hard_inputs.values()):
        r.setup_ready = None
        r.reasons.append("SETUP 판정 입력 부족: " +
                         ", ".join(k for k, v in hard_inputs.items() if v is None))
    else:
        min_contr = cfg.swing.min_contraction_count
        checks = {
            "trend_ok": trend_ok is True,
            f"base>={s.min_base_length}": r.base_length >= s.min_base_length,
            f"ATR수축<={s.atr_contraction_max}": r.atr_contraction_ratio <= s.atr_contraction_max,
            f"Dryup<={s.volume_dryup_max}": r.volume_dryup_ratio <= s.volume_dryup_max,
            f"수축>={min_contr}": r.contraction_count >= min_contr,
        }
        if s.range10_hard_filter and r.range_10_pct is not None:
            checks[f"range10<={s.range10_max}"] = r.range_10_pct <= s.range10_max
        r.setup_ready = all(checks.values())
        r.reasons.extend(f"{k}={'OK' if v else 'X'}" for k, v in checks.items())

    # --- setup_quality_score (랭킹용, 0~100) ---
    r.setup_quality_score = _quality_score(
        r, cfg, high_proximity_ratio, rs_score, rs_change_20d, rs_line_new_high)

    return r


def _quality_score(r: SetupResult, cfg: SepaConfig,
                   high_proximity_ratio: float | None,
                   rs_score: float | None,
                   rs_change_20d: float | None,
                   rs_line_new_high: bool | None) -> float | None:
    w = cfg.setup.quality_weights
    subs: dict[str, float] = {}

    if rs_score is not None:
        subs["rs"] = _clamp01(rs_score / 100.0)
    if rs_change_20d is not None:
        subs["rs_accel"] = _lin(rs_change_20d, -20.0, 20.0)
    if rs_line_new_high is not None:
        subs["rs_line_high"] = 1.0 if rs_line_new_high else 0.0
    if high_proximity_ratio is not None:
        subs["high_proximity"] = _lin(high_proximity_ratio, 0.70, 1.00)
    if r.atr_contraction_ratio is not None:
        subs["atr_contraction"] = _lin(r.atr_contraction_ratio, 1.0, 0.5)
    if r.volume_dryup_ratio is not None:
        subs["vol_dryup"] = _lin(r.volume_dryup_ratio, 1.0, 0.4)
    if r.contraction_count is not None:
        subs["contraction_count"] = _clamp01(r.contraction_count / 3.0)
    if r.range_10_pct is not None:
        subs["range_tightness"] = _lin(r.range_10_pct, 15.0, 3.0)
    if r.pivot_distance_pct is not None:
        d = r.pivot_distance_pct
        subs["pivot_proximity"] = 1.0 if -3.0 <= d <= 0.0 else _clamp01(1.0 - abs(d + 1.5) / 8.0)

    if not subs:
        return None
    total_w = sum(w[k] for k in subs)
    if total_w <= 0:
        return None
    score = sum(subs[k] * w[k] for k in subs) / total_w * 100.0
    return round(score, 1)
