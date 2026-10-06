"""매집 가능성 지표(가격·거래량만). 수급은 과거 이력이 30일뿐이라 여기에 넣지 않는다.
조건(2026-10-06 고정, 백테스트 결과를 보기 전에 정한 값):
 A 거래량 증가   최근 10일 평균 거래대금 / 60일 평균 거래대금 >= 1.3
 B 좁은 변동폭   20일 고저 범위(종가 대비 %) <= 5 x ATR14(종가 대비 %)
 D 위치          종가 > 150일선·200일선, 종가 <= 50일선 x 1.15, 종가 >= 252일 고가 x 0.75, 50일 상승일/하락일 거래량 >= 1.2
 E 흡수          최근 20일 중 거래량이 60일 평균의 1.5배를 넘은 날들의 평균 종가 위치((종가-저가)/(고가-저가)) >= 0.5 (그런 날이 1일 이상)
점수 = A+B+D+E (0~4)."""
from __future__ import annotations

import numpy as np
import pandas as pd

CFG = {"vol_fast": 10, "vol_slow": 60, "vol_ratio": 1.3, "range_days": 20, "range_atr_mult": 5.0, "atr_days": 14,
       "ext_max": 1.15, "hi_days": 252, "hi_min": 0.75, "udv_days": 50, "udv_min": 1.2,
       "heavy_mult": 1.5, "heavy_days": 20, "clv_min": 0.5}


def compute(df: pd.DataFrame, cfg: dict = CFG) -> pd.DataFrame:
    """df: 날짜 인덱스(오름차순), 열 open/high/low/close/volume. 날짜별 A·B·D·E 불리언과 score를 돌려준다. 미래 값을 쓰지 않는다."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    turnover = c * v
    a_ratio = turnover.rolling(cfg["vol_fast"]).mean() / turnover.rolling(cfg["vol_slow"]).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_pct = tr.rolling(cfg["atr_days"]).mean() / c
    range_pct = (h.rolling(cfg["range_days"]).max() - l.rolling(cfg["range_days"]).min()) / c
    sma50, sma150, sma200 = (c.rolling(n).mean() for n in (50, 150, 200))
    hi = h.rolling(cfg["hi_days"], min_periods=200).max()
    up = v.where(c > c.shift(), 0.0).rolling(cfg["udv_days"]).sum()
    dn = v.where(c < c.shift(), 0.0).rolling(cfg["udv_days"]).sum()
    udv = up / dn.replace(0, np.nan)
    rng = (h - l).replace(0, np.nan)
    clv = ((c - l) / rng).fillna(0.5)
    heavy = (v > cfg["heavy_mult"] * v.rolling(cfg["vol_slow"]).mean().shift()).astype(float)
    n_heavy = heavy.rolling(cfg["heavy_days"]).sum()
    clv_heavy = (clv * heavy).rolling(cfg["heavy_days"]).sum() / n_heavy.replace(0, np.nan)
    out = pd.DataFrame(index=df.index)
    out["A"] = a_ratio >= cfg["vol_ratio"]
    out["B"] = range_pct <= cfg["range_atr_mult"] * atr_pct
    out["D"] = (c > sma150) & (c > sma200) & (c <= cfg["ext_max"] * sma50) & (c >= cfg["hi_min"] * hi) & (udv >= cfg["udv_min"])
    out["E"] = (n_heavy >= 1) & (clv_heavy >= cfg["clv_min"])
    out = out.fillna(False).astype(bool)
    out["score"] = out[["A", "B", "D", "E"]].sum(axis=1)
    out["ok"] = sma200.notna() & hi.notna() & (c > 0) & udv.notna() & a_ratio.notna() & atr_pct.notna()   # 지표를 계산할 이력이 충분한 날만
    return out
