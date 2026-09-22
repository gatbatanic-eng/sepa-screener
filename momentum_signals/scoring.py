"""momentum_signals/scoring.py — 하드 게이트, 강도 점수, 후보 압축(스펙 5~7번).

파이프라인 순서(스펙 원문 그대로): 4번에서 시장별 RS 상위를 병합해 통합
상위 5종목을 먼저 확정하고, 그 5종목에 대해서만 5~7번(하드게이트→점수→
후보 압축)을 적용한다. 5종목 밖의 종목은 애초에 후보가 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config as cfg
from signals import StockMetrics


@dataclass
class GateResult:
    volume_ok: bool | None = None
    risk_ok: bool | None = None

    @property
    def passed(self) -> bool | None:
        if self.volume_ok is None or self.risk_ok is None:
            return None
        return self.volume_ok and self.risk_ok


@dataclass
class ScoreBreakdown:
    rs: float = 0.0
    volume: float = 0.0
    pivot: float = 0.0
    clv: float = 0.0
    rsi: float = 0.0
    disparity: float = 0.0
    risk_efficiency: float = 0.0

    @property
    def total(self) -> float:
        return round(self.rs + self.volume + self.pivot + self.clv +
                     self.rsi + self.disparity + self.risk_efficiency, 2)


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def hard_gate(m: StockMetrics) -> GateResult:
    g = GateResult()
    if m.volume_ratio is not None:
        g.volume_ok = m.volume_ratio >= cfg.GATE_VOLUME_RATIO_MIN
    if m.risk_pct is not None:
        g.risk_ok = m.risk_pct <= cfg.GATE_MAX_INITIAL_RISK_PCT
    return g


def _pivot_score(dist_pct: float) -> float:
    w = cfg.SCORE_WEIGHTS["pivot"]
    if cfg.PIVOT_SCORE_OPTIMAL_LOW <= dist_pct <= cfg.PIVOT_SCORE_OPTIMAL_HIGH:
        return w
    if dist_pct < cfg.PIVOT_SCORE_OPTIMAL_LOW:
        over = cfg.PIVOT_SCORE_OPTIMAL_LOW - dist_pct
    else:
        over = dist_pct - cfg.PIVOT_SCORE_OPTIMAL_HIGH
    return w * _clip01(1.0 - over / cfg.PIVOT_SCORE_DECAY_RANGE)


def _rsi_score(value: float) -> float:
    w = cfg.SCORE_WEIGHTS["rsi"]
    if cfg.RSI_SCORE_BAND_LOW <= value <= cfg.RSI_SCORE_BAND_HIGH:
        return w
    if value < cfg.RSI_SCORE_BAND_LOW:
        over = cfg.RSI_SCORE_BAND_LOW - value
    else:
        over = value - cfg.RSI_SCORE_BAND_HIGH
    return w * _clip01(1.0 - over / cfg.RSI_SCORE_DECAY_RANGE)


def _disparity_score(value: float) -> float:
    w = cfg.SCORE_WEIGHTS["disparity"]
    if value <= cfg.DISPARITY_SCORE_FULL_MAX:
        return w
    over = value - cfg.DISPARITY_SCORE_FULL_MAX
    return w * _clip01(1.0 - over / cfg.DISPARITY_SCORE_DECAY_RANGE)


def score(m: StockMetrics) -> ScoreBreakdown:
    """m의 필드가 None인 항목은 0점 처리(값을 아예 모르면 가점하지 않는다)."""
    s = ScoreBreakdown()
    w = cfg.SCORE_WEIGHTS

    if m.rs_score is not None:
        s.rs = round(w["rs"] * _clip01(m.rs_score / 100.0), 2)
    if m.volume_ratio is not None:
        s.volume = round(w["volume"] * _clip01(m.volume_ratio / cfg.VOLUME_SCORE_FULL_RATIO), 2)
    if m.pivot_distance_pct is not None:
        s.pivot = round(_pivot_score(m.pivot_distance_pct), 2)
    if m.clv_value is not None:
        s.clv = round(w["clv"] * _clip01(m.clv_value / cfg.CLV_SCORE_FULL), 2)
    if m.rsi_value is not None:
        s.rsi = round(_rsi_score(m.rsi_value), 2)
    if m.disparity20 is not None:
        s.disparity = round(_disparity_score(m.disparity20), 2)
    if m.risk_pct is not None:
        s.risk_efficiency = round(
            w["risk_efficiency"] * _clip01(1.0 - m.risk_pct / cfg.RISK_EFFICIENCY_REFERENCE_PCT), 2)
    return s


def select_top5_balanced(rs_by_code: dict[str, tuple[StockMetrics, float]],
                          kr_codes: list[str], us_codes: list[str],
                          n: int = cfg.CANDIDATE_TOP5_N) -> list[str]:
    """시장별 RS 상위를 번갈아 뽑아 통합 상위 n종목을 만든다(스펙 4번 "시장
    규모 차이로 인한 편중 방지"). RS_SCORE가 None인 종목은 제외한다."""
    def ranked(codes: list[str]) -> list[str]:
        scored = [c for c in codes if c in rs_by_code and rs_by_code[c][1] is not None]
        return sorted(scored, key=lambda c: rs_by_code[c][1], reverse=True)

    kr_ranked, us_ranked = ranked(kr_codes), ranked(us_codes)
    out: list[str] = []
    i = j = 0
    turn_kr = True
    while len(out) < n and (i < len(kr_ranked) or j < len(us_ranked)):
        if turn_kr and i < len(kr_ranked):
            out.append(kr_ranked[i]); i += 1
        elif not turn_kr and j < len(us_ranked):
            out.append(us_ranked[j]); j += 1
        elif i < len(kr_ranked):
            out.append(kr_ranked[i]); i += 1
        elif j < len(us_ranked):
            out.append(us_ranked[j]); j += 1
        turn_kr = not turn_kr
    return out
