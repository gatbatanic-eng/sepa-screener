"""
sepa/swings.py — 스윙 고점/저점 및 수축(contraction) 탐지
=========================================================

VCP(변동성 수축 패턴)를 "완전한 Minervini 재현" 이라고 주장하지 않는다. 현재
데이터(일봉 OHLCV)로 구현 가능한 **deterministic heuristic** 이다.

look-ahead 주의
---------------
fractal 스윙은 확정에 우측 ``right`` 봉이 필요하다. 따라서 :func:`detect_swings`
는 positional 인덱스 ``left`` ~ ``n-1-right`` 범위의 **확정된 스윙만** 반환한다.
마지막 ``right`` 봉과 "오늘"은 아직 스윙이 될 수 없다 — 이것이 인과적으로 옳다
(미래에 더 높은 고가가 나오면 지금 고점이 스윙이 아니게 되므로).

과거 시점(예: 5거래일 전)의 스윙 상태를 재현하려면 그 시점까지 잘린 시계열을
넘겨라.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sepa.config import SwingConfig


@dataclass(frozen=True)
class Swing:
    idx: int          # positional 인덱스 (0-based)
    price: float
    kind: str         # "H" (고점) | "L" (저점)


def detect_swings(high: pd.Series, low: pd.Series,
                  left: int, right: int) -> list[Swing]:
    """fractal 스윙 고점/저점 목록 (positional 인덱스 오름차순).

    - 스윙 고점: high[i] 가 high[i-left..i-1] 및 high[i+1..i+right] 를 모두 초과.
    - 스윙 저점: low[i] 가 low[i-left..i-1] 및 low[i+1..i+right] 를 모두 하회.
    - i 범위는 [left, n-1-right] — 우측 확인 봉이 부족한 끝부분은 제외(인과성).
    - 같은 봉이 고점이면서 저점일 수는 없다 (고점 우선).
    """
    h = high.to_numpy(dtype=float)
    lo = low.to_numpy(dtype=float)
    n = len(h)
    out: list[Swing] = []
    if n < left + right + 1:
        return out

    for i in range(left, n - right):
        wh = h[i - left: i + right + 1]
        wl = lo[i - left: i + right + 1]
        if np.isnan(wh).any() or np.isnan(wl).any():
            continue
        center = left  # 윈도우 내 i 의 위치
        if h[i] == np.max(wh) and np.argmax(wh) == center:
            out.append(Swing(idx=i, price=float(h[i]), kind="H"))
        elif lo[i] == np.min(wl) and np.argmin(wl) == center:
            out.append(Swing(idx=i, price=float(lo[i]), kind="L"))
    return out


def _alternating(swings: list[Swing]) -> list[Swing]:
    """연속으로 같은 종류(HH 또는 LL)가 나오면 더 극단인 것만 남겨 H/L 교대 시퀀스로."""
    if not swings:
        return []
    seq: list[Swing] = [swings[0]]
    for s in swings[1:]:
        if s.kind == seq[-1].kind:
            if (s.kind == "H" and s.price >= seq[-1].price) or \
               (s.kind == "L" and s.price <= seq[-1].price):
                seq[-1] = s
        else:
            seq.append(s)
    return seq


@dataclass(frozen=True)
class ContractionResult:
    count: int                       # 순차 감소로 인정된 pullback 다리 개수
    widths: list[float]              # 최근→과거 순서의 pullback 폭(%) 목록 (분석 대상 전체)
    base_start_idx: int | None       # base(수축 구간) 시작으로 볼 positional 인덱스
    tightening: bool                 # 최근 다리들이 순차 감소 중인지


def detect_contractions(high: pd.Series, low: pd.Series,
                        cfg: SwingConfig) -> ContractionResult:
    """
    최근 ``cfg.base_lookback`` 거래일 안에서 스윙을 잡고, 스윙 고점→다음 스윙
    저점으로의 pullback 폭을 계산한다. 최근 다리부터 거꾸로 보며

        leg[k] <= leg[k-1] * cfg.shrink_tolerance

    를 만족하는 연속 구간의 길이를 ``count`` 로 센다. 예) 18% → 11% → 6% 처럼
    순차 감소하면 count = 3.

    다리가 하나도 없으면 count=0, base_start_idx=None.
    """
    n = len(high)
    lb = min(cfg.base_lookback, n)
    start = n - lb
    win_high = high.iloc[start:]
    win_low = low.iloc[start:]

    swings = detect_swings(win_high, win_low, cfg.fractal_left, cfg.fractal_right)
    # positional 인덱스를 원본 기준으로 보정
    swings = [Swing(idx=s.idx + start, price=s.price, kind=s.kind) for s in swings]
    seq = _alternating(swings)

    # pullback 다리: 스윙 고점 뒤에 스윙 저점이 오는 쌍
    legs: list[float] = []          # (고점가, 저점가) → 폭%
    leg_hi_idx: list[int] = []
    for a, b in zip(seq, seq[1:]):
        if a.kind == "H" and b.kind == "L" and a.price > 0:
            legs.append((a.price - b.price) / a.price * 100.0)
            leg_hi_idx.append(a.idx)

    if not legs:
        return ContractionResult(count=0, widths=[], base_start_idx=None, tightening=False)

    # 최근 다리부터 거꾸로
    legs_recent_first = legs[::-1]
    hi_idx_recent_first = leg_hi_idx[::-1]

    count = 1
    for k in range(1, len(legs_recent_first)):
        if legs_recent_first[k - 1] <= legs_recent_first[k] * cfg.shrink_tolerance:
            count += 1
            if count >= cfg.max_legs:
                break
        else:
            break

    # base 시작: 수축에 참여한 가장 오래된 다리의 고점
    base_start_idx = hi_idx_recent_first[min(count, len(hi_idx_recent_first)) - 1]
    tightening = count >= cfg.min_contraction_count

    return ContractionResult(
        count=count,
        widths=[round(w, 2) for w in legs_recent_first[:cfg.max_legs]],
        base_start_idx=int(base_start_idx),
        tightening=tightening,
    )
