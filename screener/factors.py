"""팩터 점수 계산 — 시장에 무관한 순수 로직 (STRATEGY.md 3절).

입력: 표준 스키마(schema.SCHEMA_COLUMNS) DataFrame
출력: 아래 컬럼이 추가된 DataFrame
    price_to_sma200, value_score, momentum_score, quality_score, composite_score
    (+ 디버깅용 개별 순위 컬럼 rk_*)

모든 스코어는 0~100. 결측 지표는 백분위 순위 단계에서 NaN 으로 남기고,
하위 스코어 평균에서 skipna 로 자동 제외한다. 하위 스코어 전체가 결측이면 중립값 50.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import schema

# Composite 가중치
WEIGHT_VALUE = 0.40
WEIGHT_MOMENTUM = 0.30
WEIGHT_QUALITY = 0.30

NEUTRAL = 50.0


def _pct_rank(s: pd.Series, *, ascending: bool) -> pd.Series:
    """0~100 백분위 순위. ascending=True 면 값이 클수록 100점.

    NaN 은 NaN 으로 유지(na_option='keep'). 유효값이 1개뿐이면 그 값은 50점(중립).
    """
    s = pd.to_numeric(s, errors="coerce")
    valid = s.notna().sum()
    if valid == 0:
        return pd.Series(np.nan, index=s.index)
    if valid == 1:
        return s.notna().map({True: NEUTRAL, False: np.nan}).astype(float)
    return s.rank(pct=True, ascending=ascending, na_option="keep") * 100.0


def _row_mean(cols: list[pd.Series]) -> pd.Series:
    """여러 순위 시리즈의 행별 평균(skipna). 전부 NaN 인 행은 NEUTRAL."""
    mat = pd.concat(cols, axis=1)
    out = mat.mean(axis=1, skipna=True)
    return out.fillna(NEUTRAL)


def compute_factor_scores(df: pd.DataFrame) -> pd.DataFrame:
    """표준 스키마 DataFrame 에 팩터 스코어 컬럼을 추가해 반환한다."""
    df = schema.validate(df)

    # --- 파생 지표 ---
    sma = df["sma200"].replace(0, np.nan)
    df["price_to_sma200"] = df["price"] / sma - 1.0

    # P/E, P/B 는 0 이하(적자·자본잠식)면 '저평가'로 오인되므로 결측 처리
    per = df["per"].where(df["per"] > 0)
    pbr = df["pbr"].where(df["pbr"] > 0)
    ev = df["ev_ebitda"].where(df["ev_ebitda"] > 0)

    # --- 개별 백분위 순위 ---
    # 낮을수록 좋은 지표는 ascending=False (작은 값 = 100점)
    df["rk_per"] = _pct_rank(per, ascending=False)
    df["rk_pbr"] = _pct_rank(pbr, ascending=False)
    df["rk_ev_ebitda"] = _pct_rank(ev, ascending=False)
    df["rk_shareholder_yield"] = _pct_rank(df["shareholder_yield"], ascending=True)

    df["rk_ret_6m"] = _pct_rank(df["ret_6m"], ascending=True)
    df["rk_ret_12m"] = _pct_rank(df["ret_12m"], ascending=True)
    df["rk_price_to_sma200"] = _pct_rank(df["price_to_sma200"], ascending=True)

    df["rk_roe"] = _pct_rank(df["roe"], ascending=True)
    df["rk_debt_to_equity"] = _pct_rank(df["debt_to_equity"], ascending=False)
    df["rk_net_income"] = df["net_income_positive"].map({True: 100.0, False: 0.0}).astype(float)

    # --- 하위 스코어 ---
    df["value_score"] = _row_mean(
        [df["rk_per"], df["rk_pbr"], df["rk_ev_ebitda"], df["rk_shareholder_yield"]]
    )
    df["momentum_score"] = _row_mean(
        [df["rk_ret_6m"], df["rk_ret_12m"], df["rk_price_to_sma200"]]
    )
    df["quality_score"] = _row_mean(
        [df["rk_roe"], df["rk_debt_to_equity"], df["rk_net_income"]]
    )

    # --- Composite ---
    df["composite_score"] = (
        WEIGHT_VALUE * df["value_score"]
        + WEIGHT_MOMENTUM * df["momentum_score"]
        + WEIGHT_QUALITY * df["quality_score"]
    )

    for c in ("value_score", "momentum_score", "quality_score", "composite_score"):
        df[c] = df[c].round(2)

    return df
