"""RANGE-MR/V-REBOUND 지표·점수화 계산을 하나의 Signal 파이프라인으로
묶는 orchestrator (스펙 25/26조).

전략 계산(`src/strategies/`)과 점수화(`src/scoring/`)는 서로를 모르는
채로 설계했다 — 이 모듈이 둘을 조합해 매일자 `Signal`을 만든다. 백테스트
엔진(Phase 7)과 실시간 스크리닝(Phase 9)이 동일한 이 함수를 공유해야
스펙 33조("백테스트와 실시간 스크리닝 로직이 동일한 전략 엔진을 사용")를
지킨다.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from src.config import RangeMRConfig, RegimeGatesConfig, VReboundConfig
from src.indicators.volatility import atr as _atr
from src.market.regime import RegimeType
from src.models.signal import Signal, SignalState, StrategyName
from src.scoring.range_score import classify_signal as classify_range_mr_signal
from src.scoring.range_score import compute_setup_score, compute_trigger_score
from src.scoring.v_rebound_score import classify_signal as classify_v_rebound_signal
from src.scoring.v_rebound_score import compute_v_rebound_score, is_rebound_invalidated
from src.strategies.range_mr import (
    compute_box_metrics,
    compute_mean_reversion_metrics,
    compute_stop_target as compute_range_mr_stop_target,
    compute_trigger_flags,
    detect_support_touches,
    rolling_support_touch_count,
)
from src.strategies.v_rebound import (
    compute_breakout,
    compute_initial_filter,
    compute_stop_target as compute_v_rebound_stop_target,
    compute_volume_structure,
    track_stabilization,
)
from src.indicators.relative_strength import excess_return as _excess_return


def _apply_regime_gate(
    result: pd.DataFrame,
    market_regime: Optional[pd.Series],
    regime_gate: Optional[RegimeGatesConfig],
    allowed_regimes: list[str],
) -> None:
    """레짐이 전략 실행을 게이팅한다 (Phase 0 계획 제안 1).

    market_regime과 regime_gate가 둘 다 주어지고 게이트가 켜져 있을 때만
    적용한다 — 백테스트 등 레짐 정보 없이 순수 신호 품질만 볼 때는
    아무 영향을 주지 않는다(기존 호출부와 호환).
    """
    if market_regime is None or regime_gate is None or not regime_gate.enabled:
        return
    allowed = set(allowed_regimes)
    mask = market_regime.astype(str).isin(allowed)
    result.loc[~mask, "signal"] = None


def evaluate_range_mr(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    benchmark_close: pd.Series,
    config: RangeMRConfig,
    market_regime: Optional[pd.Series] = None,
    regime_gate: Optional[RegimeGatesConfig] = None,
) -> pd.DataFrame:
    """RANGE-MR 전 과정을 하루 단위로 계산한 DataFrame을 반환한다.

    entry는 포함하지 않는다 — 신호 생성 시점(T일 종가)에는 아직 알 수 없고
    (T+1일 시가, Phase 0 제안 3), 실제 체결가는 백테스트 엔진이 채운다.
    """
    box_df = compute_box_metrics(high, low, close, config.box)
    touch_events = detect_support_touches(low, close, box_df["box_low"], config.support)
    touch_count = rolling_support_touch_count(touch_events, config.box.period_days)
    mean_rev_df = compute_mean_reversion_metrics(close, box_df["box_midpoint"], config)
    trigger_flags = compute_trigger_flags(open_, high, low, close, volume, benchmark_close, config)
    trigger_score = compute_trigger_score(trigger_flags, config)

    atr_series = _atr(high, low, close, config.stop.atr_period)
    stop_target_df = compute_range_mr_stop_target(
        box_df["box_high"], box_df["box_low"], box_df["box_midpoint"], close, atr_series, config
    )

    dollar_volume = close * volume
    avg_trading_value_krw = dollar_volume.rolling(
        window=config.trigger.volume_avg_days, min_periods=config.trigger.volume_avg_days
    ).mean()

    span = box_df["box_midpoint"] - box_df["box_low"]
    reversion_ratio = (box_df["box_midpoint"] - close) / span

    setup_metrics = pd.DataFrame(
        {
            "box_width": box_df["box_width"],
            "box_position": box_df["box_position"],
            "support_touch_count": touch_count,
            "reversion_ratio": reversion_ratio,
            "rsi": mean_rev_df["rsi"],
            "avg_trading_value_krw": avg_trading_value_krw,
            "rr_1": stop_target_df["rr_1"],
        },
        index=close.index,
    )
    setup_result = compute_setup_score(setup_metrics, config)

    # 박스 자체가 아직 형성되지 않은 워밍업 구간(데이터 부족)은 "무효화"가
    # 아니라 "신호 없음"이어야 한다 — 박스가 없으면 무효화할 대상도 없다.
    has_box = box_df["box_width"].notna()
    is_invalidated = has_box & ((~box_df["passes_box_filter"]) | (close < stop_target_df["stop"]).fillna(False))

    signal_state = [
        classify_range_mr_signal(s, t, rr, bool(inv), config)
        for s, t, rr, inv in zip(setup_result["setup_score"], trigger_score, stop_target_df["rr_1"], is_invalidated)
    ]

    result = pd.DataFrame(
        {
            "setup_score": setup_result["setup_score"],
            "trigger_score": trigger_score,
            "total_score": setup_result["setup_score"],  # 스펙 11조: "총점"은 Setup Score 배점표 자체다
            "quality_status": setup_result["quality_status"],
            "signal": signal_state,
            "stop": stop_target_df["stop"],
            "target_1": stop_target_df["target_1"],
            "target_2": stop_target_df["target_2"],
            "rr_1": stop_target_df["rr_1"],
            "rr_2": stop_target_df["rr_2"],
            "box_position": box_df["box_position"],
            "box_width": box_df["box_width"],
            "support_touch_count": touch_count,
            "breakout": trigger_flags["breakout"],
            "volume_ratio": trigger_flags["volume_ratio"],
        },
        index=close.index,
    )
    if market_regime is not None:
        result["market_regime"] = market_regime
    _apply_regime_gate(result, market_regime, regime_gate, regime_gate.range_mr_regimes if regime_gate else [])
    return result


def range_mr_row_to_signal(symbol: str, date, row: pd.Series, name: Optional[str] = None) -> Optional[Signal]:
    if row["signal"] is None or (isinstance(row["signal"], float) and pd.isna(row["signal"])):
        return None
    reasons = [
        f"박스 포지션 = {row['box_position']:.0%}" if pd.notna(row["box_position"]) else "박스 포지션 = 확인불가",
        f"지지 터치 횟수 = {int(row['support_touch_count'])}회" if pd.notna(row["support_touch_count"]) else "지지 터치 횟수 = 확인불가",
        f"거래량 배수 = {row['volume_ratio']:.2f}배" if pd.notna(row["volume_ratio"]) else "거래량 배수 = 확인불가",
        f"3일 고점 돌파 = {'예' if bool(row['breakout']) else '아니오'}",
        f"손익비(R/R) = {row['rr_1']:.2f}" if pd.notna(row["rr_1"]) else "손익비(R/R) = 확인불가",
    ]
    return Signal(
        symbol=symbol,
        name=name,
        strategy=StrategyName.RANGE_MR,
        date=date,
        market_regime=row.get("market_regime", RegimeType.NORMAL),
        setup_score=row["setup_score"],
        trigger_score=row["trigger_score"],
        total_score=row["total_score"],
        signal=row["signal"],
        quality_status=row["quality_status"],
        stop=_none_if_nan(row["stop"]),
        target_1=_none_if_nan(row["target_1"]),
        target_2=_none_if_nan(row["target_2"]),
        rr_1=_none_if_nan(row["rr_1"]),
        rr_2=_none_if_nan(row["rr_2"]),
        reasons=reasons,
    )


def evaluate_v_rebound(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    benchmark_close: pd.Series,
    config: VReboundConfig,
    market_regime: Optional[pd.Series] = None,
    regime_gate: Optional[RegimeGatesConfig] = None,
) -> pd.DataFrame:
    """V-REBOUND 전 과정을 하루 단위로 계산한 DataFrame을 반환한다."""
    filter_df = compute_initial_filter(high, close, benchmark_close, config)
    stabilization_df = track_stabilization(low, high, close, filter_df["passes_filter"], config)
    volume_df = compute_volume_structure(volume, stabilization_df["candidate_low_idx"], config)
    rs_5d = _excess_return(close, benchmark_close, config.rs.days)

    n = len(close)
    breakout_flags = [
        compute_breakout(
            close.iloc[i],
            stabilization_df["first_rebound_high"].iloc[i],
            volume_df["rebound_volume_ratio"].iloc[i],
            rs_5d.iloc[i],
            config,
        )
        for i in range(n)
    ]
    breakout = pd.Series(breakout_flags, index=close.index)

    atr_series = _atr(high, low, close, config.stop.atr_period)
    stop_target_df = compute_v_rebound_stop_target(
        stabilization_df["confirmed_low_price"],
        stabilization_df["first_rebound_high"],
        filter_df["period_high"],
        close,
        atr_series,
        config,
    )

    score_metrics = pd.DataFrame(
        {
            "drawdown": filter_df["drawdown"],
            "excess_return_60d": filter_df["excess_return_60d"],
            "is_stabilized": stabilization_df["is_stabilized"],
            "recovered_3pct": stabilization_df["recovered_3pct"],
            "rebound_volume_ratio": volume_df["rebound_volume_ratio"],
            "rs_5d": rs_5d,
            "breakout": breakout,
        },
        index=close.index,
    )
    score_result = compute_v_rebound_score(score_metrics, config)

    # 이미 안정화(NEW_LOW=FALSE)로 확정됐던 저점이 재패닉으로 다시
    # 깨지는 경우만 무효화한다. 초기 필터를 더 이상 통과하지 못하는 것
    # (=주가가 충분히 회복함)은 무효화 사유가 아니다 — 그건 성공
    # 시나리오다.
    is_invalidated = stabilization_df["broke_confirmed_low"].apply(lambda b: is_rebound_invalidated(bool(b)))

    signal_state = [
        classify_v_rebound_signal(
            total_score=score_result["total_score"].iloc[i],
            is_stabilized=bool(stabilization_df["is_stabilized"].iloc[i]),
            breakout=bool(breakout.iloc[i]),
            rebound_volume_ratio=volume_df["rebound_volume_ratio"].iloc[i],
            rr_1=stop_target_df["rr_1"].iloc[i],
            is_invalidated=bool(is_invalidated.iloc[i]),
            config=config,
        )
        for i in range(n)
    ]

    result = pd.DataFrame(
        {
            "total_score": score_result["total_score"],
            "quality_status": score_result["quality_status"],
            "signal": signal_state,
            "stop": stop_target_df["stop"],
            "target_1": stop_target_df["target_1"],
            "target_2": stop_target_df["target_2"],
            "rr_1": stop_target_df["rr_1"],
            "rr_2": stop_target_df["rr_2"],
            "drawdown": filter_df["drawdown"],
            "excess_return_60d": filter_df["excess_return_60d"],
            "is_stabilized": stabilization_df["is_stabilized"],
            "rebound_volume_ratio": volume_df["rebound_volume_ratio"],
            "panic_volume_ratio": volume_df["panic_volume_ratio"],
            "rs_5d": rs_5d,
            "breakout": breakout,
        },
        index=close.index,
    )
    if market_regime is not None:
        result["market_regime"] = market_regime
    _apply_regime_gate(result, market_regime, regime_gate, regime_gate.v_rebound_regimes if regime_gate else [])
    return result


def v_rebound_row_to_signal(symbol: str, date, row: pd.Series, name: Optional[str] = None) -> Optional[Signal]:
    if row["signal"] is None or (isinstance(row["signal"], float) and pd.isna(row["signal"])):
        return None
    reasons = [
        f"고점 대비 하락률 = {row['drawdown']:.0%}" if pd.notna(row["drawdown"]) else "고점 대비 하락률 = 확인불가",
        f"시장 대비 초과하락 = {row['excess_return_60d']:.0%}p" if pd.notna(row["excess_return_60d"]) else "시장 대비 초과하락 = 확인불가",
        f"저점 안정화(신규 저점 없음) = {'예' if bool(row['is_stabilized']) else '아니오'}",
        f"반등 거래량 배수 = {row['rebound_volume_ratio']:.2f}배" if pd.notna(row["rebound_volume_ratio"]) else "반등 거래량 배수 = 확인불가",
        f"5일 상대강도(RS) = {row['rs_5d']:.1%}p" if pd.notna(row["rs_5d"]) else "5일 상대강도(RS) = 확인불가",
        f"첫 반등고점 돌파 = {'예' if bool(row['breakout']) else '아니오'}",
        f"손익비(R/R) = {row['rr_1']:.2f}" if pd.notna(row["rr_1"]) else "손익비(R/R) = 확인불가",
    ]
    return Signal(
        symbol=symbol,
        name=name,
        strategy=StrategyName.V_REBOUND,
        date=date,
        market_regime=row.get("market_regime", RegimeType.NORMAL),
        setup_score=row["total_score"],
        trigger_score=None,
        total_score=row["total_score"],
        signal=row["signal"],
        quality_status=row["quality_status"],
        stop=_none_if_nan(row["stop"]),
        target_1=_none_if_nan(row["target_1"]),
        target_2=_none_if_nan(row["target_2"]),
        rr_1=_none_if_nan(row["rr_1"]),
        rr_2=_none_if_nan(row["rr_2"]),
        reasons=reasons,
    )


def _none_if_nan(value) -> Optional[float]:
    return None if pd.isna(value) else float(value)
