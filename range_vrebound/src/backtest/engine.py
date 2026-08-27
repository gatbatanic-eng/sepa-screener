"""거래 시뮬레이션 (스펙 27조).

entry_date는 신호가 발생한 날(T)이 아니라 실제 체결일(T+1, Phase 0 제안
3)이어야 하며, 여기 전달하는 high/low/close 시계열은 그 체결일부터
시작해야 한다 (index 0 = 체결일).

구현상 정해야 했던 규칙(스펙에 명시 없음, Phase 7 제안):
- 체결 당일(index 0) 자체는 스탑/타깃 히트 판정에서 제외한다 — 시가에
  막 체결된 날 같은 날 스탑/타깃을 잡는 것은 체결가 자체의 정의와
  충돌한다. 단, MFE/MAE 계산에는 체결일도 포함한다.
- 같은 날 스탑과 타깃이 동시에 히트되면 보수적으로 STOP을 우선한다.
- INVALIDATED는 스탑/타깃보다 먼저 검사한다 (전략 재평가가 가격 기반
  청산보다 우선한다고 본다).
- 만기(max_holding_days)까지 아무 것도 히트하지 않으면 그날 종가로
  TIME_EXIT 청산한다.
"""
from __future__ import annotations

from datetime import date as date_
from typing import Optional

import pandas as pd

from src.models.signal import SignalState, StrategyName
from src.models.trade import ExitReason, Trade


def simulate_trade(
    symbol: str,
    strategy: StrategyName,
    entry_date: date_,
    entry_price: float,
    stop: float,
    target: float,
    max_holding_days: int,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    invalidated: Optional[pd.Series] = None,
) -> Trade:
    n = len(close)
    last_position = min(max_holding_days, n - 1)

    exit_reason: Optional[ExitReason] = None
    exit_price: Optional[float] = None
    exit_position = last_position

    for pos in range(1, last_position + 1):
        if invalidated is not None and bool(invalidated.iloc[pos]):
            exit_reason = ExitReason.INVALIDATED
            exit_price = float(close.iloc[pos])
            exit_position = pos
            break
        today_low = low.iloc[pos]
        if pd.notna(today_low) and today_low <= stop:
            exit_reason = ExitReason.STOP
            exit_price = stop
            exit_position = pos
            break
        today_high = high.iloc[pos]
        if pd.notna(today_high) and today_high >= target:
            exit_reason = ExitReason.TARGET
            exit_price = target
            exit_position = pos
            break

    if exit_reason is None:
        exit_reason = ExitReason.TIME_EXIT
        exit_price = float(close.iloc[exit_position])

    exit_date = pd.Timestamp(close.index[exit_position]).date()

    mfe = (high.iloc[: exit_position + 1].max() - entry_price) / entry_price
    mae = (low.iloc[: exit_position + 1].min() - entry_price) / entry_price
    return_pct = (exit_price - entry_price) / entry_price

    return Trade(
        symbol=symbol,
        strategy=strategy,
        entry_date=entry_date,
        entry_price=entry_price,
        stop=stop,
        target=target,
        max_holding_days=max_holding_days,
        exit_date=exit_date,
        exit_reason=exit_reason,
        exit_price=exit_price,
        return_pct=return_pct,
        mfe=float(mfe),
        mae=float(mae),
        holding_days=exit_position,
    )


def generate_trades(
    symbol: str,
    strategy: StrategyName,
    signals_df: pd.DataFrame,
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    holding_periods_days: list[int],
) -> list[Trade]:
    """`src.pipeline`의 evaluate_range_mr/evaluate_v_rebound 결과에서
    BUY_CANDIDATE로 분류된 날마다, 스펙 27조가 요구하는 평가 기간
    (5/10/20/40일 등 holding_periods_days) 각각에 대해 별도로 거래를
    시뮬레이션한다 — 같은 신호를 여러 보유기간 가정으로 비교할 수 있게 한다.

    체결은 신호일(T) 종가가 아니라 T+1일 시가로 한다 (Phase 0 제안 3).
    target은 RR 게이트가 쓰는 target_1을 쓴다 (Phase 0 제안 6,
    `risk.rr_gate_uses`).
    """
    invalidated_mask = signals_df["signal"] == SignalState.INVALIDATED
    n = len(signals_df)
    dates = signals_df.index

    trades: list[Trade] = []
    for pos in range(n):
        if signals_df["signal"].iloc[pos] != SignalState.BUY_CANDIDATE:
            continue

        entry_pos = pos + 1
        if entry_pos >= n:
            continue  # 체결할 다음 거래일 데이터가 없음 (시뮬레이션 구간 끝)

        stop = signals_df["stop"].iloc[pos]
        target = signals_df["target_1"].iloc[pos]
        if pd.isna(stop) or pd.isna(target):
            continue

        entry_date = pd.Timestamp(dates[entry_pos]).date()
        entry_price = float(open_.iloc[entry_pos])

        window_high = high.iloc[entry_pos:]
        window_low = low.iloc[entry_pos:]
        window_close = close.iloc[entry_pos:]
        window_invalidated = invalidated_mask.iloc[entry_pos:]

        for max_days in holding_periods_days:
            trades.append(
                simulate_trade(
                    symbol=symbol,
                    strategy=strategy,
                    entry_date=entry_date,
                    entry_price=entry_price,
                    stop=float(stop),
                    target=float(target),
                    max_holding_days=max_days,
                    high=window_high,
                    low=window_low,
                    close=window_close,
                    invalidated=window_invalidated,
                )
            )
    return trades
