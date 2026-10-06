"""수급 매집 후보 규칙(2026-10-06 고정, 결과를 보기 전에 정한 값). 한국만.
C 수급: 최근 20거래일 (외국인+기관) 순매수 금액이 양수이고 같은 기간 거래대금의 5% 이상이며, 순매수한 날이 12일 이상.
   금액 = 일별 순매수 수량 x 그날 종가. 외국인은 NHPLUG invest 필드(네이버와 소폭 차이가 나는 근사값).
FLOW      = C 그리고 D(위치: 150·200일선 위, 50일선 +15% 이내, 52주 고점 75% 이상, 상승일/하락일 거래량 >= 1.2)
FLOW_ACC  = C 그리고 A(거래대금 증가) 그리고 B(좁은 변동폭) 그리고 D
A·B·D는 accumulation/features.py의 값 그대로(과거 가격·거래량 백테스트에서 효과가 확인되지 않았지만, 수급과 결합했을 때를 따로 본다)."""
from __future__ import annotations

FLOW_CFG = {"days": 20, "net_pct": 0.05, "min_pos_days": 12}
GROUPS = ("FLOW", "FLOW_ACC")


def flow_condition(rows: list[list], cfg: dict = FLOW_CFG) -> tuple[bool, dict]:
    """rows: 최근 cfg['days']개 [외국인, 기관, 개인, 종가, 거래량]. (충족 여부, 근거 수치)"""
    if len(rows) != cfg["days"]:
        return False, {}
    net = sum((r[0] + r[1]) * r[3] for r in rows)
    turn = sum(r[4] * r[3] for r in rows)
    pos = sum(1 for r in rows if r[0] + r[1] > 0)
    share = net / turn if turn > 0 else None
    ok = bool(share is not None and net > 0 and share >= cfg["net_pct"] and pos >= cfg["min_pos_days"])
    return ok, {"netShare": round(share, 4) if share is not None else None, "posDays": pos}


def groups(c_ok: bool, a: bool, b: bool, d: bool) -> list[str]:
    out = []
    if c_ok and d:
        out.append("FLOW")
        if a and b:
            out.append("FLOW_ACC")
    return out
