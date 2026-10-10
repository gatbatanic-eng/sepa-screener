"""점수 → 진입 적합성 → 최대 3개. 적합한 종목이 모자라면 모자란 대로 내고 탈락 사유를 센다."""
from __future__ import annotations

from collections import Counter

from . import config as C
from .pool import CORE, RESEARCH


def score(strategies: dict) -> int:
    return sum(C.CORE_POINTS if s in CORE else C.RESEARCH_POINTS for s in strategies)


def entry_view(code: str, pool: dict, direct: dict | None = None) -> dict:
    """한 종목의 진입 자료를 한곳에 모은다. 출처 우선순위: SEPA → 계좌복구 → 기술적 → 직접 계산."""
    sepa, aggr, tech = pool["sepaRows"].get(code), pool["aggrRows"].get(code), pool["techRows"].get(code)
    risk, src = None, None
    if sepa and sepa.get("initRisk") is not None:
        risk, src = sepa["initRisk"], "SEPA"
    elif aggr and aggr.get("initialRiskPct") not in (None, 0, 0.0):
        risk, src = aggr["initialRiskPct"], "계좌복구"
    elif tech and tech.get("riskPct") is not None:
        risk, src = tech["riskPct"], "기술적"
    elif direct:
        risk, src = direct["riskPct"], "직접 계산"
    chase = []
    if tech and tech.get("chaseWarning"):
        chase.append("기술적 추격 경고")
    if aggr and aggr.get("notExtended") is False:
        chase.append("계좌복구 과열")
    if sepa and sepa.get("zone") == "EXTENDED":
        chase.append("SEPA 과열 구간")
    if direct and direct.get("chase"):
        chase.append("RSI·피벗 이격 추격")
    liquid = aggr.get("liquidityOk") if aggr else None
    return {"riskPct": risk, "riskSource": src, "verdict": (sepa or {}).get("entryVerdict"), "chase": chase,
            "liquidityOk": liquid, "zone": (sepa or {}).get("zone")}


def reject_reason(view: dict) -> str | None:
    risk = view["riskPct"]
    if risk is None:
        return "손절폭 미확인"
    if risk <= 0:
        return "손절폭 0 이하(계산 불가)"
    if risk > C.MAX_RISK_PCT:
        return f"손절폭 {C.MAX_RISK_PCT:g}% 초과"
    if view["verdict"] == "NO-GO":
        return "SEPA 진입 NO-GO"
    if view["chase"]:
        return "추격(과열)"
    if view["liquidityOk"] is False:
        return "유동성 부족"
    return None


def needs_direct(code: str, pool: dict) -> bool:
    """풀 안의 종목 중 어느 탭에서도 손절폭을 못 얻은 경우만 직접 계산한다."""
    return entry_view(code, pool)["riskPct"] is None


def pick(market: str, pool: dict, direct: dict[str, dict] | None = None) -> dict:
    direct = direct or {}
    rows = []
    for code, strategies in pool["selected"].items():
        # 후보 풀 = 핵심 전략(SEPA·실적 턴어라운드)이 고른 종목. 연구 전략만 고른 종목은 점수에만 반영한다.
        if not (set(strategies) & set(CORE)):
            continue
        view = entry_view(code, pool, direct.get(code))
        f = pool["funnel"].get(code, {})
        sepa = pool["sepaRows"].get(code, {})
        tech = pool["techRows"].get(code, {})
        rows.append({"code": code, "name": f.get("name") or sepa.get("name") or tech.get("name") or code,
                     "market": f.get("exchange") or sepa.get("market") or tech.get("market"),
                     "price": f.get("price") or sepa.get("close") or tech.get("close"),
                     "score": score(strategies), "strategies": {k: strategies[k] for k in strategies},
                     "funnelRank": f.get("rank"), "entry": view, "reject": reject_reason(view)})
    rows.sort(key=lambda r: (-r["score"], r["funnelRank"] or 999, r["entry"]["riskPct"] if r["entry"]["riskPct"] else 999, r["code"]))
    ok = [r for r in rows if not r["reject"]]
    picks = ok[:C.MAX_PICKS]
    rejects = Counter(r["reject"] for r in rows if r["reject"])
    return {"allRows": rows, "market": market, "regime": pool.get("regime"), "gate": pool.get("gate"), "candidates": len(rows), "suitable": len(ok),
            "picks": picks, "rejectSummary": dict(rejects.most_common()),
            "shortfall": None if len(picks) >= C.MAX_PICKS else
            f"진입 적합 종목이 {len(picks)}개뿐입니다(후보 {len(rows)}개 중). 억지로 채우지 않았습니다."}
