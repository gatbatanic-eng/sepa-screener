"""표준 스키마 정의 — 데이터 소스(data_kr/data_us)와 팩터 로직(factors) 사이의 계약.

factors.py 는 이 스키마만 알고 시장(한국/미국)에는 무관하다.
"""
from __future__ import annotations

import pandas as pd

# 데이터 소스가 반드시 채워야 하는 컬럼 (STRATEGY.md 5절)
SCHEMA_COLUMNS: list[str] = [
    "symbol",
    "name",
    "market",
    "price",
    "sma200",
    "ret_6m",
    "ret_12m",
    "per",
    "pbr",
    "ev_ebitda",
    "shareholder_yield",
    "roe",
    "debt_to_equity",
    "net_income_positive",
]

# 결측이 허용되는(소스별 한계) 컬럼 — 나머지는 값이 있어야 정상
NULLABLE_COLUMNS: set[str] = {
    "ev_ebitda",
    "debt_to_equity",
    "per",
    "pbr",
    "shareholder_yield",
    "roe",
}

NUMERIC_COLUMNS: list[str] = [
    "price",
    "sma200",
    "ret_6m",
    "ret_12m",
    "per",
    "pbr",
    "ev_ebitda",
    "shareholder_yield",
    "roe",
    "debt_to_equity",
]


def empty_frame() -> pd.DataFrame:
    """스키마 컬럼만 가진 빈 DataFrame."""
    return pd.DataFrame({c: pd.Series(dtype="object") for c in SCHEMA_COLUMNS})


def validate(df: pd.DataFrame, *, strict: bool = False) -> pd.DataFrame:
    """스키마 컬럼 존재 여부를 확인하고 타입을 정돈한다.

    누락된 컬럼은 NaN 으로 채워 넣는다(소스 한계 대비). strict=True 면 누락 시 예외.
    반환 DataFrame 은 SCHEMA_COLUMNS 순서로 정렬된 복사본이다.
    """
    df = df.copy()
    missing = [c for c in SCHEMA_COLUMNS if c not in df.columns]
    if missing:
        if strict:
            raise ValueError(f"표준 스키마 컬럼 누락: {missing}")
        for c in missing:
            df[c] = pd.NA

    for c in NUMERIC_COLUMNS:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # net_income_positive: 결측은 False(보수적)로
    df["net_income_positive"] = (
        df["net_income_positive"].map(_to_bool).fillna(False).astype(bool)
    )
    df["symbol"] = df["symbol"].astype(str)
    df["name"] = df["name"].astype(str)
    df["market"] = df["market"].astype(str)

    return df[SCHEMA_COLUMNS].reset_index(drop=True)


def _to_bool(v) -> object:
    if v is None:
        return pd.NA
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "1", "y", "yes", "t"}:
            return True
        if s in {"false", "0", "n", "no", "f", ""}:
            return False
        return pd.NA
    try:
        if pd.isna(v):
            return pd.NA
    except (TypeError, ValueError):
        pass
    return bool(v)
