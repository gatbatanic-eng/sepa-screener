"""소형 고변동 반등 후보 관찰 그룹 (REB_WATCH) — 연구용, 매수 신호가 아니다.

배경(2026-10 분석): 스크리너가 시작된 2026-08-17 이후 저점 대비 +50% 이상 반등한 한국 134종목은
시가총액이 작고(중앙값 2,386억 vs 6,774억) 변동성·베타가 높고 52주 고점에서 깊게 눌려 있던 종목이었다.
같은 프로파일은 반등 가능성과 하락 위험을 함께 키우는 요인이라 신호로 쓰지 않고, 이 그룹의 이후 성과를
추적기로 쌓아 검증한다(과거 에피소드에서는 설명력이 일관되지 않았다: AUC 0.44~0.71).

- 기준은 아래 CONFIG 로 **고정**한다. 결과를 본 뒤 바꾸지 않고, 바꾸면 새 계열로 센다
  (research_tracker 의 strategySeriesId 는 CONFIG 로만 만들어지므로 구현만 고치면 기록은 이어진다).
- 한국 전용. 시가총액·변동성·낙폭 중 하나라도 계산할 수 없으면 False 가 아니라 None(판정 불가)이다.
- 이 모듈은 네트워크·파일 읽기를 하지 않고, ``export_rebound`` 만 output/ 에 입력 파일을 쓴다.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

CONFIG = {
    "marcapMaxKRW": 300_000_000_000,   # 시가총액 3,000억원 미만
    "vol60Min": 100.0,                 # 60거래일 일수익률 표준편차 × √252 (%) 이상
    "drawdown52wMax": -0.50,           # 52주 고점(일중 고가) 대비 -50% 이하
    "volWindow": 60,
    "highWindow": 252,
    "minHistory": 120,
}


def _marcap_krw(v):
    """KR 시가총액을 원 단위로. 1e9 미만이면 억원 단위로 보고 1e8 을 곱한다(personas.fund_metrics 와 같은 규칙)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x <= 0:
        return None
    return x * 1e8 if x < 1e9 else x


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    g, l = gain.iloc[-1], loss.iloc[-1]
    if not (math.isfinite(g) and math.isfinite(l)):
        return None
    return 100.0 if l == 0 else float(100 - 100 / (1 + g / l))


def evaluate(row: dict, frame: pd.DataFrame | None, market: str) -> dict:
    """종목 하나의 REB_WATCH 판정 + 이후 분석용 속성. 계산 불가면 reboundWatch=None."""
    out = {k: row.get(k) for k in ("code", "name", "market", "close", "priceAsOf")}
    out.update(status="UNKNOWN", inUniverse=row.get("inUniverse"), reboundWatch=None, reason="판정 불가")
    try:
        if str(market).lower() != "kr":
            raise ValueError("한국 전용")
        if frame is None:
            raise ValueError("종목 OHLCV 누락")
        if row.get("status") != "OK" or not isinstance(row.get("inUniverse"), bool):
            raise ValueError("원본 데이터 확인불가")
        df = frame.sort_index()
        df = df[~df.index.duplicated(keep="last")].dropna(subset=["Close", "High", "Low"])
        if len(df) < CONFIG["minHistory"]:
            raise ValueError("이력 부족")
        close, high, low = df["Close"].astype(float), df["High"].astype(float), df["Low"].astype(float)
        vol = df["Volume"].astype(float) if "Volume" in df else pd.Series(np.nan, index=df.index)
        cap = _marcap_krw(row.get("marcap"))
        if cap is None:
            raise ValueError("시가총액 없음")

        rets = close.pct_change().tail(CONFIG["volWindow"])
        vol60 = float(rets.std() * math.sqrt(252) * 100)
        high52 = float(high.tail(CONFIG["highWindow"]).max())
        last = float(close.iloc[-1])
        dd52 = last / high52 - 1.0
        if not (math.isfinite(vol60) and math.isfinite(dd52) and high52 > 0):
            raise ValueError("변동성/낙폭 계산 불가")

        # 기본 유니버스 밖(AI 보조 종목 등)은 추적기 규칙상 신호 대상이 아니므로 관찰 그룹에서도 제외한다.
        watch = bool(row["inUniverse"] and cap < CONFIG["marcapMaxKRW"] and vol60 >= CONFIG["vol60Min"]
                     and dd52 <= CONFIG["drawdown52wMax"])
        low25 = float(low.tail(25).min())
        avg_vol20_prev = vol.shift(1).rolling(20).mean().iloc[-1]
        sma20 = float(close.tail(20).mean())
        value20 = float((close * vol).tail(20).median()) if vol.notna().any() else float("nan")
        out.update(
            status="OK", priceAsOf=str(df.index[-1])[:10], close=last, reboundWatch=watch,
            marcapEok=cap / 1e8, vol60=vol60, dd52Pct=dd52 * 100,
            offLow25Pct=(last / low25 - 1) * 100 if low25 > 0 else None,
            sma20Pct=(last / sma20 - 1) * 100 if sma20 > 0 else None,
            volumeRatio=float(vol.iloc[-1] / avg_vol20_prev) if avg_vol20_prev and math.isfinite(avg_vol20_prev) and avg_vol20_prev > 0 else None,
            rsi14=_rsi(close),
            ret20Pct=(last / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else None,
            ret60Pct=(last / float(close.iloc[-61]) - 1) * 100 if len(close) > 61 else None,
            value20Eok=value20 / 1e8 if math.isfinite(value20) else None,
            reason="소형·고변동·깊은 낙폭 프로파일" if watch else "프로파일 미충족",
        )
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as exc:
        out["reason"] = str(exc)
    return out


def export_rebound(payload: dict, frames: dict[str, pd.DataFrame], out_dir: Path | None = None) -> None:
    """research_tracker.export_inputs 가 호출한다. 한국 실행에서만 입력 파일을 만든다."""
    market = payload["market"]
    if str(market).lower() != "kr":
        return
    from research_tracker import digest, strategy_series_id, write_json

    rows = [evaluate(row, frames.get(row["code"]), market) for row in payload["rows"]]
    strategy = {
        "name": "소형 고변동 반등 후보(관찰)",
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
        groups=["REB_WATCH"],
        horizons=[5, 20, 60],
    )
    write_json((out_dir or Path(__file__).parent / "output") / f"research_input_rebound_{market}.json", result)
