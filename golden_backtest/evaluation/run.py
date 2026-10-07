"""백테스트 실행의 단일 진입점. 종목별 **전체 이력**을 검증된 스냅샷에서 읽어 엔진에 넘긴다.

규칙
- 캐시만 읽는다(네트워크 없음). 재수집은 `python -m golden_backtest.data.build`로만 한다.
- 데이터는 자르지 않는다. 지표 초기값(예: ATR 시드)과 사상 최고가는 읽기 시작점에 따라 달라지면 안 되고, 워밍업 게이트(252봉)는
  종목별 전체 캐시의 첫 봉부터 센다. 신호를 쓰는 기간(예: 2015-01-01 이후)은 전략의 `trade_start`로만 제한한다.
- manifest의 스냅샷과 내용이 다르면 읽지 않고 실패한다(store.SnapshotMismatch).
"""
from __future__ import annotations

import pandas as pd

from golden_backtest.data import store
from golden_backtest.engine.costs import Costs
from golden_backtest.engine.simulator import SimResult, simulate


def load_history(symbol: str) -> pd.DataFrame:
    """검증된 스냅샷의 전체 이력. 첫 봉이 manifest의 첫 봉과 같음을 load_ohlcv_verified가 확인한다."""
    return store.load_ohlcv_verified(symbol)


def run_strategy(symbol: str, strategy, costs: Costs, spec_version: str, warmup_bars: int | None = None) -> tuple[pd.DataFrame, SimResult]:
    df = load_history(symbol)
    return df, simulate(df, strategy, symbol, costs, spec_version, warmup_bars=warmup_bars)
