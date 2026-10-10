"""주도주 진입 규칙 백테스트의 규칙·지표. 2026-10-10 고정 — 백테스트 결과를 보기 전에 정한 값이고, 결과를 보고 바꾸지 않는다(바꾸면 새 계열로 센다).
하위 시스템끼리 import 하지 않는 관례에 따라 필요한 정의는 복사했다(출처: sepa 추세템플릿 요지, technical_signals 손절·RSI·피벗 정의, accumulation 백테스트 구조).

추세 통과(TREND, 세파 추세템플릿 요지): 종가>150·200일선, 150일선>200일선, 50일선>150일선, 종가>50일선, 200일선이 20일 전보다 높음,
  종가 >= 52주 저가 x 1.3, 종가 >= 52주 고가 x 0.75, 12개월 수익률 순위(그날 유니버스 안 백분위) >= 70.
손절폭(v1 정의): min(40일 저점 - 0.5 ATR14, 종가 - 1.75 ATR14)까지의 거리(종가 대비 %). 추격: RSI14 >= 75 또는 60일 피벗(전일까지 고가) 대비 +5% 이상.

그룹(모두 신호일 다음 거래일 종가 진입, 같은 종목은 직전 10거래일 안에 신호가 있으면 다시 세지 않음):
  ALL      그날 지표를 계산할 수 있는 전 종목(기준선)
  TREND    추세 통과
  V1       TREND & 손절폭 8% 이하 & 추격 아님        (지금 '오늘의 추천'의 진입 조건)
  LEADER   TREND & RS >= 90                            (과열·손절폭 무시, 추격 허용)
  PULLBACK TREND & RS >= 80 & 저가가 20일 지수이평 x 1.01 이하로 닿았다가 종가가 20일 이평 위 & RSI14 < 65
  BREAKOUT TREND & 종가 > 60일 피벗 & 거래량 >= 50일 평균(전일까지) x 1.4 & 피벗 대비 +5% 이내
청산: HOLD = 20·40거래일 뒤 종가 / STOP8 = 종가가 진입가 -8% 이하가 되는 첫 종가 또는 20·40거래일 뒤 종가. 편도 비용 한국 25bp·미국 10bp."""
from __future__ import annotations

import numpy as np
import pandas as pd

CFG = {"rs_trend": 70, "rs_leader": 90, "rs_pullback": 80, "risk_max": 8.0, "rsi_chase": 75.0, "pivot_chase": 5.0,
       "swing": 40, "atr": 14, "atr_stop": 1.75, "atr_buffer": 0.5, "pivot": 60, "ema": 20, "touch": 1.01, "rsi_pullback": 65.0,
       "vol_break": 1.4, "stop_exit": 0.08, "cooldown": 10, "min_low_ratio": 1.3, "min_high_ratio": 0.75}
HORIZONS = (20, 40)
GROUPS = ("ALL", "TREND", "V1", "LEADER", "PULLBACK", "BREAKOUT")


def _rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + g / l.replace(0, np.nan))
    return out.where(l != 0, 100.0)


