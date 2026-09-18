"""
technical_signals/signals.py — 지표 시계열 → 신호 판정 + 그룹 점수 + 판정
=============================================================================

2026-09-18 리뷰 반영 (이전 버전 대비 구조를 다시 짰다):

1. **지표 중복 제거**: 예전엔 MACD·RSI·스토캐스틱을 flat 가중합해서 셋 다
   좋으면 점수가 실제로 부풀려졌다. 지금은 그룹(추세/트리거/모멘텀/변동성/
   거래량)별로 배점을 고정하고, 그룹 안에서는 "판정 가능한 멤버 중 True
   비율"만큼만 그 그룹 배점을 받는다 — 그룹 안에 지표가 몇 개든 그룹 배점을
   못 넘는다.
2. **추세추종 vs 박스권반등 분리**: 방향이 반대일 수 있는 두 전략(고점돌파
   대 하단반등)을 한 점수로 섞지 않는다. `evaluate_signals()`가
   trend_score/rebound_score를 따로 계산하고, 최종 판정(trend_verdict/
   rebound_verdict)도 트랙별로 따로 낸다.
3. **진입 위치 게이트**: 점수가 높아도 이미 많이 오른 종목은 매수 신호가
   아니다. 피벗 대비 이격(pivot_distance_pct), 손절 리스크(risk_pct),
   추격 경고(chase_warning)를 하드 게이트로 둔다.
4. **손절/리스크**: 스윙저점-ATR버퍼(구조적)와 ATR배수(변동성 기준) 중
   더 보수적인(=리스크를 더 크게 잡는) 쪽을 손절가로 쓴다.
5. **시장 국면**: 이 파일은 개별 종목만 계산한다. 시장 지수·breadth 기반
   판정은 `regime.py`에서 따로 계산해 `pipeline.py`가 `compute_verdict()`에
   넘겨준다(종목별 계산 시점엔 유니버스 전체의 breadth를 아직 모르므로).

**백테스트(룩어헤드 방지 포함)는 이번 범위에 없다** — SEPA의
research_tracker.py 같은 백테스트 인프라 자체가 technical_signals엔 없어서
지표 하나 고치는 수준이 아니라 별도 서브시스템이 필요하기 때문이다.

**모든 판정은 참고용이며 매수·매도를 권유하지 않는다.**
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import config as cfg
from indicators import (
    adx, atr, bollinger, bollinger_bandwidth, clv, crossed_down_recent, crossed_up_recent,
    disparity, macd, obv, rolling_pivot_high, rsi, sma, stochastic, swing_low,
)

REVIEW = "매수검토"
READY = "진입준비"
HOLD = "진입보류"


@dataclass
class SignalResult:
    close: float | None = None

    # --- 이동평균: 이벤트(크로스)와 상태(정배열) 둘 다 ---
    sma_fast: float | None = None
    sma_slow: float | None = None
    golden_cross: bool | None = None
    dead_cross: bool | None = None
    trend_aligned: bool | None = None  # 상태: close>SMA200 AND SMA50>SMA200

    # --- MACD ---
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    macd_bull_cross: bool | None = None
    macd_bear_cross: bool | None = None

    # --- RSI ---
    rsi_value: float | None = None
    rsi_oversold_exit: bool | None = None    # 박스권반등 트랙용(과매도 회복 이벤트)
    rsi_overbought: bool | None = None
    rsi_healthy_trend: bool | None = None    # 추세추종 트랙용(50~70 유지 = 건강한 상승)

    # --- 스토캐스틱 ---
    stoch_k: float | None = None
    stoch_d: float | None = None
    stoch_bull_cross: bool | None = None

    # --- 볼린저밴드 ---
    bb_mid: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    bb_bandwidth: float | None = None
    bb_lower_revert: bool | None = None
    bb_squeeze: bool | None = None
    bb_upper_breakout: bool | None = None

    # --- 거래량 ---
    obv_rising: bool | None = None
    volume_ratio50: float | None = None      # 거래량/50일평균

    # --- ADX ---
    adx_value: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    adx_trending: bool | None = None

    # --- 이격도 ---
    disparity20: float | None = None
    disparity_oversold: bool | None = None   # 박스권반등 "위치" 조건

    # --- 피벗/돌파(추세추종 진입 트리거) ---
    pivot: float | None = None
    pivot_distance_pct: float | None = None
    breakout_trigger: bool | None = None
    chase_warning: bool | None = None

    # --- 손절/리스크 ---
    atr_value: float | None = None
    swing_low_price: float | None = None
    stop_price: float | None = None
    risk_pct: float | None = None
    risk_too_high: bool | None = None

    # --- 그룹 점수(0~100, 랭킹용, 트랙별) ---
    trend_score: float | None = None
    rebound_score: float | None = None

    # --- 최종 판정(트랙별, 하드 게이트 반영) ---
    trend_verdict: str | None = None
    trend_verdict_reasons: list[str] = field(default_factory=list)
    rebound_verdict: str | None = None
    rebound_verdict_reasons: list[str] = field(default_factory=list)

    reasons: list[str] = field(default_factory=list)  # 데이터 부족 등 계산 자체가 안 된 사유


def evaluate_signals(ohlcv: pd.DataFrame, min_trading_value: float = cfg.MIN_TRADING_VALUE_KRW) -> SignalResult:
    """ohlcv: 컬럼 Open/High/Low/Close/Volume, DatetimeIndex 오름차순.
    min_trading_value: 돌파 확인용 최소 거래대금(통화 단위는 시장에 맞춰
    pipeline.py가 넘겨준다 — KR은 원화, US는 달러).
    시장 국면(regime)은 여기서 모르므로 trend_verdict/rebound_verdict는
    비워두고, pipeline.py가 전 종목 계산 후 compute_verdict()로 채운다."""
    r = SignalResult()
    n = len(ohlcv)
    if n < cfg.MIN_TRADING_DAYS:
        r.reasons.append(f"데이터 부족 ({n}봉 < {cfg.MIN_TRADING_DAYS})")
        return r

    high = ohlcv["High"].astype(float)
    low = ohlcv["Low"].astype(float)
    close = ohlcv["Close"].astype(float)
    volume = ohlcv["Volume"].astype(float)
    r.close = float(close.iloc[-1])

    # --- 이동평균 ---
    sma_fast = sma(close, cfg.MA_FAST)
    sma_slow = sma(close, cfg.MA_SLOW)
    if not pd.isna(sma_fast.iloc[-1]) and not pd.isna(sma_slow.iloc[-1]):
        r.sma_fast = round(float(sma_fast.iloc[-1]), 4)
        r.sma_slow = round(float(sma_slow.iloc[-1]), 4)
        r.golden_cross = bool(crossed_up_recent(sma_fast, sma_slow, cfg.CROSS_RECENT_WINDOW).iloc[-1])
        r.dead_cross = bool(crossed_down_recent(sma_fast, sma_slow, cfg.CROSS_RECENT_WINDOW).iloc[-1])
        r.trend_aligned = bool(r.close > r.sma_slow and r.sma_fast > r.sma_slow)

    # --- MACD ---
    macd_line, macd_signal, macd_hist = macd(close, cfg.MACD_FAST, cfg.MACD_SLOW, cfg.MACD_SIGNAL)
    if not pd.isna(macd_line.iloc[-1]) and not pd.isna(macd_signal.iloc[-1]):
        r.macd_line = round(float(macd_line.iloc[-1]), 4)
        r.macd_signal = round(float(macd_signal.iloc[-1]), 4)
        r.macd_hist = round(float(macd_hist.iloc[-1]), 4)
        r.macd_bull_cross = bool(crossed_up_recent(macd_line, macd_signal, cfg.MACD_RECENT_WINDOW).iloc[-1])
        r.macd_bear_cross = bool(crossed_down_recent(macd_line, macd_signal, cfg.MACD_RECENT_WINDOW).iloc[-1])

    # --- RSI ---
    rsi_series = rsi(close, cfg.RSI_PERIOD)
    if not pd.isna(rsi_series.iloc[-1]):
        r.rsi_value = round(float(rsi_series.iloc[-1]), 2)
        oversold_line = pd.Series(cfg.RSI_OVERSOLD, index=rsi_series.index)
        r.rsi_oversold_exit = bool(crossed_up_recent(rsi_series, oversold_line, cfg.RSI_RECENT_WINDOW).iloc[-1])
        r.rsi_overbought = r.rsi_value >= cfg.RSI_OVERBOUGHT
        r.rsi_healthy_trend = cfg.RSI_HEALTHY_TREND_LO <= r.rsi_value <= cfg.RSI_HEALTHY_TREND_HI

    # --- 스토캐스틱 ---
    stoch_k, stoch_d = stochastic(high, low, close, cfg.STOCH_K_PERIOD, cfg.STOCH_K_SMOOTH, cfg.STOCH_D_SMOOTH)
    if not pd.isna(stoch_k.iloc[-1]) and not pd.isna(stoch_d.iloc[-1]):
        r.stoch_k = round(float(stoch_k.iloc[-1]), 2)
        r.stoch_d = round(float(stoch_d.iloc[-1]), 2)
        crossed = crossed_up_recent(stoch_k, stoch_d, cfg.STOCH_RECENT_WINDOW)
        was_oversold = (stoch_d.rolling(cfg.STOCH_RECENT_WINDOW, min_periods=1).min() <= cfg.STOCH_OVERSOLD)
        r.stoch_bull_cross = bool(crossed.iloc[-1] and was_oversold.iloc[-1])

    # --- 볼린저밴드 ---
    bb_mid, bb_upper, bb_lower = bollinger(close, cfg.BB_PERIOD, cfg.BB_STD)
    avg_vol20 = volume.rolling(20, min_periods=20).mean()
    if not pd.isna(bb_mid.iloc[-1]) and not pd.isna(bb_upper.iloc[-1]) and not pd.isna(bb_lower.iloc[-1]):
        r.bb_mid = round(float(bb_mid.iloc[-1]), 4)
        r.bb_upper = round(float(bb_upper.iloc[-1]), 4)
        r.bb_lower = round(float(bb_lower.iloc[-1]), 4)
        bandwidth = bollinger_bandwidth(bb_mid, bb_upper, bb_lower)
        if not pd.isna(bandwidth.iloc[-1]):
            r.bb_bandwidth = round(float(bandwidth.iloc[-1]), 4)
            recent_bw = bandwidth.iloc[-cfg.BB_SQUEEZE_LOOKBACK:]
            r.bb_squeeze = bool(recent_bw.notna().sum() >= cfg.BB_PERIOD and
                                 bandwidth.iloc[-1] <= recent_bw.min())
        r.bb_lower_revert = bool(crossed_up_recent(close, bb_lower, cfg.CROSS_RECENT_WINDOW).iloc[-1])

        if not pd.isna(avg_vol20.iloc[-1]) and avg_vol20.iloc[-1] > 0:
            vol_ratio20 = volume.iloc[-1] / avg_vol20.iloc[-1]
            r.bb_upper_breakout = bool(close.iloc[-1] > bb_upper.iloc[-1] and
                                        vol_ratio20 >= cfg.BB_BREAKOUT_VOL_RATIO)

    # --- OBV / 거래량 ---
    obv_series = obv(close, volume)
    obv_sma = sma(obv_series, cfg.OBV_SMA_PERIOD)
    if not pd.isna(obv_sma.iloc[-1]):
        r.obv_rising = bool(obv_series.iloc[-1] > obv_sma.iloc[-1])

    avg_vol50 = volume.rolling(50, min_periods=50).mean()
    if not pd.isna(avg_vol50.iloc[-1]) and avg_vol50.iloc[-1] > 0:
        r.volume_ratio50 = round(float(volume.iloc[-1] / avg_vol50.iloc[-1]), 3)

    # --- ADX ---
    adx_series, plus_di, minus_di = adx(high, low, close, cfg.ADX_PERIOD)
    if not pd.isna(adx_series.iloc[-1]):
        r.adx_value = round(float(adx_series.iloc[-1]), 2)
        r.plus_di = round(float(plus_di.iloc[-1]), 2) if not pd.isna(plus_di.iloc[-1]) else None
        r.minus_di = round(float(minus_di.iloc[-1]), 2) if not pd.isna(minus_di.iloc[-1]) else None
        r.adx_trending = r.adx_value >= cfg.ADX_TRENDING_MIN

    # --- 이격도 ---
    disp_ma = sma(close, cfg.DISPARITY_MA_PERIOD)
    disp = disparity(close, disp_ma)
    if not pd.isna(disp.iloc[-1]):
        r.disparity20 = round(float(disp.iloc[-1]), 2)
        r.disparity_oversold = r.disparity20 <= cfg.REBOUND_DISPARITY_MAX

    # --- 피벗/돌파(추세추종 진입 트리거, 당일 제외 피벗으로 자기참조 방지) ---
    pivot_series = rolling_pivot_high(high, cfg.PIVOT_LOOKBACK)
    if not pd.isna(pivot_series.iloc[-1]) and pivot_series.iloc[-1] > 0:
        r.pivot = round(float(pivot_series.iloc[-1]), 4)
        r.pivot_distance_pct = round((r.close / r.pivot - 1.0) * 100.0, 2)

        above_pivot = close > pivot_series
        vol_ratio_series = (volume / avg_vol20).where(avg_vol20 > 0)
        recent_breakout = (above_pivot & (vol_ratio_series >= cfg.BB_BREAKOUT_VOL_RATIO)).fillna(False)

        clv_today = clv(float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1]))
        clv_ok = clv_today is not None and clv_today >= cfg.BREAKOUT_CLV_MIN
        trading_value_today = float(close.iloc[-1]) * float(volume.iloc[-1])
        value_ok = trading_value_today >= min_trading_value

        r.breakout_trigger = bool(
            recent_breakout.iloc[-cfg.BREAKOUT_RECENT_WINDOW:].any() and clv_ok and value_ok
        )

    # --- 추격 경고 ---
    if r.rsi_value is not None or r.pivot_distance_pct is not None:
        rsi_chase = r.rsi_value is not None and r.rsi_value >= cfg.RSI_CHASE_WARNING
        dist_chase = r.pivot_distance_pct is not None and r.pivot_distance_pct >= cfg.PIVOT_DISTANCE_HOLD
        r.chase_warning = bool(rsi_chase or dist_chase)

    # --- 손절/리스크: 스윙저점-ATR버퍼(구조적) vs ATR배수, 더 보수적(=리스크 큰) 쪽 ---
    atr_series = atr(high, low, close, cfg.ADX_PERIOD)
    swing_series = swing_low(low, cfg.SWING_LOW_LOOKBACK)
    if not pd.isna(atr_series.iloc[-1]) and not pd.isna(swing_series.iloc[-1]):
        r.atr_value = round(float(atr_series.iloc[-1]), 4)
        r.swing_low_price = round(float(swing_series.iloc[-1]), 4)
        structural_stop = r.swing_low_price - cfg.STOP_ATR_BUFFER_MULT * r.atr_value
        atr_stop = r.close - cfg.ATR_STOP_MULT * r.atr_value
        # ATR이 종가 대비 비정상적으로 커서(품질이 나쁜/희박한 데이터) 손절가가
        # 0 이하로 나오면 그 자체가 신뢰할 수 없는 값이므로 억지로 보여주지
        # 않고 판정불가(None)로 남긴다 — 실데이터 전체 스크리닝 중 발견됨.
        if structural_stop > 0 and atr_stop > 0:
            r.stop_price = round(min(structural_stop, atr_stop), 4)  # 더 낮은(=더 보수적) 쪽
            if r.close > 0:
                r.risk_pct = round((r.close - r.stop_price) / r.close * 100.0, 2)
                r.risk_too_high = r.risk_pct > cfg.MAX_RISK_PCT

    r.trend_score = _group_score(_trend_group_fractions(r), cfg.TREND_GROUP_WEIGHTS)
    r.rebound_score = _group_score(_rebound_group_fractions(r), cfg.REBOUND_GROUP_WEIGHTS)

    return r


def _fraction(members: dict[str, bool | None]) -> float | None:
    known = [v for v in members.values() if v is not None]
    if not known:
        return None
    return sum(1 for v in known if v) / len(known)


def _trend_group_fractions(r: SignalResult) -> dict[str, float | None]:
    return {
        "trend": _fraction({"trend_aligned": r.trend_aligned, "golden_cross": r.golden_cross,
                             "adx_trending": r.adx_trending}),
        "trigger": _fraction({"breakout_trigger": r.breakout_trigger}),
        "momentum": _fraction({"macd_bull_cross": r.macd_bull_cross, "rsi_healthy_trend": r.rsi_healthy_trend}),
        "volatility": _fraction({"bb_squeeze": r.bb_squeeze}),
        "volume": _fraction({"obv_rising": r.obv_rising}),
    }


def _rebound_group_fractions(r: SignalResult) -> dict[str, float | None]:
    return {
        "position": _fraction({"disparity_oversold": r.disparity_oversold}),
        "trigger": _fraction({"bb_lower_revert": r.bb_lower_revert}),
        "momentum": _fraction({"rsi_oversold_exit": r.rsi_oversold_exit, "stoch_bull_cross": r.stoch_bull_cross}),
        "volatility": _fraction({"bb_squeeze": r.bb_squeeze}),
        "volume": _fraction({"obv_rising": r.obv_rising}),
    }


def _group_score(fractions: dict[str, float | None], weights: dict[str, float]) -> float | None:
    available = {k: v for k, v in fractions.items() if v is not None}
    if not available:
        return None
    total_w = sum(weights[k] for k in available)
    if total_w <= 0:
        return None
    score = sum(available[k] * weights[k] for k in available) / total_w * 100.0
    return round(score, 1)


def compute_verdict(r: SignalResult, regime: str | None) -> None:
    """유니버스 전체를 다 계산해 regime(시장 국면)을 알게 된 뒤 pipeline.py가
    호출한다. trend_verdict/rebound_verdict를 r에 채운다(제자리 수정)."""
    r.trend_verdict, r.trend_verdict_reasons = _trend_verdict(r, regime)
    r.rebound_verdict, r.rebound_verdict_reasons = _rebound_verdict(r, regime)


def _trend_verdict(r: SignalResult, regime: str | None) -> tuple[str | None, list[str]]:
    # risk_too_high는 base 자격요건이 아니라 아래 HOLD 사유로만 쓴다 — base에
    # 넣으면 "위험 초과"인 경우 HOLD 대신 곧장 None(판정불가)이 돼버려서
    # "검토는 됐지만 위험해서 보류"라는 원래 의도(사용자 리뷰 8번)가 안 산다.
    if r.risk_too_high is None:
        return None, []  # 위험을 판정할 수 없으면 아예 판정하지 않는다

    score_ok = (r.trend_score >= cfg.SCORE_REVIEW_MIN) if r.trend_score is not None else None
    pivot_ok = (r.pivot_distance_pct <= cfg.PIVOT_DISTANCE_MAX_REVIEW) if r.pivot_distance_pct is not None else None
    base = {"trend_aligned": r.trend_aligned, "breakout_trigger": r.breakout_trigger,
            "score": score_ok, "pivot_ok": pivot_ok}
    if any(v is None for v in base.values()):
        return None, []
    if not all(base.values()):
        return None, []

    hold_reasons = []
    if r.chase_warning:
        hold_reasons.append("추격경고(RSI 75+ 또는 피벗대비 +5%↑)")
    if r.risk_too_high:
        hold_reasons.append("구조적손절위험 7%초과")
    if regime == "RED":
        hold_reasons.append("시장국면 RED")
    if hold_reasons:
        return HOLD, hold_reasons

    ready = {
        "volume": r.volume_ratio50 is not None and r.volume_ratio50 >= cfg.READY_VOLUME_RATIO_MIN,
        "macd_hist": r.macd_hist is not None and r.macd_hist > 0,
        "rsi_band": r.rsi_healthy_trend is True,
        "regime": regime in ("GREEN", "YELLOW"),
        "score": r.trend_score is not None and r.trend_score >= cfg.SCORE_READY_MIN,
    }
    if all(ready.values()):
        return READY, ["거래량확인", "MACD히스토그램양수", "RSI50~70", f"시장국면{regime}"]
    return REVIEW, ["정배열", "돌파트리거", f"점수{r.trend_score:.0f}", "리스크7%이내"]


def _rebound_verdict(r: SignalResult, regime: str | None) -> tuple[str | None, list[str]]:
    if r.risk_too_high is None:
        return None, []  # 위험을 판정할 수 없으면 아예 판정하지 않는다(trend 트랙과 동일한 원칙)

    score_ok = (r.rebound_score >= cfg.SCORE_REVIEW_MIN) if r.rebound_score is not None else None
    base = {"position": r.disparity_oversold, "trigger": r.bb_lower_revert, "score": score_ok}
    if any(v is None for v in base.values()):
        return None, []
    if not all(base.values()):
        return None, []

    hold_reasons = []
    if r.rsi_value is not None and r.rsi_value >= cfg.RSI_CHASE_WARNING:
        hold_reasons.append("이미 많이 반등함(RSI 75+)")
    if r.risk_too_high:
        hold_reasons.append("구조적손절위험 7%초과")
    if regime == "RED":
        hold_reasons.append("시장국면 RED")
    if hold_reasons:
        return HOLD, hold_reasons

    ready = {
        "volume": r.volume_ratio50 is not None and r.volume_ratio50 >= cfg.READY_VOLUME_RATIO_MIN,
        "momentum": r.rsi_oversold_exit is True or r.stoch_bull_cross is True,
        "regime": regime in ("GREEN", "YELLOW"),
        "score": r.rebound_score is not None and r.rebound_score >= cfg.SCORE_READY_MIN,
    }
    if all(ready.values()):
        return READY, ["거래량확인", "모멘텀회복확인", f"시장국면{regime}"]
    return REVIEW, ["눌린위치", "하단복귀트리거", f"점수{r.rebound_score:.0f}", "리스크7%이내"]
