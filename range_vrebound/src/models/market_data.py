"""가격 데이터와 (V1에는 아직 없는) 펀더멘털 데이터 모델.

스펙 5조: 가격 데이터와 펀더멘털 데이터는 분리한다.
"""
from __future__ import annotations

from datetime import date as date_
from typing import Optional

from pydantic import BaseModel, model_validator


class OHLCVBar(BaseModel):
    date: date_
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    @model_validator(mode="after")
    def _high_at_least_low(self) -> "OHLCVBar":
        if self.high < self.low:
            raise ValueError("high는 low보다 작을 수 없습니다.")
        return self


class MarketIndexBar(BaseModel):
    date: date_
    index_code: str  # "KS11"(코스피) 또는 "KQ11"(코스닥)
    close: float


class FundamentalRecord(BaseModel):
    """V1은 펀더멘털 데이터 소스가 없다. 필드는 전량 Optional이며, 값이
    없으면 Quality Score 계산기가 이를 UNKNOWN으로 매핑한다 (스펙 16조).
    데이터가 없다는 것을 양호한 상태로 간주하지 않는다.
    """

    symbol: str
    period: str  # 예: "2025Q4"
    revenue: Optional[float] = None
    operating_income: Optional[float] = None
    cash_flow: Optional[float] = None
    debt: Optional[float] = None
    shares_outstanding: Optional[float] = None
