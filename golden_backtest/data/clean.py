"""원본 일봉 정리: 미확정 봉 제거, 결측 행 제거(조용히 채우지 않고 내역을 돌려준다).

clean_ohlcv의 취지는 technical_signals/data.py에서 가져왔다(종가 NaN 봉이 남으면 모든 지표가 NaN이 된다).
"""
from __future__ import annotations

import pandas as pd

PRICE_COLS = ["Open", "High", "Low", "Close", "Adj Close"]


def normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out.index.name = "date"
    return out[~out.index.duplicated(keep="last")].sort_index()


def clean_ohlcv(df: pd.DataFrame, cutoff: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """cutoff 이후(미확정) 봉과 가격이 비었거나 0 이하인 행을 버린다.

    반환: (정리된 프레임, {"unclosed_dropped": n, "nan_dropped": [날짜, ...],
                          "nan_runs": [{"start","end","bars"}, ...]})  # nan_runs: 제거된 행이 연속된 구간
    """
    df = normalize_index(df)
    unclosed = df.index > cutoff
    df = df[~unclosed]
    cols = [c for c in PRICE_COLS if c in df.columns]
    bad = (df[cols].isna() | (df[cols] <= 0)).any(axis=1)
    info = {"unclosed_dropped": int(unclosed.sum()), "nan_dropped": [d.date().isoformat() for d in df.index[bad]],
            "nan_runs": _runs(df.index, bad.values)}
    return df[~bad], info


def _runs(index: pd.DatetimeIndex, mask) -> list[dict]:
    """mask가 True로 연속된 구간(원본 행 순서 기준)."""
    runs, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            runs.append({"start": index[i].date().isoformat(), "end": index[j].date().isoformat(), "bars": j - i + 1})
            i = j + 1
        else:
            i += 1
    return runs


def strip_leading_placeholders(df: pd.DataFrame, min_run: int) -> tuple[pd.DataFrame, dict | None]:
    """종목 시작 구간에서 O=H=L=C이고 거래량 0인 봉이 min_run개 이상 연속이면 그 연속 구간 끝까지 잘라낸다.

    공급자가 상장 전(또는 미국 상장 전) 구간을 멈춘 가격으로 채워 둔 경우다. 시작 구간만 대상이고, 중간의 정지 봉은 건드리지 않는다.
    반환: (잘라낸 프레임, {"bars","first","last"} 또는 None).
    """
    flat = ((df["Open"] == df["High"]) & (df["High"] == df["Low"]) & (df["Low"] == df["Close"]) & (df["Volume"] == 0)).values
    n = 0
    while n < len(flat) and flat[n]:
        n += 1
    if n < min_run:
        return df, None
    return df.iloc[n:], {"bars": int(n), "first": df.index[0].date().isoformat(), "last": df.index[n - 1].date().isoformat()}
