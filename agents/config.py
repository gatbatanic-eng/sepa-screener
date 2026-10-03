"""에이전트 팀 고정 규칙. 결과를 본 뒤 바꾸지 않는다(바꾸면 AGENTS.md 변경 이력에 사유와 함께 남기고 새 계열로 센다)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "research" / "agents"
PUBLIC_JSON = ROOT / "docs" / "research" / "agents.json"

RULES_FROZEN_ON = "2026-10-03"
INCEPTION = "2026-10-05"          # 이 날짜 이후(포함)의 신호만 '실전 계열'. 이전은 규칙을 정한 뒤 소급 적용한 참고용(사후 적용).
PREVIEW_START = "2026-09-11"      # 사후 적용 계열의 시작일(SEPA 추적기 첫 신호일)

INITIAL_CAPITAL = 8000            # 에이전트당 가상 자본. 단위는 표시용(환율 미반영, 시장 구분 없이 합산)
MAX_POSITIONS = 6                 # 동시 보유 상한. 한 포지션 = 평가금액 ÷ MAX_POSITIONS (현금 한도 내)
HOLD_SESSIONS = 40                # 시간 청산: 진입 후 40거래일(약 2개월)
STOP_PCT = 8.0                    # 종가가 진입가 대비 -8% 이하이면 그 종가에 청산(장중 손절은 가정하지 않는다)
COST_BPS = {"kr": 25, "us": 10}   # 편도 비용(수수료·세금·슬리피지 합산 가정)
ENTRY_LAG = 1                     # 신호 유효일 종가를 본 뒤 '다음 거래일 종가'에 진입한다
MIN_CLOSED = 30                   # 이 미만이면 '표본 부족' — 도태 판단 금지
CONTROL_SEED = "agents-control-v1"
