"""
sepa/pipeline.py — 종목 1개 단위 v2 조립
========================================

``screening.py`` 가 호출하는 얇은 오케스트레이션 계층. 기존 스크리너가 이미
계산한 것(조건 1~7, 52주 고저가, OHLCV)을 받아 v2 상태(TREND v2 / SETUP /
ENTRY / EXIT warning)를 조립해 **평평한 dict** 로 돌려준다.

- 네트워크·파일 IO 없음.
- RS v2 percentile 은 유니버스 전체가 필요하므로 :func:`compute_rs_v2` (rs.py)
  를 별도로 먼저 호출한다. 이 모듈의 :func:`evaluate_stock_v2` 는 그 결과
  (``RsV2``) 를 입력으로 받는다.
"""
from __future__ import annotations

import pandas as pd

from sepa import states
from sepa.config import SepaConfig
from sepa.entry import evaluate_entry, recent_confirmed_breakout
from sepa.exit import detect_exit_warnings
from sepa.pivot import compute_pivot
from sepa.rs import RsV2, compute_rs_v2  # noqa: F401  (screening.py 가 여기서 import)
from sepa.setup import evaluate_setup

__all__ = ["compute_rs_v2", "evaluate_stock_v2", "high_proximity_tier"]


def high_proximity_tier(ratio: float | None, cfg: SepaConfig) -> str | None:
    if ratio is None:
        return None
    t = cfg.trend
    if ratio >= t.high_proximity_super:
        return states.SUPER_LEADER
    if ratio >= t.high_proximity_leader:
        return states.LEADER
    if ratio >= t.high_proximity_min:
        return states.NORMAL
    return states.PROXIMITY_FAIL


def _all_true(vals: list[bool | None]) -> bool | None:
    if any(v is None for v in vals):
        return None
    return all(vals)


# evaluate_stock_v2 가 항상 내보내는 키 (데이터 부족 시에도 키는 존재, 값만 None)
_V2_KEYS = (
    "trend_ok", "cond8_v2", "rs_score", "rs_score_20d_ago", "rs_change_20d",
    "rs_line_new_high", "er21", "er63", "er126", "er252",
    "high_proximity_ratio", "high_proximity_tier",
    "base_length", "range_10_pct", "atr20", "atr60", "atr_contraction_ratio",
    "avg_volume_10", "avg_volume_20", "avg_volume_50", "volume_dryup_ratio",
    "pivot_price", "pivot_distance_pct", "pivot_source",
    "contraction_count", "contraction_widths",
    "setup_ready", "setup_quality_score",
    "zone", "confirmed_breakout", "breakout_volume_ratio", "breakout_clv",
    "recent_breakout_days_ago", "pullback",
    "entry_state", "entry_state_reason",
    "exit_state", "exit_warnings", "exit_state_reason",
    "structural_stop_price", "swing_low_price", "initial_risk_pct", "entry_risk_flag",
)


