"""기술적 신호 스크리너 실행 스크립트.

사용법 (technical_signals/ 안에서)
----------------------------------
    python run_daily_screen.py --market KR        # 코스피+코스닥 전체
    python run_daily_screen.py --market US        # S&P500 전체
    python run_daily_screen.py --market ALL        # 둘 다 순차 실행
    python run_daily_screen.py --market KR --limit 20   # 개발/테스트용

결과는 output/latest_{kr,us}.json 에 저장된다(그다음 generate_dashboard.py가
읽어서 docs/technical/index.html을 만든다) — SEPA의 screening.py →
generate_dashboard.py 흐름과 동일한 2단계 구조.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pipeline import record_to_dict, regime_to_dict, run

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_market(market: str, limit: int | None) -> None:
    logger.info("=== %s 기술적 신호 스크리닝 시작 ===", market)
    records, regime = run(market, limit=limit)
    ok = sum(1 for r in records if r.status == "OK")
    logger.info("%s: %d종목 중 %d 정상판정 (시장국면=%s)", market, len(records), ok, regime.regime)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = market.lower()
    path = OUTPUT_DIR / f"latest_{prefix}.json"
    payload = {"rows": [record_to_dict(r) for r in records], "regime": regime_to_dict(regime)}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    logger.info("저장: %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(description="기술적 신호 스크리너")
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="KR")
    parser.add_argument("--limit", type=int, default=None, help="개발/테스트용: 유니버스 앞에서 N종목만")
    args = parser.parse_args()

    markets = ["KR", "US"] if args.market == "ALL" else [args.market]
    for m in markets:
        run_market(m, limit=args.limit)


if __name__ == "__main__":
    main()
