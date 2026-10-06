"""에이전트 팀 고정 규칙. 결과를 본 뒤 바꾸지 않는다(바꾸면 AGENTS.md 변경 이력에 사유와 함께 남기고 새 계열로 센다)."""
from __future__ import annotations

import datetime as dt
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

# --- 도태 규칙 (3단계, 2026-10-03 고정). 실전 계열의 성과만 쓴다. 대조군(control)은 심사 대상이 아니다. ---
REVIEW_FROZEN_ON = "2026-10-03"
MDD_PROBATION = -15.0             # 최대 낙폭이 이 이하이면 표본 크기와 무관하게 '관찰'로 (위험 규칙)
MDD_RETIRE = -25.0                # 이 이하이면 표본 크기와 무관하게 '소멸'
MIN_CLOSED_REVIEW = 30            # 성과 기반 강등은 청산 30건 이상 + 대조군도 30건 이상일 때만
MIN_CLOSED_RETIRE = 60            # 신뢰구간 기반 소멸은 청산 60건 이상
PROBATION_RECHECK = 30            # 관찰 후 추가로 이만큼 청산돼야 복귀를 판단
PROBATION_MAX_EXTRA = 60          # 관찰 후 추가 60건이 쌓였는데도 대조군을 못 이기면 소멸
CAPITAL_WEIGHT = {"ACTIVE": 1.0, "PROBATION": 0.5, "RETIRED": 0.0}  # 4단계(자본 배분)가 읽는 권고 비중
BOOT_N = 2000
BOOT_SEED = 20261003

# --- 포트폴리오 매니저·리스크 심사 (4단계, 2026-10-03 고정). 권고일 뿐 주문하지 않는다. ---
PORTFOLIO_FROZEN_ON = "2026-10-03"
MAX_SINGLE_WEIGHT = 0.15          # 한 종목 상한: 자본의 15% (여러 에이전트가 같은 종목을 들면 합산해서 본다)
MAX_MARKET_WEIGHT = 0.70          # 한 시장(한국/미국) 상한: 자본의 70%
MAX_NAMES = 12                    # 권고 종목 수 상한
SECTOR_FROZEN_ON = "2026-10-06"   # 섹터 한도는 업종 데이터(NHPLUG)를 붙인 날 고정. 한국만 적용(미국은 분류 데이터 없음)
MAX_SECTOR_WEIGHT = 0.30          # 한 업종 상한: 자본의 30% (시장 상한 70%보다 먼저 정하지 않고, 결과를 보기 전에 정한 값)


SESSION_CUT = dt.timedelta(hours=22, minutes=40)   # 정기 실행(ledger.yml)의 예약 시각(UTC). 이 시각이 지나야 그날 보고서를 쓴다.


def session_date(now: dt.datetime | None = None) -> dt.date:
    """실행 시각 → 보고서·스냅샷의 기준 거래일 = 22:40 UTC 예약 시각이 이미 지난 가장 최근 평일.
    예약이 지연돼 다음 날 01시 UTC에 시작해도 그 평일로 센다(월 22:40 예약 → 화 01:30 실행 = 월요일).
    그날 예약 시각 전에 수동·푸시로 돌면 직전 평일로 본다(월요일 아침 실행 = 금요일). 주말은 금요일."""
    now = now or dt.datetime.now(dt.timezone.utc)
    d = (now - SESSION_CUT).date()
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d
