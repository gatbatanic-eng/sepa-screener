"""도태 심사. 소멸은 단계(ACTIVE → PROBATION → RETIRED)로만 일어나며 RETIRED는 되돌리지 않는다. 규칙은 config와 AGENTS.md에 고정.

심사 대상은 실전 계열뿐이다. 상태 전이는 research/agents/reviews.json에 이력으로 남는다(추가만 가능).
성과 판단은 '대조군 대비 평균 거래 수익률 차이'(진입일 단위 부트스트랩 95% 구간)로 한다. 위험 규칙(최대 낙폭)만 표본과 무관하게 즉시 적용한다.
"""
from __future__ import annotations

from . import config, stats


def initial(date: str) -> dict:
    return {"status": "ACTIVE", "since": date, "closedAtChange": 0, "history": []}


def review(prev: dict | None, summary: dict, trades: list[dict], control_trades: list[dict], date: str) -> tuple[dict, dict]:
    """(새 상태, 판단 근거). prev가 RETIRED면 그대로 둔다."""
    state = dict(prev or initial(date))
    state["history"] = list(state.get("history", []))
    closed = summary.get("closed", 0)
    mdd = summary.get("maxDrawdownPct", 0.0)
    cmp_ = (stats.cluster_bootstrap_diff(trades, control_trades)
            if closed >= config.MIN_CLOSED_REVIEW and len(control_trades) >= config.MIN_CLOSED_REVIEW else None)
    evidence = {"closed": closed, "maxDrawdownPct": mdd, "vsControl": cmp_,
                "controlClosed": len(control_trades)}
    if state["status"] == "RETIRED":
        return state, evidence
    cur, to, why = state["status"], None, None
    extra = closed - state.get("closedAtChange", 0)
    if mdd <= config.MDD_RETIRE:
        to, why = "RETIRED", f"최대 낙폭 {mdd}% ≤ {config.MDD_RETIRE}%"
    elif cur == "ACTIVE":
        if mdd <= config.MDD_PROBATION:
            to, why = "PROBATION", f"최대 낙폭 {mdd}% ≤ {config.MDD_PROBATION}%"
        elif cmp_ and cmp_["diff"] < 0:
            to, why = "PROBATION", f"대조군 대비 평균 거래 {cmp_['diff']}%p (청산 {closed}건)"
    elif cur == "PROBATION":
        if cmp_ and closed >= config.MIN_CLOSED_RETIRE and cmp_["hi"] < 0:
            to, why = "RETIRED", f"대조군보다 유의하게 나쁨(구간 상한 {cmp_['hi']}%p)"
        elif cmp_ and extra >= config.PROBATION_MAX_EXTRA and cmp_["diff"] <= 0:
            to, why = "RETIRED", f"관찰 후 {extra}건이 지나도 대조군 대비 {cmp_['diff']}%p"
        elif cmp_ and extra >= config.PROBATION_RECHECK and cmp_["diff"] > 0 and mdd > config.MDD_PROBATION:
            to, why = "ACTIVE", f"관찰 후 {extra}건, 대조군 대비 +{cmp_['diff']}%p"
    if to:
        state["history"].append({"date": date, "from": cur, "to": to, "reason": why, "closed": closed})
        state.update(status=to, since=date, closedAtChange=closed)
    return state, evidence
