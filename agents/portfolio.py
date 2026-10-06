"""포트폴리오 매니저 + 리스크 심사. 에이전트 계좌의 보유·진입 대기 종목을 모아 '권고 포트폴리오'를 만든다. 주문은 하지 않는다.

1) 배분: 소멸하지 않은 에이전트(대조군 제외)가 권고 비중(정상 1.0·관찰 0.5)에 비례해 자본을 나눠 갖고,
   각 에이전트 자본 ÷ MAX_POSITIONS가 포지션 하나의 금액이다. 같은 종목을 여러 에이전트가 들면 금액이 합산된다(합의 가중).
2) 리스크 심사(순서 고정): 종목 수 한도 → 한 종목 상한 → 시장별 상한 → 업종별 상한(한국만, NHPLUG 업종). 걸러내거나 줄인 이유를 줄마다 남긴다.
미국 섹터·유동성 한도는 데이터(분류, 통화 환산)가 없어 아직 걸지 못한다. 심사 결과에 그렇게 표시한다.
"""
from __future__ import annotations

from . import config


def allocate(agents: dict, capital: float = config.INITIAL_CAPITAL) -> tuple[list[dict], dict]:
    """에이전트 데이터(main이 만든 구조) → (종목별 합산 행, 에이전트별 예산)."""
    live = {aid: a for aid, a in agents.items() if a.get("review", {}).get("capitalWeight")}
    total_w = sum(a["review"]["capitalWeight"] for a in live.values())
    budgets = {aid: capital * a["review"]["capitalWeight"] / total_w for aid, a in live.items()} if total_w else {}
    lines: dict[tuple[str, str], dict] = {}
    for aid, a in live.items():
        slot = budgets[aid] / config.MAX_POSITIONS
        s = a["series"]["live"]
        held = s["open"]
        free = max(0, config.MAX_POSITIONS - len(held))
        pending = sorted((k for k in s["skipped"] if k["reason"] == "NO_ENTRY_BAR_YET"),
                         key=lambda k: (-(k.get("score") or 0), k["id"]))[:free]  # 자리가 남은 만큼만, 점수 높은 순
        for p in held:
            row = lines.setdefault((p["market"], p["code"]), _row(p["market"], p["code"], p.get("name")))
            row["amount"] += slot
            row["agents"].append(aid)
            row["state"] = "HELD"
            row["entries"].append({"agent": aid, "date": p["entryDate"], "price": p["entryPrice"]})
            row["lastPrice"] = p["lastPrice"]
        for k in pending:
            market = "kr" if k["id"].startswith("sepa:kr:") else "us"
            row = lines.setdefault((market, k["code"]), _row(market, k["code"], k.get("name")))
            row["amount"] += slot
            row["agents"].append(aid)
            row["signalDate"] = k["signalDate"]
    out = []
    for row in lines.values():
        if row["entries"]:  # 가장 가까운 손절가(진입가가 높은 쪽)를 보여준다. 에이전트마다 진입가가 다를 수 있다.
            row["stopPrice"] = round(max(e["price"] for e in row["entries"]) * (1 - config.STOP_PCT / 100), 4)
        out.append(row)
    return out, budgets


def _row(market, code, name):
    return {"market": market, "code": code, "name": name, "amount": 0.0, "agents": [], "entries": [], "state": "PENDING"}


def risk_review(rows: list[dict], capital: float = config.INITIAL_CAPITAL, sectors: dict | None = None) -> tuple[list[dict], dict]:
    """한도를 적용해 줄마다 (APPROVED | TRIMMED | REJECTED, 이유)와 최종 금액을 붙인다.
    sectors: {(시장, 코드): 업종}. 있으면 한국 종목에 업종 상한을 건다."""
    rows = sorted((dict(r, agents=list(r["agents"]), reasons=[], target=r["amount"]) for r in rows),
                  key=lambda r: (-r["amount"], -len(r["agents"]), r["market"], r["code"]))
    for i, r in enumerate(rows):
        if i >= config.MAX_NAMES:
            r["target"] = 0.0
            r["reasons"].append(f"종목 수 한도 {config.MAX_NAMES}개 초과")
    cap = capital * config.MAX_SINGLE_WEIGHT
    for r in rows:
        if r["target"] > cap:
            r["reasons"].append(f"한 종목 상한 {config.MAX_SINGLE_WEIGHT:.0%} 적용")
            r["target"] = cap
    for market in ("kr", "us"):
        group = [r for r in rows if r["market"] == market and r["target"] > 0]
        total, limit = sum(r["target"] for r in group), capital * config.MAX_MARKET_WEIGHT
        if total > limit:
            for r in group:
                r["target"] *= limit / total
                r["reasons"].append(f"{market.upper()} 시장 상한 {config.MAX_MARKET_WEIGHT:.0%} 적용")
    sectors = sectors or {}
    for r in rows:
        r["sector"] = sectors.get((r["market"], str(r["code"]).zfill(6) if r["market"] == "kr" else r["code"]))
    sec_cap = capital * config.MAX_SECTOR_WEIGHT
    for name in sorted({r["sector"] for r in rows if r["sector"] and r["target"] > 0}):
        group = [r for r in rows if r["sector"] == name and r["target"] > 0]
        total = sum(r["target"] for r in group)
        if total > sec_cap:
            for r in group:
                r["target"] *= sec_cap / total
                r["reasons"].append(f"업종 '{name}' 상한 {config.MAX_SECTOR_WEIGHT:.0%} 적용")
    for r in rows:
        r["target"] = round(r["target"], 2)
        r["weight"] = round(r["target"] / capital, 4)
        r["verdict"] = "REJECTED" if r["target"] == 0 else "TRIMMED" if r["reasons"] else "APPROVED"
    gross = sum(r["target"] for r in rows)
    summary = {"capital": capital, "gross": round(gross, 2), "grossPct": round(100 * gross / capital, 1),
               "cashPct": round(100 * (1 - gross / capital), 1),
               "kr": round(100 * sum(r["target"] for r in rows if r["market"] == "kr") / capital, 1),
               "us": round(100 * sum(r["target"] for r in rows if r["market"] == "us") / capital, 1),
               "names": sum(r["target"] > 0 for r in rows),
               "notChecked": (["섹터 쏠림(미국)"] if sectors else ["섹터 쏠림"]) + ["유동성(거래대금 대비 비중)"],
               "sectorUnknown": sum(r["market"] == "kr" and not r["sector"] and r["target"] > 0 for r in rows) if sectors else None}
    return rows, summary


def build(agents: dict, capital: float = config.INITIAL_CAPITAL, sectors: dict | None = None) -> dict:
    rows, budgets = allocate(agents, capital)
    reviewed, summary = risk_review(rows, capital, sectors)
    return {"budgets": {k: round(v, 2) for k, v in budgets.items()}, "lines": reviewed, "exposure": summary,
            "warnings": [] if budgets else ["소멸하지 않은 에이전트가 없어 권고 포트폴리오가 비어 있습니다."]}
