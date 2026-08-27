"""시장 레짐 스키마 및 판정 로직 (스펙 6조).

CRASH: 시장 20일 수익률 <= -10% 또는 60일 고점대비 하락률 <= -15% (OR 조건)
RANGE: 추세 부재 + 좁은 밴드 — |60일 수익률| <= abs_return_max 이고
       (60일 고가-저가)/저가 <= band_width_max (Phase 0 계획에서 사용자가
       확정한 정의)
RECOVERY: 가장 최근 CRASH로 분류된 날로부터 recovery.lookback_days 거래일
       이내이면서, 오늘 자체는 CRASH 조건에 해당하지 않는 경우. "저점 형성"을
       별도 알고리즘으로 탐지하지 않고, CRASH 이벤트 직후의 관찰 기간을
       RECOVERY로 보는 단순하고 인과적인(과거 데이터만 사용) 근사다. 스펙
       6조가 RECOVERY의 정확한 수식을 정의하지 않아 채택한 구현 결정이며,
       V-REBOUND의 STABILIZATION/BREAKOUT 로직(Phase 5)이 실제 저점·회복
       탐지를 담당한다.
NORMAL: 위 조건에 모두 해당하지 않는 기본값 (데이터가 부족해 CRASH/RANGE
       조건을 판정할 수 없는 경우도 조건 미충족으로 보아 NORMAL로 분류된다).

레짐은 CRASH > RECOVERY > RANGE > NORMAL 순으로 우선순위를 매겨 상호
배타적으로 분류한다.
"""
from __future__ import annotations

from datetime import date as date_
from enum import Enum
from typing import TYPE_CHECKING, Optional

import pandas as pd
from pydantic import BaseModel

from src.indicators.relative_strength import period_return
from src.indicators.volatility import drawdown_from_high, range_width, rolling_high, rolling_low

if TYPE_CHECKING:
    from src.config import MarketRegimeConfig


class RegimeType(str, Enum):
    NORMAL = "NORMAL"
    RANGE = "RANGE"
    CRASH = "CRASH"
    RECOVERY = "RECOVERY"


class MarketRegime(BaseModel):
    date: date_
    regime: RegimeType

    # 판정에 쓰인 raw 값 (설명가능성용, 스펙 26조)
    return_20d: Optional[float] = None
    drawdown_60d: Optional[float] = None
    return_60d: Optional[float] = None
    band_width_60d: Optional[float] = None
    days_since_last_crash: Optional[int] = None


def compute_regime_series(index_close: pd.Series, config: "MarketRegimeConfig") -> pd.DataFrame:
    """시장 지수 종가 시계열로부터 날짜별 레짐을 인과적으로(과거 데이터만
    사용) 분류한다. 반환되는 DataFrame은 index_close와 같은 인덱스를 쓰며,
    regime과 설명가능성용 raw 값 컬럼들을 담는다.
    """
    return_20d = period_return(index_close, config.crash.return_lookback_days)
    crash_period_high = rolling_high(index_close, config.crash.drawdown_lookback_days)
    drawdown_60d = drawdown_from_high(index_close, crash_period_high)

    return_60d = period_return(index_close, config.range.lookback_days)
    range_high = rolling_high(index_close, config.range.lookback_days)
    range_low = rolling_low(index_close, config.range.lookback_days)
    band_width_60d = range_width(range_high, range_low)

    # 부동소수점 오차로 정확히 경계값인 케이스가 "<=" 판정을 놓치지 않도록
    # 아주 작은 허용오차(1e-9)를 둔다 (예: 90/100-1 == -0.09999999999999998).
    eps = 1e-9
    is_crash = (return_20d <= config.crash.return_20d_max + eps) | (
        drawdown_60d <= config.crash.drawdown_60d_max + eps
    )
    is_crash = is_crash.fillna(False)

    is_range = (return_60d.abs() <= config.range.abs_return_max + eps) & (
        band_width_60d <= config.range.band_width_max + eps
    )
    is_range = is_range.fillna(False)

    n = len(index_close)
    regimes: list[RegimeType] = [RegimeType.NORMAL] * n
    days_since_last_crash: list[Optional[int]] = [None] * n
    last_crash_idx: Optional[int] = None

    for i in range(n):
        if bool(is_crash.iloc[i]):
            regimes[i] = RegimeType.CRASH
            last_crash_idx = i
            days_since_last_crash[i] = 0
        elif last_crash_idx is not None and (i - last_crash_idx) <= config.recovery.lookback_days:
            regimes[i] = RegimeType.RECOVERY
            days_since_last_crash[i] = i - last_crash_idx
        elif bool(is_range.iloc[i]):
            regimes[i] = RegimeType.RANGE
        else:
            regimes[i] = RegimeType.NORMAL

    return pd.DataFrame(
        {
            "regime": regimes,
            "return_20d": return_20d,
            "drawdown_60d": drawdown_60d,
            "return_60d": return_60d,
            "band_width_60d": band_width_60d,
            "days_since_last_crash": days_since_last_crash,
        },
        index=index_close.index,
    )


def regime_row_to_model(date: date_, row: pd.Series) -> MarketRegime:
    """compute_regime_series 결과의 한 행을 MarketRegime 모델로 변환한다."""
    return MarketRegime(
        date=date,
        regime=row["regime"],
        return_20d=_none_if_nan(row["return_20d"]),
        drawdown_60d=_none_if_nan(row["drawdown_60d"]),
        return_60d=_none_if_nan(row["return_60d"]),
        band_width_60d=_none_if_nan(row["band_width_60d"]),
        days_since_last_crash=row["days_since_last_crash"],
    )


def _none_if_nan(value: float) -> Optional[float]:
    return None if pd.isna(value) else float(value)
