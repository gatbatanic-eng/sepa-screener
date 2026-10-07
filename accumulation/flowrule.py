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


# ---- 매집 v2 (2026-10-07 고정, 결과를 보기 전에 정한 값). v1은 그대로 두고 나란히 기록한다. ----
# 사유(2026-10-07 점검): v1 통과 종목은 20일 수익률 중앙값 +16.5%(이미 오른 종목), 순매수의 35%가 하루에 몰림(블록딜·지수 이벤트),
# 위치 조건 D는 상승 2단계를 고른다. v2는 매집의 '조용함·꾸준함·바닥권'과 프로그램 매매 제외를 직접 요구한다.
V2_CFG = {"days": 20, "net_pct": 0.05, "max_day_share": 0.40, "min_pos_weeks": 3, "max_abs_ret": 0.10, "hi_low": 0.60, "hi_high": 0.90}
V2_GROUPS = ("V2", "V2_FRG", "V2_INST")


def v2_condition(rows: list[list], hi_ratio: float | None, cfg: dict = V2_CFG) -> tuple[bool | None, dict]:
    """rows: 최근 20개 [외국인, 기관, 개인, 종가, 거래량, 프로그램]. 프로그램 값이 없는 행이 있으면 판정 불가(None).
    순매수 = (외국인 + 기관 − 프로그램) x 종가 — 지수 차익거래 같은 프로그램 매매를 뺀 외국인·기관 수요."""
    if len(rows) != cfg["days"] or any(len(r) < 6 for r in rows):
        return None, {}
    x = [(r[0] + r[1] - r[5]) * r[3] for r in rows]
    net, turn = sum(x), sum(r[4] * r[3] for r in rows)
    share = net / turn if turn > 0 else None
    top = max(x) / net if net > 0 else None
    weeks = sum(1 for k in range(0, cfg["days"], 5) if sum(x[k:k + 5]) > 0)
    ret = rows[-1][3] / rows[0][3] - 1 if rows[0][3] else None
    lead = "FRG" if sum(r[0] * r[3] for r in rows) >= sum(r[1] * r[3] for r in rows) else "INST"
    ev = {"netShare": round(share, 4) if share is not None else None, "topDayShare": round(top, 3) if top is not None else None,
          "posWeeks": weeks, "ret20": round(ret, 4) if ret is not None else None, "hiRatio": round(hi_ratio, 3) if hi_ratio is not None else None, "lead": lead}
    ok = bool(share is not None and net > 0 and share >= cfg["net_pct"] and top is not None and top <= cfg["max_day_share"]
              and weeks >= cfg["min_pos_weeks"] and ret is not None and abs(ret) <= cfg["max_abs_ret"]
              and hi_ratio is not None and cfg["hi_low"] <= hi_ratio <= cfg["hi_high"])
    return ok, ev
