"""전략 공통 접두 불변성 검사(CLAUDE.md 2절: 모든 전략에 두 가지 필수). 5단계 전략도 이 헬퍼를 쓴다.

1. 신호 열 직접 검사: prepare()가 만든 열을 잘라 비교 (한 봉짜리 미래 누수도 잡는다)
2. 엔진 거래 검사: 잘라 simulate한 확정 거래가 전체 실행의 같은 구간과 같은지
거래 검사만으로는 한 봉짜리 누수를 못 잡는다(누수가 영향을 주는 마지막 봉 신호는 잘린 데이터 밖에서 체결된다).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from golden_backtest.engine.simulator import simulate
from golden_backtest.tests.engine_helpers import COSTS, SPEC
from golden_backtest.tests.lookahead import find_prefix_violations


def signal_violations(make_strategy, df: pd.DataFrame, columns: list[str], cuts: list[int]) -> dict[str, list[int]]:
    """열별 접두 불변성 위반 절단 위치. 모두 빈 목록이어야 통과."""
    return {c: find_prefix_violations(lambda d, c=c: make_strategy().prepare(d)[c].astype(float), df, cuts=cuts) for c in columns}


def _key(t):
    return (t.entry_date, t.exit_date, round(t.entry_price, 8), round(t.exit_price, 8), t.exit_reason, round(t.r_multiple, 10))


def trade_violations(make_strategy, df: pd.DataFrame, cuts: list[int]) -> tuple[int, list[int]]:
    """(전체 실행의 확정 거래 수, 거래가 달라진 절단 위치)."""
    full = simulate(df, make_strategy(), "X", COSTS, SPEC)
    bad = []
    for cut in cuts:
        part = simulate(df.iloc[: cut + 1], make_strategy(), "X", COSTS, SPEC)
        d = df.index[cut]
        a = [_key(t) for t in full.confirmed() if t.exit_date <= d]
        b = [_key(t) for t in part.confirmed() if t.exit_date <= d]
        if a != b:
            bad.append(cut)
    return len(full.confirmed()), bad
