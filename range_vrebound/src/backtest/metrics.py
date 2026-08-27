"""백테스트 성과 지표 (스펙 28조).

호출자가 원하는 부분집합(전략별/레짐별)으로 trades를 먼저 필터링한 뒤
이 함수를 호출한다 — 그룹핑 자체는 이 모듈의 책임이 아니다.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from pydantic import BaseModel

from src.models.trade import Trade


class BacktestMetrics(BaseModel):
    total_trades: int
    win_rate: Optional[float] = None
    avg_return: Optional[float] = None
    median_return: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    profit_factor: Optional[float] = None
    expectancy: Optional[float] = None
    max_drawdown: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    avg_holding_period: Optional[float] = None


def compute_metrics(trades: list[Trade]) -> BacktestMetrics:
    if not trades:
        return BacktestMetrics(total_trades=0)

    returns = np.array([t.return_pct for t in trades], dtype=float)
    holding_days = np.array([t.holding_days for t in trades], dtype=float)

    wins = returns[returns > 0]
    losses = returns[returns < 0]

    win_rate = len(wins) / len(returns)
    avg_return = float(np.mean(returns))
    median_return = float(np.median(returns))
    avg_win = float(np.mean(wins)) if len(wins) > 0 else None
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else None

    profit_factor = float(np.sum(wins) / abs(np.sum(losses))) if len(losses) > 0 and np.sum(losses) != 0 else None
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss if avg_win is not None and avg_loss is not None else None

    equity = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(equity)
    drawdown = equity / running_max - 1.0
    max_drawdown = float(np.min(drawdown))

    std = np.std(returns, ddof=1) if len(returns) > 1 else 0.0
    sharpe_ratio = float(avg_return / std) if std > 0 else None

    downside = np.clip(returns, a_min=None, a_max=0.0)
    downside_deviation = np.sqrt(np.mean(downside**2))
    sortino_ratio = float(avg_return / downside_deviation) if downside_deviation > 0 else None

    return BacktestMetrics(
        total_trades=len(trades),
        win_rate=win_rate,
        avg_return=avg_return,
        median_return=median_return,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        avg_holding_period=float(np.mean(holding_days)),
    )
