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
