"""관찰 지표 7번 '섹터 동반 급등' — AI 인프라 바스켓의 가격·자본시장 과열도.

2000년 광통신 버블 말기의 세 가지 특징을 수치로 본다.
A 급등 폭: 바스켓 중 6개월 +100% 이상 비율
B 변두리 추월: 6개월 수익률 중앙값, 변두리(fringe) − 대장(core)
C 저질 주도: 6개월 수익률 중앙값, TTM 영업적자 − 흑자
D 주식 공급: 주식 수 증가율 중앙값, 또는 12개월 내 증자·CB 공시 종목 비율(한국)
경계 세부 3개 이상 → r, 1~2개 → y, 0개 → g. 결측 세부는 판정에서 빠진다.
"""
from __future__ import annotations

import csv
from pathlib import Path
from statistics import median

A_SHARE = 0.25
B_GAP = 0.20
C_GAP = 0.20
D_SHARE_GROWTH = 0.05
D_EVENT_SHARE = 0.20


def load_basket(path: Path, market: str) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {r["symbol"]: r for r in csv.DictReader(f) if r["market"] == market}


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def _gap(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def compute(members: list[dict]) -> dict:
    """members: [{symbol, tier, ret6m, opTTM, shareGrowth, dilutionEvents12m}]"""
    rets = [m for m in members if m.get("ret6m") is not None]
    core = [m["ret6m"] for m in rets if m["tier"] == "core"]
    fringe = [m["ret6m"] for m in rets if m["tier"] == "fringe"]
    loss = [m["ret6m"] for m in rets if m.get("opTTM") is not None and m["opTTM"] <= 0]
    profit = [m["ret6m"] for m in rets if m.get("opTTM") is not None and m["opTTM"] > 0]
    growth = [m["shareGrowth"] for m in members if m.get("shareGrowth") is not None]
    events = [m["dilutionEvents12m"] for m in members if m.get("dilutionEvents12m") is not None]

    a = sum(1 for m in rets if m["ret6m"] >= 1.0) / len(rets) if rets else None
    b = _gap(_median(fringe), _median(core))
    c = _gap(_median(loss), _median(profit))
    d_growth = _median(growth)
    d_events = sum(1 for e in events if e > 0) / len(events) if events else None

    flags = {
        "A": None if a is None else a >= A_SHARE,
        "B": None if b is None else b >= B_GAP,
        "C": None if c is None else c >= C_GAP,
        "D": None if d_growth is None and d_events is None else
             (d_growth is not None and d_growth >= D_SHARE_GROWTH) or (d_events is not None and d_events >= D_EVENT_SHARE),
    }
    hits = sum(1 for v in flags.values() if v)
    level = "" if all(v is None for v in flags.values()) else "r" if hits >= 3 else "y" if hits >= 1 else "g"
    top = sorted(rets, key=lambda m: -m["ret6m"])[:10]
    return {
        "level": level, "hits": hits, "flags": flags,
        "A_share100": a, "B_fringeMinusCore": b, "C_lossMinusProfit": c,
        "D_shareGrowthMedian": d_growth, "D_dilutionEventShare": d_events,
        "n": {"members": len(members), "withPrice": len(rets), "core": len(core), "fringe": len(fringe),
              "loss": len(loss), "profit": len(profit)},
        "topMovers": [{"symbol": m["symbol"], "tier": m["tier"], "ret6m": m["ret6m"]} for m in top],
        "thresholds": {"A": A_SHARE, "B": B_GAP, "C": C_GAP, "D_shareGrowth": D_SHARE_GROWTH, "D_eventShare": D_EVENT_SHARE},
    }
