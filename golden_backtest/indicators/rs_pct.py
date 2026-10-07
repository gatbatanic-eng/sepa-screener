"""상대강도 백분위 (B2 트렌드 템플릿 8번: RS ≥ 70)."""
from __future__ import annotations

import pandas as pd


def relative_return(close: pd.DataFrame, bench_close: pd.Series, n: int = 252) -> pd.DataFrame:
    """[근사] n거래일 상대수익률 = 종목 n일 수익률 − 지수 n일 수익률. close: 열=종목, 행=날짜(수정종가).
    지수는 종목 날짜에 맞춰 정렬하되 결측을 채우지 않는다."""
    bench = bench_close.reindex(close.index)
    stock_ret = close / close.shift(n) - 1.0
    bench_ret = bench / bench.shift(n) - 1.0
    return stock_ret.sub(bench_ret, axis=0)


def rs_pct(rel_ret: pd.DataFrame) -> pd.DataFrame:
    """[근사] 같은 날짜 안에서 종목끼리 매긴 백분위(0~100]. 날짜 방향으로는 섞지 않으므로 t일 값은 t일 정보만 쓴다.
    NaN 종목은 그 날 모수에서 빠진다(상장 전·이력 부족 종목). 모수는 호출자가 넘긴 열 집합이다."""
    return rel_ret.rank(axis=1, pct=True) * 100.0
