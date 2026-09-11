"""
sepa/states.py — 상태 상수
==========================

한 종목은 서로 독립적인 두 상태를 가진다.

- ``entry_state``  : 신규 진입 관점 (TREND → SETUP → READY → ENTRY 계열)
- ``exit_state``   : 보유/매도 관점 (가격행동 기반 경고. 포지션이 있으면 더 정밀해짐)

두 상태는 한 필드에 합치지 않는다. 진입 후보이면서 동시에 매도 경고가 뜰 수
있기 때문이다 (예: GO_PULLBACK 인데 PROFIT_ALERT).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# ENTRY 계열
# ---------------------------------------------------------------------------
TREND_FAIL = "TREND_FAIL"                 # 8개 TREND TEMPLATE 조건 미충족
TREND_OK = "TREND_OK"                     # TREND 통과, 아직 셋업 아님
SETUP = "SETUP"                           # 셋업 형성 중 (피벗 한참 아래)
WATCH = "WATCH"                           # 피벗 접근 중 (-5% ~ -2%)
READY = "READY"                           # 피벗 임박 (-2% ~ 0%)
BREAKOUT_UNCONFIRMED = "BREAKOUT_UNCONFIRMED"  # 피벗 0~3% 위지만 거래량/종가위치 미확인
GO_BREAKOUT = "GO_BREAKOUT"               # 확인된 돌파 (거래량 + 종가위치)
GO_PULLBACK = "GO_PULLBACK"               # 돌파 후 눌림목 되돌림에서 건강한 반등
LATE = "LATE"                             # 피벗 +3% ~ +5% (추격 주의)
EXTENDED = "EXTENDED"                     # 피벗 +5% 초과 (추격 금지)
FAILED = "FAILED"                         # 최근 돌파가 빠르게 실패 (FAST_FAIL 감지)

ENTRY_STATES = [
    TREND_FAIL, TREND_OK, SETUP, WATCH, READY,
    BREAKOUT_UNCONFIRMED, GO_BREAKOUT, GO_PULLBACK, LATE, EXTENDED, FAILED,
]

# 대시보드 정렬/우선순위용 (진입 매력도가 높은 순).
ENTRY_STATE_RANK = {
    GO_BREAKOUT: 0,
    GO_PULLBACK: 1,
    READY: 2,
    BREAKOUT_UNCONFIRMED: 3,
    WATCH: 4,
    SETUP: 5,
    LATE: 6,
    EXTENDED: 7,
    TREND_OK: 8,
    FAILED: 9,
    TREND_FAIL: 10,
}

# "지금 실제 진입 후보" 로 볼 상태
GO_STATES = {GO_BREAKOUT, GO_PULLBACK}


# ---------------------------------------------------------------------------
# EXIT 계열
# ---------------------------------------------------------------------------
HOLD = "HOLD"                             # 추세 유지, 이상 없음
WATCH_EXIT = "WATCH_EXIT"                 # EMA10 종가 이탈 등 1차 경고
FAST_FAIL = "FAST_FAIL"                   # 돌파 직후 실패 (빠른 손절 대상)
STOP = "STOP"                             # 구조적 손절가 이탈 (포지션 필요)
TREND_BREAK = "TREND_BREAK"               # SMA50 대량 거래 이탈 등 추세 훼손
TIME_STOP = "TIME_STOP"                   # 진입 후 무성과 + RS 약화 (포지션 필요)
PROFIT_ALERT = "PROFIT_ALERT"            # 클라이맥스/과이격 — 경고만 (강제매도 아님)

EXIT_STATES = [
    HOLD, WATCH_EXIT, FAST_FAIL, STOP, TREND_BREAK, TIME_STOP, PROFIT_ALERT,
]

# 심각도 (클수록 심각). 스크리너에서 여러 경고가 동시에 뜨면 가장 심각한 것을 대표로.
EXIT_STATE_SEVERITY = {
    HOLD: 0,
    PROFIT_ALERT: 1,
    WATCH_EXIT: 2,
    TIME_STOP: 3,
    TREND_BREAK: 4,
    STOP: 5,
    FAST_FAIL: 6,
}

# 포지션(진입가·진입일) 이 있어야만 판정 가능한 상태 — 스크리너 단독에서는 안 뜬다.
POSITION_REQUIRED_EXIT_STATES = {STOP, TIME_STOP}


# ---------------------------------------------------------------------------
# MARKET REGIME
# ---------------------------------------------------------------------------
GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"
RECOVERY = "RECOVERY"
REGIMES = [GREEN, YELLOW, RED, RECOVERY]

# breadth 등급
BREADTH_STRONG = "STRONG"
BREADTH_NORMAL = "NORMAL"
BREADTH_WEAK = "WEAK"
BREADTH_RISK_OFF = "RISK_OFF"


# ---------------------------------------------------------------------------
# 52주 고점 근접도 품질 등급
# ---------------------------------------------------------------------------
SUPER_LEADER = "SUPER_LEADER"
LEADER = "LEADER"
NORMAL = "NORMAL"
PROXIMITY_FAIL = "FAIL"
