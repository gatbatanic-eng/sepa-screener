"""투자 자문 위원회 고정 규칙. 결과를 본 뒤 바꾸지 않는다(바꾸면 ADVISORY.md 변경 이력에 사유와 함께 남긴다)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "advisory" / "data"
RULES_FROZEN_ON = "2026-10-05"

# --- 입장(stance) 점수: 데스크별 점수의 합. 표본·판단 불가 항목은 0점이 아니라 '판단 보류'로 제외한다. ---
MACRO_POINTS = {"risk_on": 2, "neutral": 0, "risk_off": -2}
MARKET_REGIME_POINTS = {"GREEN": 1, "YELLOW": 0, "RED": -1}   # 시장별 다수 국면(한국·미국 각각)
BREADTH_HIGH, BREADTH_LOW = 0.60, 0.30                          # 중앙값 breadth: 이상 +1, 이하 -1
OFFENSE_MIN, DEFENSE_MAX = 3, -3                                # 합계 ≥ +3 공격, ≤ -3 방어, 그 사이 중립
STANCE_LABELS = {"offense": "공격", "neutral": "중립", "defense": "방어"}
EXPOSURE_CAP = {"offense": 100, "neutral": 70, "defense": 40}   # 입장별 권고 총 투입 상한(%) — 참고값

# --- 섹터·종목 데스크 ---
MIN_GROUP_N = 6            # 섹터 통계는 종목 6개 이상일 때만
TOP_SECTORS = 4
MAX_CANDIDATES = 8         # 종목 데스크 후보 수 상한
STALE_MACRO_DAYS = 3       # 매크로 갱신이 이보다 오래되면 '오래된 데이터'로 표시

# --- 공격 진입 추천(2026-10-05 고정). 추천 팀과 검증 팀은 코드·기준이 분리돼 있다. ---
PICK_MIN, PICK_MAX = 2, 3           # 매일 추천 종목 수(최소 2, 최대 3). 후보 풀이 2개 미만이면 있는 만큼만
PICK_RISK_BUDGET_PCT = 1.0          # 손절 시 자본 대비 손실 목표(%) → 권고 비중 = 목표 ÷ 계획 손실폭
PICK_MAX_WEIGHT = 0.15              # 한 종목 상한(에이전트 리그와 동일)
STANCE_SIZE = {"offense": 1.0, "neutral": 0.75, "defense": 0.5}   # 입장별 비중 배율
GRADE_SIZE = {"A": 1.0, "B": 0.5, "C": 0.0}                       # 검증 등급별 비중 배율(C=관찰 전용, 비중 0)
PICK_FALLBACK_STOP_PCT = 8.0        # 계산된 손절가가 없을 때 종가 대비 최소 손절폭(에이전트 리그와 동일)
PICK_ATR_STOP_MULT = 1.5            # 변동성이 크면 손절폭을 max(8%, 1.5×ATR%)로 넓히고 그만큼 비중을 줄인다
PICK_WARN_PENALTY = 2.0             # 최종 순위 점수 = 종합 점수 − 2 × 검증 경고 개수(경고가 많은 종목이 같은 B라고 같게 보이지 않게)
PICK_CAPITAL = 8000                 # 표시용 자본(에이전트 리그와 같은 단위)

# 검증 팀 기준(추천 팀과 별개로 정한다)
VERIFY_PRICE_TOL = 0.01             # 추천 데이터와 스크리너 본 결과의 종가 차이 허용(1%)
# 시장별 검증 기준(2026-10-06). SEPA 구조적 손절폭은 미국 기준으로 만든 임계값이라, 한국 급등주는 스윙 저점이 30~40% 아래에 있는 일이 흔해
# 그대로 쓰면 구조적으로 전부 기각된다. 한국은 구조적 손절폭의 경고선을 넓히는 대신 변동성(ATR)·과열·시가총액을 따로 본다.
VERIFY_RULES = {
    "us": {"sepa_warn": 8.0, "sepa_fail": 12.0, "mcap_warn": None, "mcap_fail": None},
    "kr": {"sepa_warn": 25.0, "sepa_fail": 50.0, "mcap_warn": 1e11, "mcap_fail": 3e10},   # 시가총액(원): 1,000억 미만 경고, 300억 미만 기각
}
VERIFY_ATR_WARN, VERIFY_ATR_FAIL = 6.0, 10.0       # 종목 차트에서 검증 팀이 직접 계산한 ATR14(%) — 하루 평균 변동폭
VERIFY_EXT_WARN, VERIFY_EXT_FAIL = 40.0, 80.0      # 종가의 50일선 대비 괴리(%) — 과열
VERIFY_FLOW_DAYS = 5                # 한국 후보: 최근 5거래일 외국인·기관 순매수(NHPLUG)
VERIFY_GAP_WARN = 4.0               # 당일 갭(%) 경고
VERIFY_SEVERE_CONCERNS = 2          # 심각도 3 우려가 이 개수 이상이면 경고

# 사후 검증
TRACK_HORIZONS = (5, 20, 40)        # 추천 다음 거래일 종가 진입 후 N거래일(40 ≈ 2개월)
TRACK_MIN_N = 30                    # 이 미만이면 '표본 부족'
TRACK_CONTROL_PER_DAY = 2           # 대조군: 같은 날 같은 풀에서 무작위 2종목
