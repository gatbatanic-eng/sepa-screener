"""깔때기 판정 규칙 — 네트워크 없는 순수 로직.

입력은 시장 무관 표준 레코드다::

    {
      "market": "kr" | "us", "symbol": str, "name": str, "marcap": float,
      "quarters": [{"year", "quarter", "revenue", "operatingProfit", "netIncome",
                    "liabilities", "equity"}, ...],   # 오름차순, 3개월 단위
      "sharesNow": float | None, "sharesYearAgo": float | None,
      "ocfToNi": float | None, "ocfTTMPositive": bool | None,
      "dilutionEvents12m": int | None, "splitEvents12m": int | None,
      "prices": {"price", "high52Ratio", "ret6m", "ret36to6m", "aboveMa40"},
    }

결측은 ``None`` 으로 남기고 0으로 바꾸지 않는다. 점수는 가용 항목만으로 정규화한다.
"""
from __future__ import annotations

import math
from typing import Any

WEIGHTS = {"S1": 3, "S2": 3, "S6": 3, "S4": 2}

G1_RUNUP = 1.5            # 6개월 +150% 이상
G2_SHARE_GROWTH = 0.10    # 주식 수 전년 대비 +10% 초과
G2_EVENTS = 2             # 12개월 내 유상증자·CB·BW 결정 2건 이상
G3_DEBT_TO_EQUITY = 2.0   # 부채비율 200% 초과
ACCEL_STEP = 0.01         # 가속 인정: 매출 YoY가 직전 분기보다 1%p 이상 높을 때

P1_CUTOFF = 3.0           # 5년 가치 ÷ 현재 시총 3배 미만이면 텐배거 후보 제외
P1_TENBAGGER = 10.0


