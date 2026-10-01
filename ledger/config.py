"""고정 규칙. 결과를 본 뒤 바꾸지 않는다(바꾸면 LEDGER.md 변경 이력에 사유와 함께 남기고 새 계열로 센다)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER_DIR = ROOT / "research" / "ledger"
SIGNALS_DIR = LEDGER_DIR / "signals"
OUTCOMES_DIR = LEDGER_DIR / "outcomes"
EXCHANGE_CACHE = LEDGER_DIR / "exchange_kr.json"
PUBLIC_JSON = ROOT / "docs" / "research" / "ledger.json"

RULES_FROZEN_ON = "2026-10-01"
HORIZONS = (5, 20, 60, 120)        # 신호 후 거래일
MIN_N = 30                         # 이 미만이면 '표본 부족'
MIN_WINDOWS = 5                    # 서로 겹치지 않는 기간(독립 구간)이 이보다 적으면 '독립 구간 부족'
BOOT_N = 2000                      # 신호일 단위 부트스트랩 반복 수
BOOT_SEED = 20261001
CONTROL_PER_DATE = 150             # 대조군: 같은 날 같은 유니버스에서 신호와 무관하게 뽑은 표본 수
TOP_K = 50                         # 깔때기 상위 그룹

# 그날 종가가 확정됐다고 보는 UTC 시각(한국 장 마감 06:30, 미국 20:00~21:00). 이전에 기록된 값은 전 거래일 종가다.
CLOSE_FINAL_UTC_HOUR = {"kr": 7, "us": 21}

BENCHMARKS = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11", "US": "^GSPC"}
BENCHMARK_LABEL = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "US": "S&P500"}

CONTROL = "CONTROL"
