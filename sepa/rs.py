"""
sepa/rs.py — RS v2 (상대강도)
=============================

기존 RS (3/6/12개월 달력일 초과수익의 단순 평균) 를 아래로 교체한다.

1. 각 종목의 **거래일 기준** 초과수익 4개:

       ER_k = stock_return_k - benchmark_return_k    (k = 21, 63, 126, 252 거래일)

   benchmark 는 종목의 거래일 인덱스에 맞춰 ffill 정렬한다.

2. 각 ER_k 를 **유니버스 내에서 percentile rank(0~100)** 로 변환.

3. 가중합:

       RS_SCORE = 0.10*p21 + 0.40*p63 + 0.30*p126 + 0.20*p252     (0~100)

4. ``rs_change_20d`` = 오늘 RS_SCORE − 20거래일 전 RS_SCORE
   (20거래일 전 시점으로 유니버스를 다시 랭킹해서 계산 — 미래 데이터 미사용).

5. ``RS_LINE`` = stock_close / benchmark_close, 최근 126거래일 신고가 여부.

percentile 대상은 **데이터 정상 종목만**. 한국은 소속시장(KOSPI/KOSDAQ) 지수,
미국은 S&P500 지수를 벤치마크로 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sepa.config import RsConfig


@dataclass
class RsV2:
    er21: float | None = None
    er63: float | None = None
    er126: float | None = None
    er252: float | None = None
    pct21: float | None = None
    pct63: float | None = None
    pct126: float | None = None
    pct252: float | None = None
    rs_score: float | None = None
    rs_score_20d_ago: float | None = None
    rs_change_20d: float | None = None
    rs_line_new_high: bool | None = None


def _align_benchmark(stock_close: pd.Series, bench_close: pd.Series) -> pd.Series:
    """벤치마크를 종목의 거래일 인덱스에 맞춘다 (해당 시점 또는 그 이전 값)."""
    return bench_close.reindex(stock_close.index).ffill()


def _period_return(series: pd.Series, period: int, end_pos: int) -> float | None:
    """end_pos(positional, 포함) 시점 기준 period 거래일 수익률."""
    if end_pos - period < 0 or end_pos >= len(series):
        return None
    now = series.iloc[end_pos]
    past = series.iloc[end_pos - period]
    if pd.isna(now) or pd.isna(past) or past <= 0:
        return None
    return float(now / past - 1.0)


def excess_returns(stock_close: pd.Series, bench_close: pd.Series,
                   cfg: RsConfig, as_of_offset: int = 0) -> dict[str, float | None]:
    """
    ER_k = stock_return_k - benchmark_return_k, k ∈ {21,63,126,252} 거래일.

    as_of_offset > 0 이면 "그만큼 전 거래일" 을 종점으로 계산한다 (과거 시점 재현).
    """
    bench = _align_benchmark(stock_close, bench_close)
    end_pos = len(stock_close) - 1 - as_of_offset
    if end_pos < 0:
        return {"er21": None, "er63": None, "er126": None, "er252": None}

    periods = {
        "er21": cfg.period_short,
        "er63": cfg.period_mid,
        "er126": cfg.period_long,
        "er252": cfg.period_year,
    }
    out: dict[str, float | None] = {}
    for name, p in periods.items():
        s_ret = _period_return(stock_close, p, end_pos)
        b_ret = _period_return(bench, p, end_pos)
        out[name] = None if (s_ret is None or b_ret is None) else float(s_ret - b_ret)
    return out


def _percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """{code: value} → {code: percentile 0~100}. 동점은 평균 순위."""
    if not values:
        return {}
    s = pd.Series(values)
    ranks = s.rank(pct=True) * 100.0
    return {code: float(r) for code, r in ranks.items()}


def rank_universe(er_by_code: dict[str, dict[str, float | None]],
                  cfg: RsConfig) -> dict[str, dict[str, float | None]]:
    """
    유니버스 전체의 ER 딕셔너리를 받아 종목별 percentile + RS_SCORE 를 계산.

    반환: {code: {"pct21":..,"pct63":..,"pct126":..,"pct252":..,"rs_score":..}}
    4개 기간 중 하나라도 없는 종목은 rs_score = None.
    """
    keys = ["er21", "er63", "er126", "er252"]
    weights = {
        "er21": cfg.weight_short, "er63": cfg.weight_mid,
        "er126": cfg.weight_long, "er252": cfg.weight_year,
    }
    pct_by_key: dict[str, dict[str, float]] = {}
    for key in keys:
        present = {c: v[key] for c, v in er_by_code.items() if v.get(key) is not None}
        pct_by_key[key] = _percentile_ranks(present)

    out: dict[str, dict[str, float | None]] = {}
    for code in er_by_code:
        row: dict[str, float | None] = {}
        complete = True
        score = 0.0
        for key in keys:
            p = pct_by_key[key].get(code)
            row[f"pct{key[2:]}"] = p
            if p is None:
                complete = False
            else:
                score += weights[key] * p
        row["rs_score"] = round(score, 2) if complete else None
        out[code] = row
    return out


def rs_line(stock_close: pd.Series, bench_close: pd.Series) -> pd.Series:
    """RS_LINE = 종목 종가 / 벤치마크 종가 (정렬 후)."""
    bench = _align_benchmark(stock_close, bench_close)
    return stock_close / bench


def rs_line_new_high(line: pd.Series, lookback: int) -> bool | None:
    """RS_LINE 이 최근 lookback 거래일 최고치 이상인가 (인과적)."""
    s = line.dropna()
    if len(s) < 2:
        return None
    window = s.iloc[-min(lookback, len(s)):]
    return bool(s.iloc[-1] >= window.max())


def compute_rs_v2(ohlcv_map: dict[str, pd.DataFrame],
                  bench_by_code: dict[str, pd.Series],
                  cfg: RsConfig) -> dict[str, RsV2]:
    """
    유니버스 전체 RS v2 계산.

    ohlcv_map     : {code: OHLCV DataFrame}  (정상 종목만)
    bench_by_code : {code: 벤치마크 종가 Series}  (KR: 소속시장 지수 / US: S&P500)
    """
    codes = list(ohlcv_map)

    er_today: dict[str, dict[str, float | None]] = {}
    er_past: dict[str, dict[str, float | None]] = {}
    for code in codes:
        close = ohlcv_map[code]["Close"].astype(float)
        bench = bench_by_code.get(code)
        if bench is None or close.empty:
            er_today[code] = {"er21": None, "er63": None, "er126": None, "er252": None}
            er_past[code] = dict(er_today[code])
            continue
        er_today[code] = excess_returns(close, bench, cfg, as_of_offset=0)
        er_past[code] = excess_returns(close, bench, cfg, as_of_offset=cfg.accel_lookback)

    ranked_today = rank_universe(er_today, cfg)
    ranked_past = rank_universe(er_past, cfg)

    result: dict[str, RsV2] = {}
    for code in codes:
        rt = ranked_today[code]
        rp = ranked_past[code]
        score_now = rt["rs_score"]
        score_past = rp["rs_score"]
        change = None
        if score_now is not None and score_past is not None:
            change = round(score_now - score_past, 2)

        close = ohlcv_map[code]["Close"].astype(float)
        bench = bench_by_code.get(code)
        line_high = None
        if bench is not None and not close.empty:
            line_high = rs_line_new_high(rs_line(close, bench), cfg.rs_line_high_lookback)

        e = er_today[code]
        result[code] = RsV2(
            er21=e["er21"], er63=e["er63"], er126=e["er126"], er252=e["er252"],
            pct21=rt["pct21"], pct63=rt["pct63"], pct126=rt["pct126"], pct252=rt["pct252"],
            rs_score=score_now,
            rs_score_20d_ago=score_past,
            rs_change_20d=change,
            rs_line_new_high=line_high,
        )
    return result
