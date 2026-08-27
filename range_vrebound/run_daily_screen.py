"""RANGE-MR / V-REBOUND 일일 스크리닝 실행 스크립트.

사용법
------
    python run_daily_screen.py                  # 기본: CONFIG의 유니버스 전체
    python run_daily_screen.py --limit 20        # 개발/테스트용: 앞에서 20종목만
    python run_daily_screen.py --top-n 100       # 시총 상위 100종목만
    python run_daily_screen.py --db path/to.db   # 저장 위치 지정

기존 SEPA 스크리너(screening.py)와 완전히 분리되어 있다 — 이 스크립트는
range_vrebound/ 안의 코드만 쓴다.
"""
from __future__ import annotations

import argparse
import logging
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

from src.screening import run_daily_screen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=None, help="시가총액 상위 몇 종목 (기본: CONFIG 값)")
    parser.add_argument("--limit", type=int, default=None, help="개발/테스트용: 유니버스 앞에서 N종목만")
    parser.add_argument("--db", type=str, default=None, help="SQLite 저장 경로 (기본: data/screener.db)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    summary = run_daily_screen(top_n=args.top_n, limit=args.limit, db_path=args.db)
    print(summary)


if __name__ == "__main__":
    main()
