"""롱베이스 돌파 탐지 — 순수 로직(네트워크 없음).

정의
----
돌파일 b 직전부터 거꾸로, "종가[b] > 구간 최고가" 이고 깊이 <= max_depth 인 가장 긴 구간을 잡고,
그 안에서 구간 최고가가 마지막으로 나온 날(좌측 고점)부터를 베이스로 본다 (그 앞의 상승 구간은 제외).
  - pivot  = 베이스 최고 고가 (돌파 전까지의 저항선)
  - L      = 좌측 고점 ~ b-1 거래일 수
  - depth  = (pivot - 베이스 최저 저가) / pivot
  - 후반 수축 = 마지막 1/3 평균 (H-L)/C  ÷  앞 2/3 평균
look-ahead 없음: 돌파일 b 의 판정에는 b 이전 데이터 + b 당일 OHLCV 만 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd

from .config import CONFIG, LongBaseConfig


@dataclass
class Breakout:
    breakout_date: str
    days_ago: int
    base_days: int
    base_start: str
    pivot: float
    base_low: float
    depth_pct: float
    late_contraction: float
    vol_ratio: float
    clv: float
    breakout_close: float
    close: float
    extension_pct: float          # 현재 종가 / pivot - 1
    ath_in_data: bool             # 조회 데이터 전체에서 신고가(좌측 고점 없음)

    def to_dict(self) -> dict:
        return asdict(self)


def _validate(df: pd.DataFrame) -> pd.DataFrame:
    need = {"High", "Low", "Close", "Volume"}
    if not need <= set(df.columns):
        raise ValueError(f"OHLCV 컬럼 부족: {sorted(need - set(df.columns))}")
    return df


def momentum_score(close: pd.Series) -> Optional[float]:
    """RS 원점수: 6개월(126) 수익률 0.6 + 12개월(252) 수익률 0.4. 데이터 부족 시 None."""
    if len(close) < 253:
        return None
    r6 = close.iloc[-1] / close.iloc[-127] - 1.0
    r12 = close.iloc[-1] / close.iloc[-253] - 1.0
    return float(0.6 * r6 + 0.4 * r12)


def avg_trading_value(df: pd.DataFrame, days: int = 20) -> Optional[float]:
    if len(df) < days:
        return None
    tv = (df["Close"] * df["Volume"]).iloc[-days:]
    return float(tv.mean())


def _check_breakout(df: pd.DataFrame, b: int, cfg: LongBaseConfig) -> Optional[Breakout]:
    """위치 b(0-based)가 롱베이스 돌파일인지 판정. 아니면 None."""
    high = df["High"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    vol = df["Volume"].to_numpy(float)
    n = len(df)
    if b < cfg.min_base_days + 1 or b >= n:
        return None

    # --- 거래량/종가위치 (싼 조건 먼저) ---
    lo_v = max(0, b - cfg.vol_avg_days)
    avg_vol = vol[lo_v:b].mean() if b > lo_v else np.nan
    if not np.isfinite(avg_vol) or avg_vol <= 0:
        return None
    vol_ratio = vol[b] / avg_vol
    if vol_ratio < cfg.min_vol_ratio:
        return None
    rng = high[b] - low[b]
    clv = (close[b] - low[b]) / rng if rng > 0 else 0.0
    if clv < cfg.min_clv:
        return None

    # --- 베이스: b 직전부터 거꾸로, 종가[b] > 구간 최고가 이고 깊이 <= max_depth 인 가장 긴 구간 ---
    start_floor = max(0, b - cfg.max_base_days)
    hh, ll = high[start_floor:b], low[start_floor:b]
    rmax = np.maximum.accumulate(hh[::-1])
    rmin = np.minimum.accumulate(ll[::-1])
    ok = (close[b] > rmax) & ((rmax - rmin) / rmax <= cfg.max_depth)
    if not ok[0]:
        return None
    n_ok = len(ok) if ok.all() else int(np.argmin(ok))
    win_lo = b - n_ok                                   # 구간 [win_lo, b-1]
    seg = high[win_lo:b]
    # 베이스는 좌측 고점(구간 최고가가 마지막으로 나온 날)에서 시작한다 — 그 앞 상승 구간은 제외
    base_lo_idx = win_lo + int(np.nonzero(seg == seg.max())[0][-1])
    L = b - base_lo_idx
    if L < cfg.min_base_days:
        return None
    pivot = float(high[base_lo_idx:b].max())
    base_low_arr = low[base_lo_idx:b]
    base_high = high[base_lo_idx:b]
    base_low = float(base_low_arr.min())
    depth = (pivot - base_low) / pivot
    if depth > cfg.max_depth:
        return None
    j = None if win_lo == start_floor else 1            # 조회 구간 전체의 신고가 돌파면 None

    # --- 후반 수축 ---
    rel = (base_high - base_low_arr) / close[base_lo_idx:b]
    cut = int(len(rel) * 2 / 3)
    early, late = rel[:cut].mean(), rel[cut:].mean()
    contraction = float(late / early) if early > 0 else np.inf
    if contraction > cfg.late_contraction_max:
        return None

    return Breakout(
        breakout_date=str(pd.Timestamp(df.index[b]).date()),
        days_ago=n - 1 - b,
        base_days=int(L),
        base_start=str(pd.Timestamp(df.index[base_lo_idx]).date()),
        pivot=pivot, base_low=base_low, depth_pct=float(depth * 100),
        late_contraction=contraction, vol_ratio=float(vol_ratio), clv=float(clv),
        breakout_close=float(close[b]), close=float(close[-1]),
        extension_pct=float((close[-1] / pivot - 1) * 100),
        ath_in_data=j is None,
    )


def find_longbase_breakout(df: pd.DataFrame, cfg: LongBaseConfig = CONFIG) -> Optional[Breakout]:
    """최근 cfg.recent_days 거래일 안에 나온 롱베이스 돌파 중 가장 이른 것을 반환.

    추가 조건: 현재 종가 > 200일선, 현재 종가 >= pivot (돌파 실패 제외),
    현재 종가 <= pivot*(1+max_extension) (과열 제외).
    """
    df = _validate(df).dropna(subset=["High", "Low", "Close", "Volume"])
    n = len(df)
    if n < cfg.min_bars:
        return None
    close = df["Close"]
    sma = close.rolling(cfg.sma_days).mean().iloc[-1]
    if not np.isfinite(sma) or close.iloc[-1] <= sma:
        return None
    for b in range(max(0, n - cfg.recent_days), n):
        bo = _check_breakout(df, b, cfg)
        if bo is None:
            continue
        if bo.close < bo.pivot:                         # 돌파 후 피벗 아래로 복귀
            continue
        if bo.extension_pct > cfg.max_extension * 100:  # 과열
            continue
        return bo
    return None
