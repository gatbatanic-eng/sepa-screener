"""미국 장 마감 컷오프. 공휴일 달력은 추정하지 않는다(휴장일이면 소스 데이터가 마지막 실제 거래일까지만 준다).

screening.py의 _latest_closed_us_session과 같은 규칙을 이 패키지 안에 독립 구현했다(저장소 관례: 서브시스템끼리 import하지 않음).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
CLOSE_FINAL = (16, 10)  # 이 시각 이전에는 당일 봉을 미확정으로 본다


def latest_closed_us_session(now: datetime | None = None) -> pd.Timestamp:
    now = (now or datetime.now(NY)).astimezone(NY)
    day = pd.Timestamp(now.date())
    if (now.hour, now.minute) < CLOSE_FINAL:
        day -= pd.Timedelta(days=1)
    while day.weekday() >= 5:
        day -= pd.Timedelta(days=1)
    return day
