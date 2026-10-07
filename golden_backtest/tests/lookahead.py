"""접두 불변성(prefix invariance) 검사 틀. 전략 단계에서도 같은 함수를 쓴다.

정의: 데이터를 t일에서 잘라 계산한 결과 == 전체 데이터로 계산한 결과의 t일까지. 다르면 미래 정보가 섞였다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def synthetic_ohlcv(n: int = 600, seed: int = 7) -> pd.DataFrame:
    """재현 가능한 합성 일봉(랜덤워크). 네트워크 불필요."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol = rng.integers(500_000, 5_000_000, n).astype(float)
    idx = pd.bdate_range("2018-01-02", periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def default_cuts(n: int, warmup: int = 5, count: int = 12) -> list[int]:
    return sorted({int(x) for x in np.linspace(warmup, n - 1, count)})


def find_prefix_violations(fn, df: pd.DataFrame, cuts: list[int] | None = None, atol: float = 1e-9) -> list[int]:
    """fn(df) → Series/DataFrame(같은 인덱스). 위반한 절단 위치(0부터, 포함) 목록을 돌려준다. 빈 목록이면 통과."""
    full = fn(df)
    bad = []
    for t in cuts or default_cuts(len(df)):
        part = fn(df.iloc[: t + 1])
        want = full.iloc[: t + 1]
        a = np.asarray(part, dtype=float)
        b = np.asarray(want, dtype=float)
        same = np.isclose(a, b, atol=atol, rtol=1e-9, equal_nan=True).all()
        if not same:
            bad.append(t)
    return bad
