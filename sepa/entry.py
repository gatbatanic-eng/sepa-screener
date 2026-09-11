"""
sepa/entry.py — ENTRY 상태 머신
===============================

TREND + SETUP 을 통과한 종목을 피벗 거리로 구간 분류하고, 거래량·종가위치가
확인된 돌파(GO_BREAKOUT)와 돌파 후 건강한 눌림목(GO_PULLBACK)만 실제 진입
후보로 승격한다. 단순히 올랐다는 이유로 추격하지 않는다 (LATE / EXTENDED).

모든 판정은 마지막 봉(=오늘) 기준이며 미래 봉을 쓰지 않는다. 돌파 이력 스캔은
각 과거 봉 시점의 피벗/평균거래량을 그 시점까지의 데이터로 재계산한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from sepa import states
from sepa.config import SepaConfig
from sepa.indicators import clv, ema, rolling_mean
from sepa.pivot import compute_pivot

_BREAKOUT_ZONE = "BREAKOUT_ZONE"   # 내부 마커 (0 <= d <= go_max_pct)


def classify_pivot_zone(pivot_distance_pct: float | None, cfg: SepaConfig) -> str | None:
    if pivot_distance_pct is None:
        return None
    d = pivot_distance_pct
    e = cfg.entry
    if d < e.setup_below_pct:
        return states.SETUP
    if d < e.ready_min_pct:
        return states.WATCH
    if d < 0.0:
        return states.READY
    if d <= e.go_max_pct:
        return _BREAKOUT_ZONE
    if d <= e.late_max_pct:
        return states.LATE
    return states.EXTENDED


def _volume_ratio_50(volume: pd.Series, cfg: SepaConfig, end_pos: int) -> float | None:
    av50 = rolling_mean(volume.iloc[: end_pos + 1], cfg.setup.vol_dryup_slow).iloc[-1]
    if pd.isna(av50) or av50 <= 0:
        return None
    v = volume.iloc[end_pos]
    if pd.isna(v):
        return None
    return float(v / av50)


def confirmed_breakout_at(ohlcv: pd.DataFrame, pivot_price: float | None,
                          cfg: SepaConfig, end_pos: int) -> tuple[bool | None, dict]:
    """end_pos(positional) 봉이 '확인된 돌파' 였는지. 그 봉까지의 데이터만 사용."""
    info: dict = {"volume_ratio_50": None, "clv": None, "pivot_distance_pct": None}
    if pivot_price is None or pivot_price <= 0 or end_pos < 1:
        return None, info

    close = float(ohlcv["Close"].iloc[end_pos])
    high = float(ohlcv["High"].iloc[end_pos])
    low = float(ohlcv["Low"].iloc[end_pos])
    dist = (close / pivot_price - 1.0) * 100.0
    info["pivot_distance_pct"] = round(dist, 2)

    vr = _volume_ratio_50(ohlcv["Volume"].astype(float), cfg, end_pos) if "Volume" in ohlcv else None
    cv = clv(high, low, close)
    info["volume_ratio_50"] = round(vr, 2) if vr is not None else None
    info["clv"] = round(cv, 2) if cv is not None else None
    if vr is None or cv is None:
        return None, info

    ok = (0.0 <= dist <= cfg.entry.go_max_pct
          and vr >= cfg.entry.breakout_volume_min
          and cv >= cfg.entry.breakout_clv_min)
    return bool(ok), info


def recent_confirmed_breakout(ohlcv: pd.DataFrame, cfg: SepaConfig,
                              lookback: int) -> int | None:
    """최근 lookback 거래일 중 '확인된 돌파' 였던 가장 최근 봉의 '며칠 전' 값. 없으면 None."""
    n = len(ohlcv)
    for b in range(1, min(lookback, n - cfg.pivot.base_lookback) + 1):
        end_pos = n - 1 - b
        if end_pos < cfg.pivot.base_lookback:
            break
        piv = compute_pivot(ohlcv["High"].astype(float), ohlcv["Low"].astype(float),
                            ohlcv["Close"].astype(float), cfg.pivot, cfg.swing,
                            as_of_offset=b)
        ok, _ = confirmed_breakout_at(ohlcv, piv.pivot_price, cfg, end_pos)
        if ok:
            return b
    return None


@dataclass
class EntryResult:
    entry_state: str
    zone: str | None = None
    pivot_price: float | None = None
    pivot_distance_pct: float | None = None
    confirmed_breakout: bool | None = None
    breakout_volume_ratio: float | None = None
    breakout_clv: float | None = None
    recent_breakout_days_ago: int | None = None
    pullback: bool | None = None
    reasons: list[str] = field(default_factory=list)


def _pullback(ohlcv: pd.DataFrame, cfg: SepaConfig, pivot_price: float | None,
              rs_score: float | None, rs_change_20d: float | None,
              recent_breakout_days_ago: int | None = None) -> tuple[bool, list[str]]:
    e = cfg.entry
    reasons: list[str] = []
    n = len(ohlcv)
    if n < e.pullback_ema_slow + 5:
        return False, ["데이터 부족"]

    bo_ago = recent_breakout_days_ago
    if bo_ago is None or bo_ago > e.pullback_lookback:
        return False, [f"최근 {e.pullback_lookback}일 내 확인된 돌파 없음"]
    reasons.append(f"돌파 {bo_ago}일 전")

    close = ohlcv["Close"].astype(float)
    open_ = ohlcv["Open"].astype(float) if "Open" in ohlcv else close
    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)
    volume = ohlcv["Volume"].astype(float) if "Volume" in ohlcv else pd.Series(np.nan, index=close.index)

    c = float(close.iloc[-1])
    ema_f = ema(close, e.pullback_ema_fast).iloc[-1]
    ema_s = ema(close, e.pullback_ema_slow).iloc[-1]
    anchors = [a for a in (pivot_price, ema_f, ema_s) if a is not None and not pd.isna(a) and a > 0]
    if not anchors:
        return False, reasons + ["기준선 없음"]
    min_dist = min(abs(c / a - 1.0) * 100.0 for a in anchors)
    near = min_dist <= e.pullback_distance_pct
    reasons.append(f"기준선 최소이격 {min_dist:.1f}%{' OK' if near else ' X'}")

    av20 = rolling_mean(volume, e.pullback_ema_slow).iloc[-1]
    recent_vol = volume.iloc[-3:].mean()
    vol_ok = (not pd.isna(av20) and not pd.isna(recent_vol)
              and av20 > 0 and recent_vol <= e.pullback_volume_ratio * av20)
    reasons.append(f"조정 거래량 {'축소' if vol_ok else '미축소'}")

    up_close = c > float(close.iloc[-2])
    up_vs_open = c > float(open_.iloc[-1])
    cv = clv(float(high.iloc[-1]), float(low.iloc[-1]), c)
    clv_ok = cv is not None and cv >= e.pullback_clv_min
    rs_ok = ((rs_score is not None and rs_score >= e.pullback_rs_min)
             or (rs_change_20d is not None and rs_change_20d >= e.pullback_rs_change_floor))
    reasons.append(f"양봉/시가상회/CLV/RS: {up_close}/{up_vs_open}/{clv_ok}/{rs_ok}")

    ok = bool(near and vol_ok and up_close and up_vs_open and clv_ok and rs_ok)
    return ok, reasons


_UNSET = object()


def evaluate_entry(ohlcv: pd.DataFrame, cfg: SepaConfig, *,
                   trend_ok: bool | None,
                   setup_ready: bool | None,
                   rs_score: float | None,
                   rs_change_20d: float | None,
                   fast_fail: bool = False,
                   pivot_result=None,
                   recent_breakout_days_ago=_UNSET) -> EntryResult:
    close = ohlcv["Close"].astype(float)
    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)
    piv = pivot_result if pivot_result is not None else compute_pivot(
        high, low, close, cfg.pivot, cfg.swing)
    zone = classify_pivot_zone(piv.pivot_distance_pct, cfg)

    res = EntryResult(entry_state=states.TREND_FAIL, zone=zone,
                      pivot_price=piv.pivot_price,
                      pivot_distance_pct=piv.pivot_distance_pct)

    # 오늘 확인된 돌파?
    end_pos = len(ohlcv) - 1
    cbo, cbo_info = confirmed_breakout_at(ohlcv, piv.pivot_price, cfg, end_pos)
    res.confirmed_breakout = cbo
    res.breakout_volume_ratio = cbo_info["volume_ratio_50"]
    res.breakout_clv = cbo_info["clv"]

    rbo = (recent_confirmed_breakout(ohlcv, cfg, cfg.entry.pullback_lookback)
           if recent_breakout_days_ago is _UNSET else recent_breakout_days_ago)
    res.recent_breakout_days_ago = rbo

    pb, pb_reasons = _pullback(ohlcv, cfg, piv.pivot_price, rs_score, rs_change_20d,
                               recent_breakout_days_ago=rbo)
    res.pullback = pb

    # --- 상태 결정 ---
    reason: list[str] = []
    if fast_fail:
        res.entry_state = states.FAILED
        reason.append("최근 돌파 빠른 실패(FAST_FAIL)")
    elif trend_ok is not True:
        res.entry_state = states.TREND_FAIL
        reason.append("TREND TEMPLATE 미충족")
    elif pb:
        res.entry_state = states.GO_PULLBACK
        reason.append("돌파 후 눌림목 반등: " + "; ".join(pb_reasons))
    elif setup_ready is not True:
        res.entry_state = states.TREND_OK
        reason.append("TREND 통과, 셋업 미완성" if setup_ready is False else "셋업 판정 보류(데이터)")
    elif zone == states.SETUP:
        res.entry_state = states.SETUP
        reason.append(f"피벗 거리 {piv.pivot_distance_pct}% (셋업 형성)")
    elif zone == states.WATCH:
        res.entry_state = states.WATCH
        reason.append(f"피벗 거리 {piv.pivot_distance_pct}%")
    elif zone == states.READY:
        res.entry_state = states.READY
        reason.append(f"피벗 임박 {piv.pivot_distance_pct}%")
    elif zone == _BREAKOUT_ZONE:
        if cbo:
            res.entry_state = states.GO_BREAKOUT
            reason.append(f"확인된 돌파: 거래량 {res.breakout_volume_ratio}x, CLV {res.breakout_clv}")
        else:
            res.entry_state = states.BREAKOUT_UNCONFIRMED
            reason.append(f"돌파구간이나 미확인 (거래량 {res.breakout_volume_ratio}x / "
                          f"CLV {res.breakout_clv})")
    elif zone == states.LATE:
        res.entry_state = states.LATE
        reason.append(f"피벗 +{piv.pivot_distance_pct}% (추격 주의)")
    elif zone == states.EXTENDED:
        res.entry_state = states.EXTENDED
        reason.append(f"피벗 +{piv.pivot_distance_pct}% (연장 구간)")
    else:
        res.entry_state = states.TREND_OK
        reason.append("피벗 거리 판정 불가")

    res.reasons = reason
    return res
