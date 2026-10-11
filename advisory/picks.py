"""공격 진입 추천 팀. 계좌복구 공격매매 모드(recovery_mode)의 '가장 강한 종목' 풀에서 3개 렌즈 에이전트가 각자 점수를 매겨 순위를 정한다.
검증은 이 모듈이 하지 않는다(advisory/verify.py가 독립적으로 한다). 이 모듈은 verify를 가져오지 않는다.

렌즈(각 0~100):
  돌파  : 공격 돌파·돌파 여부 40/20, 거래량 배율(1배→0, 3배→30), 종가 위치(CLV 0.5→0, 1.0→20), 피벗 위 0~3% 10 / ~5% 5
  리더  : RS 점수 ×0.5, 52주 고점 근접(고점 대비 -10%→0, 0%→30), 섹터 코호트 점수 ×0.2
  안전  : 초기 위험(0%→50, 6% 이상→0), 과열 아님 20, 당일 갭(0%→15, 6% 이상→0), 유동성 통과 15
종합 = 세 렌즈 평균(동점이면 계좌복구 강도점수).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config


def _n(v):
    return v if isinstance(v, (int, float)) and v == v else None


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def breakout_lens(r: dict) -> float:
    s = 40 if r.get("aggressiveGo") else 20 if r.get("breakout") else 0
    vr = _n(r.get("volumeRatio"))
    s += _clip(((vr if vr is not None else 1) - 1) / 2) * 30
    clv = _n(r.get("clv"))
    s += _clip(((clv if clv is not None else 0.5) - 0.5) / 0.5) * 20
    pd = _n(r.get("pivotDistancePct"))
    s += 10 if pd is not None and 0 <= pd <= 3 else 5 if pd is not None and 3 < pd <= 5 else 0
    return round(s, 1)


def leader_lens(r: dict) -> float:
    rs = _n(r.get("rsScore")) or 0
    hp = _n(r.get("highProximityPct"))
    coh = _n(r.get("sectorCohortScore")) or 0
    return round(rs * 0.5 + _clip(1 + (hp if hp is not None else -10) / 10) * 30 + coh * 0.2, 1)


def safety_lens(r: dict) -> float:
    risk = _n(r.get("initialRiskPct"))
    gap = _n(r.get("gapPct"))
    s = _clip(1 - (risk if risk is not None else 6) / 6) * 50
    s += 20 if r.get("notExtended") else 0
    s += _clip(1 - abs(gap if gap is not None else 6) / 6) * 15
    s += 15 if r.get("liquidityOk") else 0
    return round(s, 1)


LENSES = (("돌파", breakout_lens), ("리더", leader_lens), ("안전", safety_lens))


POOL_SIZE = 15   # 계좌복구 모드의 랭킹(강도점수순) 상위 N개를 후보로 삼는다(그 모드의 '가장 강한 5개'보다 넓게)


def load_pool(root: Path | None = None) -> dict:
    """후보 풀. 계좌복구 모드의 랭킹 로직(rankable·enrich_row·강도점수)을 그대로 써서 상위 POOL_SIZE개.
    실행 환경에 그 모듈이 없거나 실패하면 이미 만들어진 docs/recovery/data/latest.json의 '가장 강한 5개'로 대체한다."""
    root = root or config.ROOT
    try:
        import recovery_mode as rm
        if Path(rm.ROOT).resolve() == Path(root).resolve():
            valuation, estimates = rm.load_valuation_us(), rm.estimate_history()
            rows, sessions, regimes = [], {}, {}
            for market in ("kr", "us"):
                mrows, session, _state = rm.load_market(market)
                sessions[market] = session
                regimes.update(rm.regime_summary(mrows))
                rows += [rm.enrich_row(r, market, valuation, estimates) for r in mrows if rm.rankable(r)]
            if rows:
                rm.apply_sector_strength(rows)
                rows.sort(key=lambda r: (r["strengthScore"], rm._num(r.get("signalCount"), 0.0) or 0.0, rm._num(r.get("volumeRatio"), 0.0) or 0.0), reverse=True)
                return {"rows": rows[:POOL_SIZE], "sessions": sessions, "generatedAt": None, "regimes": regimes, "source": "recovery_mode(상위 %d)" % POOL_SIZE}
    except Exception:  # noqa: BLE001 — 아래 대체 경로로
        pass
    try:
        d = json.loads((root / "docs" / "recovery" / "data" / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rows": [], "sessions": {}, "generatedAt": None, "regimes": {}, "source": "없음"}
    return {"rows": d.get("strongest") or [], "sessions": d.get("sessions") or {}, "generatedAt": d.get("generatedAt"),
            "regimes": d.get("marketRegimes") or {}, "source": "docs/recovery/data/latest.json(상위 5)"}


def rank(pool_rows: list[dict]) -> list[dict]:
    out = []
    for r in pool_rows:
        lens = {name: fn(r) for name, fn in LENSES}
        out.append({"market": r.get("market"), "code": str(r.get("code")), "name": r.get("name"), "sector": r.get("sector"),
                    "industry": r.get("industry"), "lens": lens, "composite": round(sum(lens.values()) / len(lens), 1),
                    "strength": _n(r.get("strengthScore")) or 0, "row": r})
    out.sort(key=lambda p: (-p["composite"], -p["strength"], p["code"]))
    return out


def plan(r: dict) -> dict | None:
    """진입 구간·손절·목표. 돌파한 종목은 계좌복구 모드의 실행 계획을 그대로 쓰고,
    아직 돌파 전이라 계획이 없으면 '종가 진입 + 기준 손절가'로 대체 계획을 만든다(fallback=True)."""
    try:
        import recovery_mode
        pl = recovery_mode.execution_plan(r)
        if pl:
            return pl
    except Exception:  # noqa: BLE001
        pass
    close, stop = _n(r.get("close")), _n(r.get("referenceStop"))
    if not close:
        return None
    why = "기준 손절가"
    # 돌파 전 종목은 기준 손절가가 현재가보다 위에 있거나, 현재가에 거의 붙어(예: -0.1%) 손절로 의미가 없을 수 있다
    if not (stop and 0 < stop < close) or (close - stop) / close * 100 < config.PICK_MIN_STOP_PCT:
        atr, pct = _n(r.get("atr14")), config.PICK_FALLBACK_STOP_PCT
        atr_pct = atr / close * 100 if atr else None
        why = f"종가 -{pct:.0f}% 규칙"
        if atr_pct and atr_pct * config.PICK_ATR_STOP_MULT > pct:   # 변동성 대비 손절이 너무 가까우면 넓힌다(비중은 그만큼 줄어든다)
            pct, why = atr_pct * config.PICK_ATR_STOP_MULT, f"변동성 반영 -{atr_pct * config.PICK_ATR_STOP_MULT:.1f}% (1.5×ATR {atr_pct:.1f}%)"
        stop = close * (1 - pct / 100)
    loss = (close - stop) / close * 100
    return {"entryPriceMin": round(close, 2), "entryPriceMax": round(close, 2), "referenceStop": round(stop, 2),
            "target1R": round(close + (close - stop), 2), "target2R": round(close + 2 * (close - stop), 2),
            "plannedLossPct": loss, "fallback": True, "stopBasis": why}


def weight(planned_loss_pct: float | None, stance: str, grade: str) -> float | None:
    """권고 비중(자본 대비) = 손실 목표 ÷ 계획 손실폭, 상한 15%, 입장·등급 배율. 계획이 없으면 None."""
    if not planned_loss_pct or planned_loss_pct <= 0:
        return None
    w = min(config.PICK_MAX_WEIGHT, config.PICK_RISK_BUDGET_PCT / planned_loss_pct)
    return round(w * config.STANCE_SIZE[stance] * config.GRADE_SIZE[grade], 4)