def finite(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _index(quarters: list[dict]) -> dict[tuple[int, int], dict]:
    return {(int(q["year"]), int(q["quarter"])): q for q in quarters}


def _prev_year(key: tuple[int, int]) -> tuple[int, int]:
    return key[0] - 1, key[1]


def _back(key: tuple[int, int], n: int) -> tuple[int, int]:
    idx = key[0] * 4 + (key[1] - 1) - n
    return idx // 4, idx % 4 + 1


def _ttm(idx: dict, end: tuple[int, int], field: str) -> float | None:
    vals = [finite(idx.get(_back(end, i), {}).get(field)) for i in range(4)]
    return None if any(v is None for v in vals) else sum(vals)


def _yoy(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return current / previous - 1


def quarter_metrics(quarters: list[dict]) -> dict[str, Any]:
    """분기 실적에서 성장·이익·재무 지표를 뽑는다."""
    out: dict[str, Any] = {
        "latestPeriod": None, "revYoY": [], "opLatest": None, "opYearAgo": None,
        "opTTM": None, "opTTMPrev": None, "revTTM": None, "revTTMPrev": None,
        "niTTM": None, "opmTTM": None, "opmTTMPrev": None, "debtToEquity": None,
        "equity": None,
    }
    rows = [q for q in quarters if finite(q.get("revenue")) is not None]
    if not rows:
        return out
    idx = _index(quarters)
    last = max(_index(rows))
    out["latestPeriod"] = f"{last[0]}Q{last[1]}"
    for i in range(3):
        key = _back(last, i)
        out["revYoY"].append(_yoy(finite(idx.get(key, {}).get("revenue")),
                                  finite(idx.get(_prev_year(key), {}).get("revenue"))))
    out["opLatest"] = finite(idx[last].get("operatingProfit"))
    out["opYearAgo"] = finite(idx.get(_prev_year(last), {}).get("operatingProfit"))
    prev_end = _prev_year(last)
    for name, field in (("op", "operatingProfit"), ("rev", "revenue"), ("ni", "netIncome")):
        out[f"{name}TTM"] = _ttm(idx, last, field)
        if name != "ni":
            out[f"{name}TTMPrev"] = _ttm(idx, prev_end, field)
    for suffix in ("", "Prev"):
        rev, op = out[f"revTTM{suffix}"], out[f"opTTM{suffix}"]
        out[f"opmTTM{suffix}"] = op / rev if rev and rev > 0 and op is not None else None
    # 재무상태는 자본 값이 있는 가장 최근 분기 기준
    for q in sorted(quarters, key=lambda r: (r["year"], r["quarter"]), reverse=True):
        eq, li = finite(q.get("equity")), finite(q.get("liabilities"))
        if eq is not None:
            out["equity"] = eq
            out["debtToEquity"] = li / eq if eq > 0 and li is not None else None
            break
    return out


def share_growth(stock: dict) -> float | None:
    now, ago = finite(stock.get("sharesNow")), finite(stock.get("sharesYearAgo"))
    if now is None or ago is None or ago <= 0:
        return None
    return now / ago - 1


def evaluate_gates(stock: dict, m: dict) -> dict[str, Any]:
    """① 관문. G1~G3는 제외, G4는 '관찰만' 표시."""
    hits: list[dict] = []
    prices = stock.get("prices") or {}
    ret6m = finite(prices.get("ret6m"))
    op = m["opTTM"] if m["opTTM"] is not None else m["opLatest"]
    if op is not None and op <= 0 and ret6m is not None and ret6m >= G1_RUNUP:
        hits.append({"code": "G1", "reason": f"영업적자인데 6개월 {ret6m * 100:.0f}% 급등"})
    sg = share_growth(stock)
    events = stock.get("dilutionEvents12m")
    if sg is not None and sg > G2_SHARE_GROWTH:
        hits.append({"code": "G2", "reason": f"주식 수 전년 대비 +{sg * 100:.1f}%"})
    elif events is not None and events >= G2_EVENTS:
        hits.append({"code": "G2", "reason": f"12개월 내 증자·CB·BW 결정 {events}건"})
    if m["equity"] is not None and m["equity"] <= 0:
        hits.append({"code": "G3", "reason": "자본잠식"})
    elif m["debtToEquity"] is not None and m["debtToEquity"] > G3_DEBT_TO_EQUITY:
        hits.append({"code": "G3", "reason": f"부채비율 {m['debtToEquity'] * 100:.0f}% (금융업이면 수동 확인)"})
    flags = []
    if (stock.get("splitEvents12m") or 0) >= 1:
        flags.append({"code": "G4", "reason": "12개월 내 분할 결정 공시 — 물적분할 여부 확인"})
    return {"excluded": bool(hits), "hits": hits, "flags": flags}


def score_s1(m: dict) -> tuple[float | None, list[str], bool]:
    """S1 이익 가속. 반환: (점수, 사유, T1용 이익전환 여부)."""
    g = m["revYoY"]
    if not g or g[0] is None:
        return None, [], False
    pts, why = 0.0, []
    g0 = g[0]
    if g0 >= 0.50:
        pts += 35
    elif g0 >= 0.25:
        pts += 25
    elif g0 >= 0.10:
        pts += 10
    why.append(f"매출 YoY {g0 * 100:.0f}%")
    step = ACCEL_STEP
    accel2 = len(g) >= 3 and None not in g[:3] and g[0] > g[1] + step and g[1] > g[2] + step
    accel1 = len(g) >= 2 and g[1] is not None and g[0] > g[1] + step
    if accel2:
        pts += 30
        why.append("2분기 연속 가속")
    elif accel1:
        pts += 15
        why.append("1분기 가속")
    turnaround = (m["opLatest"] is not None and m["opLatest"] > 0
                  and m["opYearAgo"] is not None and m["opYearAgo"] <= 0)
    op_g = _yoy(m["opTTM"], m["opTTMPrev"])
    rev_g = _yoy(m["revTTM"], m["revTTMPrev"])
    if turnaround:
        pts += 35
        why.append("영업 흑자전환")
    elif op_g is not None and rev_g is not None and op_g > rev_g + 0.10:
        pts += 25
        why.append(f"영업레버리지(TTM 영업이익 {op_g * 100:.0f}%)")
    elif op_g is not None and op_g > 0:
        pts += 10
    inflection = (accel2 or turnaround) and g0 >= 0.10
    return min(pts, 100.0), why, inflection


def score_s2(stock: dict, m: dict) -> tuple[float | None, list[str]]:
    """S2 이익의 질: TTM 영업이익률 추세 + 영업현금흐름 전환."""
    got, cap, why = 0.0, 0.0, []
    if m["opmTTM"] is not None and m["opmTTMPrev"] is not None:
        cap += 50
        delta = (m["opmTTM"] - m["opmTTMPrev"]) * 100
        got += 50 if delta >= 5 else 35 if delta >= 2 else 20 if delta > 0 else 0
        why.append(f"영업이익률 {delta:+.1f}%p")
    ratio = finite(stock.get("ocfToNi"))
    ocf_pos = stock.get("ocfTTMPositive")
    if m["niTTM"] is not None and m["niTTM"] <= 0 and ocf_pos is not None:
        cap += 50
        got += 25 if ocf_pos else 0
        why.append("순손실·영업현금 플러스" if ocf_pos else "순손실·영업현금 마이너스")
    elif ratio is not None:
        cap += 50
        got += 50 if ratio >= 1.0 else 35 if ratio >= 0.7 else 15 if ratio >= 0.4 else 0
        why.append(f"영업현금/순이익 {ratio:.2f}")
    return (got / cap * 100 if cap else None), why


def score_s6(stock: dict) -> tuple[float | None, list[str]]:
    """S6 자본 배분: 주식 수 증가율 + 희석성 자금조달 공시."""
    got, cap, why = 0.0, 0.0, []
    sg = share_growth(stock)
    if sg is not None:
        cap += 60
        got += 60 if sg <= 0 else 50 if sg <= 0.02 else 30 if sg <= 0.05 else 10 if sg <= 0.10 else 0
        why.append(f"주식 수 {sg * 100:+.1f}%")
    events = stock.get("dilutionEvents12m")
    if events is not None:
        cap += 40
        got += 40 if events == 0 else 20 if events == 1 else 0
        why.append(f"증자·CB 공시 {events}건")
    return (got / cap * 100 if cap else None), why


def percentile_ranks(values: dict[str, float | None]) -> dict[str, float | None]:
    """값이 작을수록 0에 가까운 백분위(0~1). 결측은 None."""
    valid = sorted((v, k) for k, v in values.items() if v is not None)
    n = len(valid)
    out: dict[str, float | None] = {k: None for k in values}
    for i, (_, k) in enumerate(valid):
        out[k] = i / (n - 1) if n > 1 else 0.5
    return out


def score_s4(pct_past: float | None) -> float | None:
    """S4 인식 지연: 과거(36→6개월 전) 수익률이 유니버스 하위일수록 높다."""
    return None if pct_past is None else round((1 - pct_past) * 100, 2)


def timing_t1(stock: dict, inflection: bool) -> str:
    p = stock.get("prices") or {}
    ratio, above = finite(p.get("high52Ratio")), p.get("aboveMa40")
    if ratio is None or above is None:
        return "N/A"
    if inflection and ratio >= 0.90 and above:
        return "ON"
    if ratio >= 0.75 and above:
        return "NEAR"
    return "OFF"


def composite(scores: dict[str, float | None]) -> float | None:
    if scores.get("S1") is None:
        return None
    total = sum(WEIGHTS[k] * v for k, v in scores.items() if v is not None)
    weight = sum(WEIGHTS[k] for k, v in scores.items() if v is not None)
    return round(total / weight, 2) if weight else None


def evaluate(stock: dict, pct_past: float | None) -> dict[str, Any]:
    m = quarter_metrics(stock.get("quarters") or [])
    gates = evaluate_gates(stock, m)
    s1, why1, inflection = score_s1(m)
    s2, why2 = score_s2(stock, m)
    s6, why6 = score_s6(stock)
    s4 = score_s4(pct_past)
    scores = {"S1": s1, "S2": s2, "S6": s6, "S4": s4}
    return {
        "metrics": {k: m[k] for k in ("latestPeriod", "revYoY", "opTTM", "opmTTM", "opmTTMPrev", "debtToEquity")},
        "shareGrowth": share_growth(stock),
        "gates": gates,
        "scores": {k: (round(v, 2) if v is not None else None) for k, v in scores.items()},
        "reasons": {"S1": why1, "S2": why2, "S6": why6},
        "composite": composite(scores),
        "T1": timing_t1(stock, inflection),
        "inflection": inflection,
    }


def p1_multiple(tam: float | None, share: float | None, margin: float | None,
                multiple: float | None, marcap: float | None, other: float | None = None) -> dict[str, Any]:
    """③ P1 10배 산수: (목표시장 × 점유율 × 성숙기 이익률 × 적정 배수 + 기타 사업 가치) ÷ 현재 시총.

    tam·other·marcap은 같은 통화·단위여야 한다. share·margin은 비율(0.2 = 20%).
    """
    vals = [finite(v) for v in (tam, share, margin, multiple, marcap)]
    if any(v is None for v in vals) or vals[4] <= 0:
        return {"value5y": None, "multipleX": None, "verdict": "입력 부족"}
    value = vals[0] * vals[1] * vals[2] * vals[3] + (finite(other) or 0.0)
    x = value / vals[4]
    verdict = "텐배거 산수 통과" if x >= P1_TENBAGGER else "보류(3~10배)" if x >= P1_CUTOFF else "제외(3배 미만)"
    return {"value5y": value, "multipleX": round(x, 2), "verdict": verdict}
