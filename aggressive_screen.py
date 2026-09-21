"""Aggressive long-only momentum breakout screen.

Research-only: generates observations and forward close-to-close outcomes. It
does not place orders and never changes the existing SEPA classifications.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


CONFIG = {
    "version": 1,
    "minHistory": 220,
    "breakoutDays": 20,
    "highDays": 252,
    "highProximityMin": 0.90,
    "rsMin": 80,
    "volumeRatioMin": 1.50,
    "clvMin": 0.70,
    "rsiMin": 55,
    "rsiMax": 75,
    "watchDistancePct": 3,
    "maxExtensionPct": 5,
    "krValue20Min": 2_000_000_000,
    "usValue20Min": 10_000_000,
    "stopAtrMultiple": 1.50,
    "maxInitialRiskPct": 7,
    "requiredSignalCount": 5,
    "timeStopDays": 5,
    "timeStopMinReturnPct": 3,
}


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    value = 100 - 100 / (1 + gain / loss)
    return value.where(loss != 0, 100).where((gain + loss) != 0, 50)


def indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.sort_index().loc[lambda x: ~x.index.duplicated(keep="last")].copy()
    if len(df) < CONFIG["minHistory"]:
        raise ValueError("지표 계산 이력 부족")
    for key in ("Open", "High", "Low", "Close", "Volume"):
        if key not in df or (~np.isfinite(df[key].tail(CONFIG["minHistory"]))).any():
            raise ValueError(f"{key} 데이터 누락")
    if (df[["Open", "High", "Low", "Close"]].tail(CONFIG["minHistory"]) <= 0).any().any():
        raise ValueError("가격 데이터 오류")
    if ((df.High < df.Low) | (df.High < df.Close) | (df.Low > df.Close)).tail(CONFIG["minHistory"]).any():
        raise ValueError("OHLC 가격 불일치")

    close = df.Close
    previous_close = close.shift()
    true_range = pd.concat(
        [df.High - df.Low, (df.High - previous_close).abs(), (df.Low - previous_close).abs()], axis=1
    ).max(axis=1)
    df["atr14"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    df["sma20"] = close.rolling(20).mean()
    df["sma50"] = close.rolling(50).mean()
    df["sma50Slope10Pct"] = (df.sma50 / df.sma50.shift(10) - 1) * 100
    df["high52w"] = df.High.rolling(CONFIG["highDays"], min_periods=200).max()
    df["breakoutLevel"] = df.High.shift(1).rolling(CONFIG["breakoutDays"]).max()
    df["volume20Prior"] = df.Volume.shift(1).rolling(20).mean()
    df["value20"] = (close * df.Volume).rolling(20).mean()
    df["volumeRatio"] = df.Volume / df.volume20Prior
    day_range = df.High - df.Low
    df["clv"] = ((close - df.Low) / day_range).where(day_range > 0, 0.5)
    df["rsi14"] = _rsi(close)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    signal = df.macd.ewm(span=9, adjust=False).mean()
    df["macdHistogram"] = df.macd - signal
    df["macdImproving"] = (df.macdHistogram > df.macdHistogram.shift(1)) | (
        (df.macd > 0) & (df.macd.shift(1) <= 0)
    )
    return df


def evaluate(row: dict, frame: pd.DataFrame | None, market: str) -> dict:
    out = {k: row.get(k) for k in ("code", "name", "market", "close", "priceAsOf")}
    out.update(
        status="UNKNOWN",
        inUniverse=row.get("inUniverse"),
        aggressiveWatch=None,
        aggressiveGo=None,
        aggressiveFail=None,
    )
    try:
        if frame is None:
            raise ValueError("종목 OHLC 누락")
        df = indicators(frame)
        today = df.iloc[-1]
        if row.get("status") != "OK" or not isinstance(row.get("inUniverse"), bool):
            raise ValueError("원본 데이터 확인불가")
        required = (
            "Close", "sma20", "sma50", "sma50Slope10Pct", "high52w", "breakoutLevel",
            "volumeRatio", "clv", "rsi14", "macdHistogram", "atr14", "value20",
        )
        if any(not math.isfinite(float(today[key])) for key in required):
            raise ValueError("핵심 지표 계산불가")

        close = float(today.Close)
        pivot = float(today.breakoutLevel)
        atr = float(today.atr14)
        rs = row.get("rsScore")
        if not isinstance(rs, (int, float)) or not math.isfinite(float(rs)):
            raise ValueError("RS 데이터 누락")

        trend = bool(close > today.sma20 > today.sma50 and today.sma50Slope10Pct > 0)
        near_high = bool(close / today.high52w >= CONFIG["highProximityMin"])
        rs_ok = bool(rs >= CONFIG["rsMin"])
        liquid = bool(today.value20 >= CONFIG["krValue20Min" if market == "kr" else "usValue20Min"])
        market_ok = row.get("regime") != "RED"
        breakout = bool(close > pivot)
        volume_ok = bool(today.volumeRatio >= CONFIG["volumeRatioMin"])
        clv_ok = bool(today.clv >= CONFIG["clvMin"])
        rsi_ok = bool(CONFIG["rsiMin"] <= today.rsi14 <= CONFIG["rsiMax"])
        macd_ok = bool(today.macdImproving)
        extension = (close / pivot - 1) * 100
        not_extended = bool(extension <= CONFIG["maxExtensionPct"])
        stop = max(pivot * 0.99, close - CONFIG["stopAtrMultiple"] * atr)
        risk = max(0.0, (close / stop - 1) * 100) if stop > 0 else math.inf
        risk_ok = bool(risk <= CONFIG["maxInitialRiskPct"])
        signal_count = sum((breakout, volume_ok, clv_ok, rs_ok, rsi_ok, macd_ok))
        eligible = bool(row["inUniverse"] and trend and near_high and rs_ok and liquid and market_ok)
        watch_distance = (close / pivot - 1) * 100
        watch = bool(eligible and -CONFIG["watchDistancePct"] <= watch_distance <= 0)
        go = bool(
            eligible and breakout and signal_count >= CONFIG["requiredSignalCount"]
            and not_extended and risk_ok
        )
        fast_fail = bool(not go and close < pivot and row.get("confirmedBo") is True)

        out.update(
            status="OK", priceAsOf=str(df.index[-1])[:10], close=close,
            aggressiveWatch=watch, aggressiveGo=go, aggressiveFail=fast_fail,
            trendOkAggressive=trend, nearHigh=near_high, rsOk=rs_ok,
            liquidityOk=liquid, marketOk=market_ok, breakout=breakout,
            volumeOk=volume_ok, clvOk=clv_ok, rsiOk=rsi_ok, macdOk=macd_ok,
            notExtended=not_extended, riskOk=risk_ok, signalCount=signal_count,
            sma20=float(today.sma20), sma50=float(today.sma50),
            sma50Slope10Pct=float(today.sma50Slope10Pct), high52w=float(today.high52w),
            highProximityPct=(close / today.high52w - 1) * 100,
            breakoutLevel=pivot, pivotDistancePct=watch_distance,
            volumeRatio=float(today.volumeRatio), clv=float(today.clv),
            rsi14=float(today.rsi14), macd=float(today.macd),
            macdHistogram=float(today.macdHistogram), atr14=atr,
            value20=float(today.value20), initialRiskPct=risk,
            referenceStop=stop,
            reason="공격 진입" if go else "돌파 대기" if watch else "조건 미충족",
            checks={
                "trend": trend, "nearHigh": near_high, "rs": rs_ok,
                "liquidity": liquid, "market": market_ok, "breakout": breakout,
                "volume": volume_ok, "clv": clv_ok, "rsi": rsi_ok,
                "macd": macd_ok, "extension": not_extended, "risk": risk_ok,
            },
        )
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as exc:
        out["reason"] = str(exc)
    return out


def export_aggressive(payload: dict, frames: dict[str, pd.DataFrame]) -> None:
    from research_tracker import digest, strategy_series_id, write_json

    market = payload["market"]
    rows = [evaluate(row, frames.get(row["code"]), market) for row in payload["rows"]]
    strategy = {
        "name": "공격형 모멘텀 돌파",
        "experimental": True,
        "config": CONFIG,
        "sourceHash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "universe": payload["strategy"]["config"]["universe"],
    }
    result = dict(
        payload,
        rows=rows,
        strategy=strategy,
        strategyId=digest(strategy),
        strategySeriesId=strategy_series_id(strategy),
        groups=["AGGR_WATCH", "AGGR_GO"],
        horizons=[1, 3, 5, 10, 20, 60],
    )
    write_json(Path(__file__).parent / "output" / f"research_input_aggressive_{market}.json", result)
