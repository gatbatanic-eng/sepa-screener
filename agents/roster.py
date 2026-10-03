"""에이전트 명단. 모두 SEPA 신호 시점의 속성(point-in-time)만 쓰는 결정론적 규칙이다.
투자자 이름은 붙이지 않는다 — 규칙이 그 투자자를 재현한다고 주장할 근거가 없다. 'lens'는 영감을 받은 관점일 뿐이다.

select(signal) → 점수(클수록 우선) 또는 None(진입 안 함).
"""
from __future__ import annotations

import hashlib
from typing import Callable

from . import config


def _a(sig: dict) -> dict:
    return sig.get("attributes") or {}


def _num(x):
    return x if isinstance(x, (int, float)) and x == x else None


def trend_leader(sig):
    """추세 템플릿 통과 + 상대강도 최상위 + 신고가 근접. 강한 종목이 더 강해진다는 관점(미너비니·오닐 계열)."""
    a = _a(sig)
    rs, prox = _num(a.get("rsRank")), _num(a.get("highProximity"))
    if rs is None or prox is None or rs < 90 or prox < 0.95 or a.get("highTier") != "SUPER_LEADER":
        return None
    return rs + prox * 10


def setup_ready(sig):
    """돌파 직전·돌파 구간의 셋업 완성 종목만. 타이밍이 수익률을 가른다는 관점."""
    a = _a(sig)
    score = _num(a.get("setupScore"))
    if a.get("zone") not in ("READY", "BREAKOUT_ZONE") or score is None or a.get("exitState") == "WATCH_EXIT":
        return None
    return score


def low_risk(sig):
    """시장 환경 우호 + 초기 위험(손절폭) 작은 종목. 잃지 않는 것이 먼저라는 관점(버크셔식 안전마진의 모멘텀 번안)."""
    a = _a(sig)
    risk = _num(a.get("initRisk"))
    if a.get("marketGate") != "우호적" or risk is None or not 0 < risk <= 8 or a.get("riskFlag"):
        return None
    return -risk


def random_control(sig):
    """대조군: 같은 신호 풀에서 신호 id 해시로 고르는 무작위 종목. 위 규칙들이 이 대조군을 못 이기면 규칙에 우위가 없다."""
    h = hashlib.sha256(f"{config.CONTROL_SEED}:{sig['id']}".encode()).digest()
    return h[0] / 255 if h[1] % 3 == 0 else None  # 신호의 약 1/3을 무작위로 뽑고, 그 안에서도 해시 순서로 우선순위


ROSTER: dict[str, dict] = {
    "trend_leader": {"label": "추세 리더", "lens": "미너비니·오닐: 강한 놈이 더 간다", "select": trend_leader},
    "setup_ready": {"label": "셋업 완성", "lens": "타이밍: 돌파 직전만 산다", "select": setup_ready},
    "low_risk": {"label": "저위험 진입", "lens": "안전마진: 손절폭이 작을 때만 산다", "select": low_risk},
    "control": {"label": "무작위 대조군", "lens": "비교 기준(실력 없는 선택)", "select": random_control},
}
Selector = Callable[[dict], "float | None"]
USED_KEYS = ("rsRank", "highProximity", "highTier", "zone", "setupScore", "exitState", "initRisk", "marketGate", "riskFlag", "structStop")
