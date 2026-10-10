"""진입 지표 직접 계산 — 다른 탭에 진입 자료가 없는 후보용. technical_signals 정의를 복사했다(하위 시스템끼리 import 하지 않는 관례).
손절 = min(스윙저점 - 0.5*ATR, 종가 - 1.75*ATR), 손절폭 = (종가-손절)/종가*100, 추격 = RSI>=75 또는 60일 피벗 대비 +5% 이상."""
from __future__ import annotations

import logging

import pandas as pd

from . import config as C

log = logging.getLogger("daily_picks.entry")


def _rsi(close: pd.Series, period: int) -> float | None:
    delta = close.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    if pd.isna(gain.iloc[-1]) or pd.isna(loss.iloc[-1]):
        return None
    if loss.iloc[-1] == 0:
        return 100.0 if gain.iloc[-1] > 0 else 50.0
    return float(100.0 - 100.0 / (1.0 + gain.iloc[-1] / loss.iloc[-1]))


def entry_metrics(df: pd.DataFrame) -> dict | None:
    """df: 날짜 오름차순, 열 High/Low/Close. 계산할 수 없으면 None(0으로 채우지 않는다)."""
    need = max(C.PIVOT_LOOKBACK + 1, C.SWING_LOOKBACK, C.ATR_PERIOD + 1, C.RSI_PERIOD + 1)
    d = df[["High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(d) < need:
        return None
    high, low, close = d["High"], d["Low"], d["Close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / C.ATR_PERIOD, adjust=False, min_periods=C.ATR_PERIOD).mean().iloc[-1]
    swing = low.rolling(C.SWING_LOOKBACK, min_periods=1).min().iloc[-1]
    px = float(close.iloc[-1])
    if pd.isna(atr) or px <= 0:
        return None
    structural = float(swing) - C.STOP_ATR_BUFFER_MULT * float(atr)
    atr_stop = px - C.ATR_STOP_MULT * float(atr)
    if structural <= 0 or atr_stop <= 0:
        return None
    stop = min(structural, atr_stop)
    pivot = high.rolling(C.PIVOT_LOOKBACK, min_periods=C.PIVOT_LOOKBACK).max().shift(1).iloc[-1]
    pivot_dist = None if pd.isna(pivot) or pivot <= 0 else (px / float(pivot) - 1.0) * 100.0
    rsi = _rsi(close, C.RSI_PERIOD)
    chase = bool((rsi is not None and rsi >= C.RSI_CHASE) or (pivot_dist is not None and pivot_dist >= C.PIVOT_CHASE_PCT))
    return {"close": round(px, 4), "stop": round(stop, 4), "riskPct": round((px - stop) / px * 100.0, 2),
            "rsi": None if rsi is None else round(rsi, 1), "pivotDistancePct": None if pivot_dist is None else round(pivot_dist, 2),
            "chase": chase}


def fetch_entry_metrics(symbols: list[str], hints: dict[str, str] | None = None) -> dict[str, dict]:
    """Yahoo 일봉(조정 안 함)으로 계산. hints: 심볼 → Yahoo 심볼(한국 .KS/.KQ). 실패한 종목은 결과에서 빠진다."""
    if not symbols:
        return {}
    import yfinance as yf

    ysym = {s: (hints or {}).get(s, s) for s in symbols}
    try:
        frame = yf.download(sorted(set(ysym.values())), period="1y", interval="1d", auto_adjust=False, progress=False,
                            threads=True, group_by="ticker")
    except Exception as exc:  # noqa: BLE001
        log.warning("진입 지표용 Yahoo 다운로드 실패: %s", exc)
        return {}
    out: dict[str, dict] = {}
    for s, y in ysym.items():
        try:
            sub = frame[y] if isinstance(frame.columns, pd.MultiIndex) else frame
            m = entry_metrics(sub.dropna(how="all"))
        except Exception:  # noqa: BLE001
            m = None
        if m:
            out[s] = m
    return out
