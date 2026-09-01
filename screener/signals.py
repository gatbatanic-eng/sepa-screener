"""신호 판정 — BUY / WATCH / SELL / NEUTRAL (STRATEGY.md 4절).

입력: factors.compute_factor_scores() 결과 DataFrame
      (composite_score, value_score, momentum_score, quality_score,
       price, sma200, ret_6m, roe, debt_to_equity, net_income_positive 필요)
선택: holdings DataFrame (symbol, buy_price, peak_price) — 트레일링 스탑 판정용

평가 순서: ① 트레일링 스탑 → ② BUY → ③ SELL → ④ WATCH → ⑤ NEUTRAL
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 임계값 (STRATEGY.md 4절과 1:1 대응 — 수정 시 STRATEGY.md 도 함께)
BUY_COMPOSITE_MIN = 80.0
BUY_RET_6M_MIN = 0.03
BUY_ROE_MIN = 8.0
BUY_DEBT_MAX = 200.0

WATCH_COMPOSITE_MIN = 60.0
WATCH_NEAR_SMA_BAND = 0.05
WATCH_VALUE_MIN = 80.0
WATCH_MOMENTUM_MAX = 40.0

SELL_COMPOSITE_MAX = 40.0
TRAILING_STOP_DRAWDOWN = -0.15


def _buy_check(r: pd.Series) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    ok = True

    if r["composite_score"] >= BUY_COMPOSITE_MIN:
        reasons.append(f"Composite {r['composite_score']:.0f}≥80")
    else:
        ok = False

    if _gt(r["price"], r["sma200"]):
        reasons.append("현재가>200MA")
    else:
        ok = False

    if _ge(r["ret_6m"], BUY_RET_6M_MIN):
        reasons.append(f"6M모멘텀 {_pct(r['ret_6m'])}≥+3%")
    else:
        ok = False

    if _ge(r["roe"], BUY_ROE_MIN) and bool(r["net_income_positive"]):
        reasons.append(f"ROE {r['roe']:.1f}%≥8% & 흑자")
    else:
        ok = False

    d2e = r["debt_to_equity"]
    if pd.isna(d2e):
        reasons.append("부채비율 데이터없음(통과 간주)")
    elif d2e <= BUY_DEBT_MAX:
        reasons.append(f"부채비율 {d2e:.0f}%≤200%")
    else:
        ok = False

    return ok, reasons


def _watch_check(r: pd.Series) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    cs = r["composite_score"]

    if WATCH_COMPOSITE_MIN <= cs < BUY_COMPOSITE_MIN:
        reasons.append(f"Composite {cs:.0f} (60~80)")

    if cs >= BUY_COMPOSITE_MIN:
        p2s = r.get("price_to_sma200")
        if p2s is None or pd.isna(p2s):
            if _gt(r["sma200"], 0):
                p2s = r["price"] / r["sma200"] - 1.0
        if p2s is not None and not pd.isna(p2s) and abs(p2s) <= WATCH_NEAR_SMA_BAND:
            reasons.append("고득점이나 200MA 근처, 추세확인 대기")
        if not _ge(r["ret_6m"], BUY_RET_6M_MIN):
            reasons.append(f"고득점이나 6M모멘텀 {_pct(r['ret_6m'])}<+3%")

    if _ge(r["value_score"], WATCH_VALUE_MIN) and r["momentum_score"] < WATCH_MOMENTUM_MAX:
        reasons.append(
            f"저평가(Value {r['value_score']:.0f}) 소외주(Momentum {r['momentum_score']:.0f})"
        )

    return (len(reasons) > 0), reasons


def _sell_check(r: pd.Series) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if r["composite_score"] < SELL_COMPOSITE_MAX:
        reasons.append(f"Composite {r['composite_score']:.0f}<40")

    if _lt(r["price"], r["sma200"]) and _lt(r["ret_6m"], 0.0):
        reasons.append(f"200MA 이탈 & 6M모멘텀 {_pct(r['ret_6m'])}<0")

    return (len(reasons) > 0), reasons


def classify(
    factor_df: pd.DataFrame,
    holdings: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """factor_df 에 signal, reason 컬럼을 추가해 반환한다."""
    df = factor_df.copy()

    hold_map: dict[str, dict] = {}
    if holdings is not None and not holdings.empty:
        h = holdings.copy()
        h["symbol"] = h["symbol"].astype(str)
        for _, hr in h.iterrows():
            hold_map[hr["symbol"]] = hr.to_dict()

    signals: list[str] = []
    reasons: list[str] = []

    for _, r in df.iterrows():
        sym = str(r["symbol"])
        held = hold_map.get(sym)

        # ① 트레일링 스탑 (스코어 무관, 최우선)
        if held is not None:
            peak = _num(held.get("peak_price"))
            buyp = _num(held.get("buy_price"))
            ref = peak if peak and not np.isnan(peak) else buyp
            if ref and not np.isnan(ref) and _num(r["price"]) is not None:
                dd = r["price"] / ref - 1.0
                if dd <= TRAILING_STOP_DRAWDOWN:
                    signals.append("SELL")
                    reasons.append(
                        f"트레일링 스탑: 고점 대비 {_pct(dd)} 하락 (기준가 {ref:,.0f})"
                    )
                    continue

        buy_ok, buy_reasons = _buy_check(r)
        if buy_ok:
            signals.append("BUY")
            reasons.append("; ".join(buy_reasons))
            continue

        sell_ok, sell_reasons = _sell_check(r)
        if sell_ok:
            signals.append("SELL")
            reasons.append("; ".join(sell_reasons))
            continue

        watch_ok, watch_reasons = _watch_check(r)
        if watch_ok:
            signals.append("WATCH")
            reasons.append("; ".join(watch_reasons))
            continue

        signals.append("NEUTRAL")
        # BUY 에서 걸린 항목을 알려주면 유용
        fail = _buy_fail_summary(r)
        reasons.append(fail or "판정 기준 해당 없음")

    df["signal"] = signals
    df["reason"] = reasons
    return df


def _buy_fail_summary(r: pd.Series) -> str:
    bits = []
    if r["composite_score"] < BUY_COMPOSITE_MIN:
        bits.append(f"Composite {r['composite_score']:.0f}<80")
    if not _gt(r["price"], r["sma200"]):
        bits.append("200MA 미돌파")
    if not _ge(r["ret_6m"], BUY_RET_6M_MIN):
        bits.append(f"6M {_pct(r['ret_6m'])}<+3%")
    if not (_ge(r["roe"], BUY_ROE_MIN) and bool(r["net_income_positive"])):
        bits.append("ROE<8% 또는 적자")
    d2e = r["debt_to_equity"]
    if not pd.isna(d2e) and d2e > BUY_DEBT_MAX:
        bits.append(f"부채비율 {d2e:.0f}%>200%")
    return "BUY 불충족: " + ", ".join(bits) if bits else ""


# --- 결측 안전 비교 헬퍼 ---
def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _gt(a, b) -> bool:
    a, b = _num(a), _num(b)
    return a is not None and b is not None and not np.isnan(a) and not np.isnan(b) and a > b


def _lt(a, b) -> bool:
    a, b = _num(a), _num(b)
    return a is not None and b is not None and not np.isnan(a) and not np.isnan(b) and a < b


def _ge(a, b) -> bool:
    a, b = _num(a), _num(b)
    return a is not None and b is not None and not np.isnan(a) and not np.isnan(b) and a >= b


def _pct(v) -> str:
    f = _num(v)
    if f is None or np.isnan(f):
        return "N/A"
    return f"{f * 100:+.1f}%"
