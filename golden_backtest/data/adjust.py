"""수정·비수정 가격 처리. 이 모듈이 유일한 곳이다(지표·신호 코드는 다시 보정하지 않는다).

공급자(FDR/Yahoo) 열의 의미
- Close    : 분할만 보정된 종가(배당 미보정). 분할 전 실제 가격이 아니다.
- Adj Close: 분할 + 배당 보정 종가.

만드는 열
- open/high/low/close : 완전 수정 OHLC = 공급자 OHLC × 수정 비율. 지표·신호 계산용.
  **수정 비율은 구간 안에서 상수다.** 공급자의 봉별 Adj Close/Close는 같은 구간 안에서도 상대 약 6e-7씩 흔들린다(공급자 반올림 잡음).
  이 잡음이 있으면 같은 실제 가격(예: 같은 고가)이 서로 다른 날 수정가에서 미세하게 달라져 "고가 ≥ 55일 최고가" 같은 동률 판정이
  재수집마다 뒤바뀐다. 그래서 배당락일과 분할일로 구간을 나누고 구간 안의 비율을 구간 중앙값으로 고정한다. 같은 실제 가격은 같은
  구간 안에서 정확히 같은 수정가가 된다.
- adj_ratio           : 위에서 쓴 구간별 상수 비율(감사용).
- raw_close           : 비수정(분할 전 실제) 종가 = Close × (그 날짜 이후 분할 비율의 곱). 10달러 필터 전용.
- dollar_volume       : Close × Volume. 분할로 가격과 거래량이 반대로 움직여 분할에 불변이므로 비수정 거래대금과 같다.

구간 검증: 구간 안 원래 비율의 중앙값 대비 최대 상대 편차가 ratio_tol(1e-5)을 넘으면 배당·분할 기록 누락 가능성으로 보고
목록(report)에 남기고, 그 구간 안의 비율 점프 지점을 추가 경계로 쓴다. 추가 경계로도 편차가 남는 구간은 unresolved로 남긴다.

접두 불변성: 이후 배당·분할이 생기면 과거 전 구간에 같은 배수가 곱해질 뿐이라, 가격끼리 비교하는 규칙(돌파, 이동평균 위/아래,
ATR 대비 비율)은 영향이 없다. 달러 절대값을 쓰는 규칙은 raw_close·dollar_volume만 써야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RATIO_TOL = 1e-5      # 구간 안 원래 비율의 최대 상대 편차 허용치. 넘으면 누락 가능성으로 보고
JUMP_TOL = 1e-5       # 추가 경계로 쓰는 비율 점프(봉 간 상대 변화) 기준. 잡음(약 6e-7)보다 충분히 크다


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


def event_boundaries(index: pd.DatetimeIndex, dividends: pd.Series | None, splits: pd.Series | None) -> list[int]:
    """배당락일·분할일에서 새 구간이 시작하는 봉 위치. ex-date가 휴장일이면 다음 개장일 봉, 데이터 시작 전이면 경계 없음."""
    pos: set[int] = set()
    for ev in (dividends, splits):
        if ev is None or len(ev) == 0:
            continue
        for ex in ev.index:
            p = int(index.searchsorted(ex))
            if 0 < p < len(index):
                pos.add(p)
    return sorted(pos)


def _segment_ids(n: int, bounds: list[int]) -> np.ndarray:
    return np.searchsorted(np.asarray(sorted(bounds), dtype=int), np.arange(n), side="right")


def _max_dev_by_segment(ratio: pd.Series, seg: np.ndarray) -> pd.DataFrame:
    med = ratio.groupby(seg).transform("median")
    dev = (ratio / med - 1.0).abs()
    g = pd.DataFrame({"seg": seg, "dev": dev.values}, index=ratio.index)
    return g


def snap_ratio(ratio: pd.Series, bounds: list[int], ratio_tol: float = RATIO_TOL, jump_tol: float = JUMP_TOL) -> tuple[pd.Series, dict]:
    """ratio(봉별 Adj Close/Close)를 구간 중앙값으로 고정한다. 반환: (고정된 비율, 보고).

    보고: suspect_segments(최대 편차 > ratio_tol였던 구간: 시작·끝·봉 수·최대 편차·최대 편차 봉),
          added_jump_boundaries(추가 경계로 쓴 점프 날짜), unresolved(추가 경계 후에도 편차가 남은 구간)
    """
    n = len(ratio)
    bounds = list(bounds)
    seg = _segment_ids(n, bounds)
    g = _max_dev_by_segment(ratio, seg)
    suspects, added = [], []
    for sid, grp in g.groupby("seg"):
        if grp.dev.max() > ratio_tol:
            suspects.append({"start": grp.index[0].date().isoformat(), "end": grp.index[-1].date().isoformat(), "bars": int(len(grp)),
                             "max_rel_dev": float(grp.dev.max()), "worst_bar": grp.dev.idxmax().date().isoformat()})
            lo = ratio.index.get_loc(grp.index[0])
            hi = ratio.index.get_loc(grp.index[-1])
            r = ratio.iloc[lo: hi + 1]
            jumps = (r / r.shift(1) - 1.0).abs()
            for k in range(1, len(r)):
                if jumps.iloc[k] > jump_tol:
                    bounds.append(lo + k)
                    added.append(r.index[k].date().isoformat())
    unresolved = []
    if suspects:
        seg = _segment_ids(n, bounds)
        g = _max_dev_by_segment(ratio, seg)
        for sid, grp in g.groupby("seg"):
            if grp.dev.max() > ratio_tol:
                unresolved.append({"start": grp.index[0].date().isoformat(), "end": grp.index[-1].date().isoformat(), "bars": int(len(grp)),
                                   "max_rel_dev": float(grp.dev.max()), "worst_bar": grp.dev.idxmax().date().isoformat()})
    snapped = ratio.groupby(seg).transform("median")
    return snapped, {"suspect_segments": suspects, "added_jump_boundaries": added, "unresolved": unresolved,
                     "segments": int(len(set(seg.tolist())))}


def build_adjusted(vendor: pd.DataFrame, splits: pd.Series | None, splits_ok: bool = True, dividends: pd.Series | None = None,
                   return_report: bool = False):
    """정리된 공급자 프레임(Open/High/Low/Close/Adj Close/Volume) → 저장용 프레임.

    splits_ok=False(분할 이벤트 조회 실패)이면 raw_close를 NaN으로 둔다. 분할이 없다고 가정하지 않는다.
    dividends가 없으면(None 또는 조회 실패) 분할일만 경계로 쓰고, 비율 점프 검출이 누락된 배당 경계를 대신한다.
    return_report=True면 (프레임, 구간 보고)를 돌려준다.
    """
    raw_ratio = vendor["Adj Close"] / vendor["Close"]
    bounds = event_boundaries(vendor.index, dividends, splits if splits_ok else None)
    ratio, report = snap_ratio(raw_ratio, bounds)
    out = pd.DataFrame(index=vendor.index)
    out["open"] = vendor["Open"] * ratio
    out["high"] = vendor["High"] * ratio
    out["low"] = vendor["Low"] * ratio
    out["close"] = vendor["Close"] * ratio                 # 공급자 Adj Close가 아니라 같은 구간 상수 비율로 만든다(봉별 잡음 제거)
    out["volume"] = vendor["Volume"].astype(float)
    if splits_ok:
        out["raw_close"] = vendor["Close"] * cumulative_split_factor(vendor.index, splits)
    else:
        out["raw_close"] = np.nan
    out["dollar_volume"] = vendor["Close"] * vendor["Volume"].astype(float)
    out["adj_ratio"] = ratio                                # 구간별 상수 수정 비율(감사용)
    out["v_close"] = vendor["Close"]                        # 감사용 원본 열
    out["v_adj_close"] = vendor["Adj Close"]
    return (out, report) if return_report else out
