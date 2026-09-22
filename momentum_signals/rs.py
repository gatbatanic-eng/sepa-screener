"""momentum_signals/rs.py — 유니버스 상대강도(RS) 퍼센타일.

sepa/rs.py의 IBD 스타일 RS_SCORE(0.10·p21 + 0.40·p63 + 0.30·p126 + 0.20·p252)
설계를 독립적으로 재구현한다(서브시스템 간 import 없음 — 이 저장소 관례).
퍼센타일은 시장(국내/미국) 안에서만 매긴다 — 스펙 4번 "시장 규모 차이로
인한 편중 방지".
"""
from __future__ import annotations

import pandas as pd

from indicators import period_return_pct


def compute_returns(close: pd.Series, lookbacks: tuple[int, ...]) -> dict[int, float | None]:
    out: dict[int, float | None] = {}
    for lb in lookbacks:
        r = period_return_pct(close, lb)
        val = r.iloc[-1] if len(r) else None
        out[lb] = None if val is None or pd.isna(val) else float(val)
    return out


def _percentile_rank(values: pd.Series, x: float) -> float:
    """values(같은 시장 전체) 중 x보다 작거나 같은 비율(0~100)."""
    valid = values.dropna()
    if len(valid) == 0:
        return 50.0
    return float((valid <= x).sum()) / len(valid) * 100.0


def rs_scores_for_market(returns_by_code: dict[str, dict[int, float | None]],
                          lookbacks: tuple[int, ...],
                          weights: tuple[float, ...]) -> dict[str, float | None]:
    """{code: {lookback: return%}} → {code: RS_SCORE(0~100) 또는 None}.
    sepa와 동일하게 4개 기간 중 하나라도 없으면 RS_SCORE = None(불완전 데이터
    종목이 랭킹을 왜곡하지 않도록)."""
    frame = pd.DataFrame(returns_by_code).T  # index=code, columns=lookback
    pct = pd.DataFrame(index=frame.index, columns=lookbacks, dtype=float)
    for lb in lookbacks:
        col = frame[lb] if lb in frame.columns else pd.Series(dtype=float)
        pct[lb] = col.map(lambda x: _percentile_rank(col, x) if pd.notna(x) else float("nan"))

    out: dict[str, float | None] = {}
    for code in frame.index:
        row = pct.loc[code]
        if row.isna().any():
            out[code] = None
            continue
        score = sum(row[lb] * w for lb, w in zip(lookbacks, weights))
        out[code] = round(float(score), 2)
    return out
