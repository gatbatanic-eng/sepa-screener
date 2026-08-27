"""RANGE-MR / V-REBOUND 스크리닝 결과를 정적 HTML 대시보드로 만든다.

- 입력: data/screener.db (run_daily_screen.py가 이미 채워둔 SQLite)
- 출력: ../docs/range_vrebound/index.html — SEPA 대시보드(../docs/index.html)와
  경로가 겹치지 않는 하위 폴더. GitHub Pages가 docs/를 서빙하므로
  /range_vrebound/ 경로로 접근할 수 있다.
- SEPA와 달리 별도 스냅샷/이력 JSON을 두지 않는다 — SQLite DB 자체가
  매 실행마다 저장소에 커밋되어 전체 이력을 이미 갖고 있다(Phase 9).

실행 방법
---------
    python run_daily_screen.py       # 먼저 스크리닝 실행
    python generate_dashboard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

from src.dashboard import build
from src.storage import DEFAULT_DB_PATH, get_engine, get_session_factory, query_signals

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR.parent / "docs" / "range_vrebound" / "index.html"


def main() -> None:
    if not Path(DEFAULT_DB_PATH).exists():
        print(f"저장된 결과가 없습니다: {DEFAULT_DB_PATH} (run_daily_screen.py를 먼저 실행하세요)")
        return

    engine = get_engine(DEFAULT_DB_PATH)
    session = get_session_factory(engine)()
    try:
        signals = query_signals(session)
    finally:
        session.close()

    if not signals:
        print("저장된 신호가 없습니다.")
        return

    build(signals, OUTPUT_PATH)
    print(f"대시보드 생성 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