def evaluate_stock_v2(ohlcv: pd.DataFrame, cfg: SepaConfig, *,
                      cond_1_7: list[bool | None],
                      close_today: float | None,
                      high_52w: float | None,
                      rs: RsV2 | None) -> dict:
    """
    반환 dict 키 (전부 v2 접두 없이, screening.py 가 한국어 컬럼으로 매핑):

      trend_ok, cond8_v2, rs_score, rs_score_20d_ago, rs_change_20d,
      rs_line_new_high, er21..er252,
      high_proximity_ratio, high_proximity_tier,
      base_length, range_10_pct, atr20, atr60, atr_contraction_ratio,
      avg_volume_10/20/50, volume_dryup_ratio,
      pivot_price, pivot_distance_pct, pivot_source,
      contraction_count, contraction_widths,
      setup_ready, setup_quality_score,
      zone, confirmed_breakout, breakout_volume_ratio, breakout_clv,
      recent_breakout_days_ago, pullback,
      entry_state, entry_state_reason,
      exit_state, exit_warnings, exit_state_reason,
      structural_stop_price, swing_low_price, initial_risk_pct, entry_risk_flag
    """
    out: dict = {k: None for k in _V2_KEYS}
    out["contraction_widths"] = []
    out["exit_warnings"] = []
    out["entry_state_reason"] = []
    out["exit_state_reason"] = []
    rs = rs or RsV2()

    # --- TREND v2 (조건 1~7 유지 + 조건 8 = RS_SCORE >= rs_min) ---
    cond8_v2 = None if rs.rs_score is None else (rs.rs_score >= cfg.rs.rs_min)
    trend_ok = _all_true(list(cond_1_7) + [cond8_v2])
    out.update(trend_ok=trend_ok, cond8_v2=cond8_v2,
               rs_score=rs.rs_score, rs_score_20d_ago=rs.rs_score_20d_ago,
               rs_change_20d=rs.rs_change_20d, rs_line_new_high=rs.rs_line_new_high,
               er21=rs.er21, er63=rs.er63, er126=rs.er126, er252=rs.er252)

    high_prox = None
    if close_today and high_52w and high_52w > 0:
        high_prox = round(close_today / high_52w, 4)
    out.update(high_proximity_ratio=high_prox,
               high_proximity_tier=high_proximity_tier(high_prox, cfg))

    if ohlcv is None or len(ohlcv) < cfg.data.min_trading_days:
        # 데이터 부족 — v2 는 전부 None / 판정 보류
        out.update(entry_state=states.TREND_FAIL if trend_ok is False else None,
                   entry_state_reason=["데이터 부족"],
                   exit_state=None, exit_warnings=[], exit_state_reason=[])
        return out

    close = ohlcv["Close"].astype(float)
    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)

    piv = compute_pivot(high, low, close, cfg.pivot, cfg.swing)
    rbo = recent_confirmed_breakout(ohlcv, cfg, cfg.entry.pullback_lookback)

    # --- EXIT warning (포지션 없이) ---
    exitw = detect_exit_warnings(ohlcv, cfg, pivot_price=piv.pivot_price,
                                 recent_breakout_days_ago=rbo,
                                 rs_change_20d=rs.rs_change_20d)
    fast_fail = states.FAST_FAIL in exitw.warnings

    # --- SETUP ---
    setup = evaluate_setup(ohlcv, cfg, trend_ok=trend_ok,
                           high_proximity_ratio=high_prox,
                           rs_score=rs.rs_score, rs_change_20d=rs.rs_change_20d,
                           rs_line_new_high=rs.rs_line_new_high)
    out.update(base_length=setup.base_length, range_10_pct=setup.range_10_pct,
               atr20=setup.atr20, atr60=setup.atr60,
               atr_contraction_ratio=setup.atr_contraction_ratio,
               avg_volume_10=setup.avg_volume_10, avg_volume_20=setup.avg_volume_20,
               avg_volume_50=setup.avg_volume_50,
               volume_dryup_ratio=setup.volume_dryup_ratio,
               pivot_price=setup.pivot_price, pivot_distance_pct=setup.pivot_distance_pct,
               pivot_source=setup.pivot_source,
               contraction_count=setup.contraction_count,
               contraction_widths=setup.contraction_widths,
               setup_ready=setup.setup_ready, setup_quality_score=setup.setup_quality_score)

    # --- ENTRY ---
    entry = evaluate_entry(ohlcv, cfg, trend_ok=trend_ok, setup_ready=setup.setup_ready,
                           rs_score=rs.rs_score, rs_change_20d=rs.rs_change_20d,
                           fast_fail=fast_fail, pivot_result=piv,
                           recent_breakout_days_ago=rbo)
    out.update(zone=entry.zone, confirmed_breakout=entry.confirmed_breakout,
               breakout_volume_ratio=entry.breakout_volume_ratio,
               breakout_clv=entry.breakout_clv,
               recent_breakout_days_ago=entry.recent_breakout_days_ago,
               pullback=entry.pullback,
               entry_state=entry.entry_state, entry_state_reason=entry.reasons)

    out.update(exit_state=exitw.exit_state, exit_warnings=exitw.warnings,
               exit_state_reason=exitw.reasons,
               structural_stop_price=exitw.structural_stop_price,
               swing_low_price=exitw.swing_low_price,
               initial_risk_pct=exitw.initial_risk_pct,
               entry_risk_flag=exitw.entry_risk_flag)
    return out
