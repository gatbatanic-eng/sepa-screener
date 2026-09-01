"""RANGE-MR / V-REBOUND 스크리닝 결과를 정적 HTML 대시보드로 만든다.

- 입력: data/screener.db (run_daily_screen.py가 이미 채워둔 SQLite)
- 출력: ../docs/range_vrebound/index.html — SEPA 대시보드(../docs/index.html)와
  경로가 겹치지 않는 하위 폴더. GitHub Pages가 docs/를 서빙하므로
  /range_vrebound/ 경로로 접근할 수 있다.
- SEPA와 달리 별도 스냅샷/이력 JSON을 두지 않는다 — SQLite DB 자체가
  매 실행마다 저장소에 커밋되어 전체 이력을 이미 갖고 있다(Phase 9).
- 현재 화면에 뜨는(필터를 통과한) 종목마다 차트용 시세 데이터를 추가로
  조회한다(네트워크 호출) — 전체 유니버스가 아니라 표시되는 종목만
  대상이라 호출 수가 크지 않다.

실행 방법
---------
    python run_daily_screen.py       # 먼저 스크리닝 실행
    python generate_dashboard.py
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

from src.config import load_config
from src.dashboard import attach_charts, build_chart_data, build_payload, collect_chart_symbols, render_html
from src.data.loader import fetch_ohlcv
from src.screening import bars_to_frame
from src.storage import DEFAULT_DB_PATH, get_engine, get_session_factory, query_signals

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR.parent / "docs" / "range_vrebound" / "index.html"


def fetch_charts(symbols: list[str]) -> dict[str, dict]:
    """종목마다 차트용 시세 데이터를 조회한다 (네트워크 호출).

    한 종목이 실패해도 나머지는 계속 진행한다 — 실패한 종목은 차트 없이
    표만 보여준다(개발 원칙 33조: 조용히 무시하지 않고 로그를 남긴다).
    """
    config = load_config()
    end = date.today()
    start = end - timedelta(days=config.data.history_calendar_days)

    charts: dict[str, dict] = {}
    for symbol in symbols:
        try:
            bars = fetch_ohlcv(symbol, start, end)
            df = bars_to_frame(bars)
            charts[symbol] = build_chart_data(df)
        except Exception:
            logger.exception("%s 차트 데이터 조회 실패 — 차트 없이 진행", symbol)
    return charts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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

    payload = build_payload(signals)

    symbols = collect_chart_symbols(payload)
    logger.info("차트 데이터 조회 중 (%d종목)...", len(symbols))
    charts = fetch_charts(symbols)
    attach_charts(payload, charts)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_html(payload), encoding="utf-8")
    print(f"대시보드 생성 완료: {OUTPUT_PATH} (차트 {len(charts)}/{len(symbols)}종목)")


if __name__ == "__main__":
    main()
