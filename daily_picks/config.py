"""오늘의 추천 규칙. 2026-10-10 고정 — 결과를 본 뒤 바꾸지 않는다(바꾸면 v2로 병행)."""
MAX_PICKS = 3                 # 시장별 최대 추천 수
MAX_RISK_PCT = 8.0            # 손절폭 상한(%). 0 이하·결측은 부적합
STOP_LOSS_PCT = 8.0           # 보유 규칙: 종가 -8% 이하면 청산
HOLD_DAYS = 40                # 보유 규칙: 40거래일
RSI_CHASE = 75.0              # technical_signals.config.RSI_CHASE_WARNING 와 같은 값(복사)
PIVOT_CHASE_PCT = 5.0         # technical_signals.config.PIVOT_DISTANCE_HOLD 와 같은 값(복사)
RSI_PERIOD = 14
ATR_PERIOD = 14
SWING_LOOKBACK = 40
ATR_STOP_MULT = 1.75
STOP_ATR_BUFFER_MULT = 0.5
PIVOT_LOOKBACK = 60
CORE_POINTS = 2               # 핵심 전략(SEPA, 깔때기) 선정 1건당 점수
RESEARCH_POINTS = 1           # 연구 전략 선정 1건당 점수
SEPA_GROUPS = ("TREND", "READY", "GO")