def features(df: pd.DataFrame, cfg: dict = CFG) -> pd.DataFrame:
    """df: 날짜 오름차순, 열 open/high/low/close/volume. 미래 값을 쓰지 않는다."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    sma50, sma150, sma200 = (c.rolling(n).mean() for n in (50, 150, 200))
    hi, lo = h.rolling(252, min_periods=200).max(), l.rolling(252, min_periods=200).min()
    ema = c.ewm(span=cfg["ema"], adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / cfg["atr"], adjust=False, min_periods=cfg["atr"]).mean()
    structural = l.rolling(cfg["swing"], min_periods=1).min() - cfg["atr_buffer"] * atr
    atr_stop = c - cfg["atr_stop"] * atr
    stop = np.minimum(structural, atr_stop)
    risk = ((c - stop) / c * 100).where((structural > 0) & (atr_stop > 0))
    rsi = _rsi(c, 14)
    pivot = h.rolling(cfg["pivot"], min_periods=cfg["pivot"]).max().shift(1)
    out = pd.DataFrame(index=df.index)
    out["trend"] = ((c > sma150) & (c > sma200) & (sma150 > sma200) & (sma50 > sma150) & (c > sma50) & (sma200 > sma200.shift(20))
                    & (c >= cfg["min_low_ratio"] * lo) & (c >= cfg["min_high_ratio"] * hi)).fillna(False)
    out["ret252"] = c / c.shift(252) - 1
    out["risk"] = risk
    out["rsi"] = rsi
    out["pivDist"] = (c / pivot - 1) * 100
    out["volRatio"] = v / v.rolling(50).mean().shift(1)
    out["pullback"] = ((l <= ema * cfg["touch"]) & (c > ema) & (rsi < cfg["rsi_pullback"])).fillna(False)
    out["ok"] = sma200.notna() & hi.notna() & lo.notna() & atr.notna() & rsi.notna() & pivot.notna() & risk.notna() & (c > 0)
    return out


def forward_returns(close: pd.Series, h: int, cost: float, stop: float | None = None) -> pd.Series:
    """신호일 t → 진입 t+1 종가 → 청산(t+1+h 종가, stop이 있으면 진입가 대비 -stop 이하가 되는 첫 종가). 비용을 뺀다. 끝 h+1일은 NaN."""
    c = close.to_numpy(float)
    out = np.full(len(c), np.nan)
    if len(c) < h + 2:
        return pd.Series(out, index=close.index)
    w = np.lib.stride_tricks.sliding_window_view(c[1:], h + 1)          # w[t] = c[t+1 .. t+1+h]
    entry, rest = w[:, 0], w[:, 1:]
    if stop is None:
        exit_ = rest[:, -1]
    else:
        hit = rest <= entry[:, None] * (1 - stop)
        first = hit.argmax(axis=1)
        exit_ = np.where(hit.any(axis=1), rest[np.arange(len(rest)), first], rest[:, -1])
    out[:len(w)] = exit_ / entry - 1 - cost
    return pd.Series(out, index=close.index)


def select(panel: pd.DataFrame, cfg: dict = CFG) -> dict[str, pd.Series]:
    """panel: 종목×날짜 행(code, date, features, rs). 그룹별 불리언. ALL 말고는 같은 종목 직전 cooldown거래일 안의 재신호를 뺀다."""
    ok = panel["ok"]
    trend = ok & panel["trend"] & (panel["rs"] >= cfg["rs_trend"])
    chase = (panel["rsi"] >= cfg["rsi_chase"]) | (panel["pivDist"] >= cfg["pivot_chase"])
    masks = {"ALL": ok, "TREND": trend,
             "V1": trend & (panel["risk"] <= cfg["risk_max"]) & (panel["risk"] > 0) & ~chase,
             "LEADER": trend & (panel["rs"] >= cfg["rs_leader"]),
             "PULLBACK": trend & (panel["rs"] >= cfg["rs_pullback"]) & panel["pullback"],
             "BREAKOUT": trend & (panel["pivDist"] > 0) & (panel["pivDist"] <= cfg["pivot_chase"]) & (panel["volRatio"] >= cfg["vol_break"])}
    out = {}
    for g, m in masks.items():
        out[g] = m if g == "ALL" else first_of_cluster(panel, m, cfg["cooldown"])
    return out


def first_of_cluster(panel: pd.DataFrame, mask: pd.Series, cooldown: int) -> pd.Series:
    sig = panel.assign(m=mask.values).sort_values(["code", "date"])
    prior = sig.groupby("code")["m"].transform(lambda s: s.astype(float).shift().rolling(cooldown, min_periods=1).max().fillna(0).astype(bool))
    return (sig["m"] & ~prior).reindex(panel.index).fillna(False)
