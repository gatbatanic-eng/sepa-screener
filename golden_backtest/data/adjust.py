"""수정·비수정 가격 처리. 이 모듈이 유일한 곳이다(지표·신호 코드는 다시 보정하지 않는다).

공급자(FDR/Yahoo) 열의 의미
- Close    : 분할만 보정된 종가(배당 미보정). 분할 전 실제 가격이 아니다.
- Adj Close: 분할 + 배당 보정 종가.

만드는 열
- open/high/low/close : 완전 수정 OHLC = 공급자 OHLC × (Adj Close / Close). 지표·신호 계산용.
- raw_close           : 비수정(분할 전 실제) 종가 = Close × (그 날짜 이후 분할 비율의 곱). 10달러 필터 전용.
- dollar_volume       : Close × Volume. 분할로 가격과 거래량이 반대로 움직여 분할에 불변이므로 비수정 거래대금과 같다.

접두 불변성: 이후 배당·분할이 생기면 과거 전 구간에 같은 배수가 곱해질 뿐이라, 가격끼리 비교하는 규칙(돌파, 이동평균 위/아래,
ATR 대비 비율)은 영향이 없다. 달러 절대값을 쓰는 규칙은 raw_close·dollar_volume만 써야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def cumulative_split_factor(index: pd.DatetimeIndex, splits: pd.Series | None) -> pd.Series:
    """각 날짜 t에 대해 'ex-date가 t보다 뒤인 분할 비율의 곱'. 분할일 당일 봉은 이미 분할 후 가격이므로 곱하지 않는다.

    splits: index=ex-date(tz 없음), value=비율(4:1 → 4.0, 1:10 병합 → 0.1).
    """
    if splits is None or len(splits) == 0:
        return pd.Series(1.0, index=index)
    s = splits.sort_index()
    ratios = s.values.astype(float)
    suffix = np.append(np.cumprod(ratios[::-1])[::-1], 1.0)  # suffix[i] = ratios[i:]의 곱
    pos = np.searchsorted(s.index.values, index.values, side="right")  # t보다 뒤(>)인 첫 분할
    return pd.Series(suffix[pos], index=index)


def build_adjusted(vendor: pd.DataFrame, splits: pd.Series | None, splits_ok: bool = True) -> pd.DataFrame:
    """정리된 공급자 프레임(Open/High/Low/Close/Adj Close/Volume) → 저장용 프레임.

    splits_ok=False(분할 이벤트 조회 실패)이면 raw_close를 NaN으로 둔다. 분할이 없다고 가정하지 않는다.
    """
    ratio = vendor["Adj Close"] / vendor["Close"]
    out = pd.DataFrame(index=vendor.index)
    out["open"] = vendor["Open"] * ratio
    out["high"] = vendor["High"] * ratio
    out["low"] = vendor["Low"] * ratio
    out["close"] = vendor["Adj Close"]
    out["volume"] = vendor["Volume"].astype(float)
    if splits_ok:
        out["raw_close"] = vendor["Close"] * cumulative_split_factor(vendor.index, splits)
    else:
        out["raw_close"] = np.nan
    out["dollar_volume"] = vendor["Close"] * vendor["Volume"].astype(float)
    out["v_close"] = vendor["Close"]          # 감사용 원본 열
    out["v_adj_close"] = vendor["Adj Close"]
    return out
