"""
run_macro.py — 하루 한 번 실행되는 진입점

  python run_macro.py                 실데이터 수집 → 규칙 → Claude 요약 → docs/macro.json + 구글시트
  python run_macro.py --mock stress   합성 데이터로 전체 파이프라인 테스트
  python run_macro.py --no-claude     Claude 요약 생략

출력:
  docs/macro.json      대시보드(macro.html / macro-card.js)가 읽는 파일
  data/macro_history.csv
"""
from __future__ import annotations

import os
import sys
import logging

# Windows 콘솔(cp949 등)에서도 한글/em-dash 출력이 깨지지 않도록 UTF-8 강제 (screening.py 와 동일)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

from fetch_macro import load_history, build_snapshot
from interpret import interpret, to_json
import sheets

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.environ.get("MACRO_JSON_PATH", os.path.join(HERE, "docs", "macro.json"))


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    mock = None
    if "--mock" in argv:
        i = argv.index("--mock")
        mock = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("-") else "calm"
    use_claude = "--no-claude" not in argv

    df = load_history(mock)
    snapshot = build_snapshot(df)
    logging.info("지표 %d개 수집: %s", len(snapshot), ", ".join(snapshot))

    result = interpret(snapshot, use_claude=use_claude)
    if mock:
        result["mock"] = mock
    to_json(result, OUT_JSON)
    logging.info("→ %s", OUT_JSON)

    if not mock:
        sheets.save(result)

    print("\n" + "=" * 60)
    print(f"[{result['generated_at']}]  국면: {result['regime_label']}  (점수 {result['score']:+d})")
    print(f"SEPA: {result['sepa_note']}")
    print("-" * 60)
    for s in result["signals"]:
        print(f"  {s['id']:5s} {s['score']:+d}  {s['message']}")
    if not result["signals"]:
        print("  특이 신호 없음")
    if result.get("summary"):
        print("-" * 60)
        print(result["summary"])
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
