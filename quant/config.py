"""퀀트 계열 고정 규칙(QUANT.md). 결과를 본 뒤 바꾸지 않는다. 바꾸면 QUANT.md 변경 이력에 사유를 쓰고 새 계열(새 ID)로 센다."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "research" / "quant"
FIN_DIR = STATE_DIR / "fin"                 # 종목별 분기 재무(공시일 포함): fin/kr/CODE.json, fin/us/SYMBOL.json
CACHE_DIR = STATE_DIR / "_cache"            # DART 확정 분기 캐시(기존 research/fundamentals 와 분리)
SELECTIONS_DIR = STATE_DIR / "selections"   # 리밸런싱 선정 기록(한 번만 쓰고 수정 금지)
REVIEWS_PATH = STATE_DIR / "reviews.json"   # 도태 전이 기록(추가만)
UNIVERSE_US_PATH = STATE_DIR / "universe_us.json"
PUBLIC_JSON = ROOT / "docs" / "research" / "quant.json"

RULES_FROZEN_ON = "2026-10-19"
INCEPTION_SIGNAL = "2026-10-30"      # 이 날짜(포함) 이후의 신호일만 '실전 계열'. 이전은 규칙을 정한 뒤 소급 적용한 참고용(사후 적용)
PREVIEW_START_SIGNAL = "2025-01-31"  # 사후 적용 계열의 첫 신호일(재무·가격이 모자란 시장은 그 달 건너뜀)

INITIAL_CAPITAL = 8000               # 가상 자본. 에이전트 리그와 같은 단위(환율 미반영, 시장 구분 없이 합산)
SLOTS = {"kr": 15, "us": 15}         # 시장별 보유 종목 수
TOTAL_SLOTS = sum(SLOTS.values())    # 한 포지션 = 평가금액 ÷ 30 (현금 한도 내)
KEEP_RANK = 30                       # 보유 종목은 시장 내 종합 순위 30위 안이면 유지(보유 완충대)
CASH_SPLIT_CAP = 1.5                 # 신규 편입 1건의 금액 상한 = 평가금액 ÷ 30 × 1.5 (첫 달은 현금 ÷ 30 이 먼저 걸림)
MAX_PER_INDUSTRY = 4                 # 같은 업종(KSIC·SIC 2자리) 신규 편입 상한(시장 안)
COST_BPS = {"kr": 25, "us": 10}      # 편도 비용(수수료·세금·슬리피지 합산 가정)

MIN_MARCAP = {"kr": 1000e8, "us": 2e9}   # 한국 1,000억 원 / 미국 20억 달러
MAX_DEBT_TO_EQUITY = 200.0               # 부채 ÷ 자본 (%)
MIN_BARS = 253                           # 12−1개월 수익률(252거래일 전)을 구하려면 필요한 종가 수
STALE_BAR_DAYS = 7                       # 신호일 기준 마지막 종가가 이보다 오래되면 제외(거래정지·상폐 의심)
MAX_FIN_AGE_DAYS = 400                   # 최근 공시 분기가 이보다 오래되면 제외
MIN_ELIGIBLE = 30                        # 시장 안 적격 종목이 이보다 적으면 그 시장은 그 달 매매하지 않음(데이터 이상 방어)
FINANCIAL_PREFIX = {"kr": ("64", "65", "66"), "us": ("60", "61", "62", "63", "64", "65", "66", "67")}

# 변형: 종합 점수의 (가치, 모멘텀, 퀄리티) 가중치. control 은 적격 종목 중 해시 순서.
WEIGHTS = {
    "multifactor": (0.40, 0.30, 0.30),
    "vq": (4 / 7, 0.0, 3 / 7),   # 모멘텀 제외(가치:퀄리티 = 40:30 유지)
    "m": (0.0, 1.0, 0.0),
}
VARIANTS = ("multifactor", "vq", "m", "control")
LABELS = {"multifactor": "퀀트 멀티팩터", "vq": "가치+퀄리티(모멘텀 제외)", "m": "모멘텀만", "control": "무작위 대조군"}
CONTROL_SEED = "quant-control-v1"

MIN_PERIODS = 12                     # 월간 리밸런싱 12회 미만이면 '표본 부족' — 성과 기반 판단 금지
MDD_PROBATION = -15.0
MDD_RETIRE = -25.0
RETIRE_MIN_PERIODS = 24              # 신뢰구간 기반 소멸은 24회 이상
BOOT_N = 2000
BOOT_SEED = 20261019
