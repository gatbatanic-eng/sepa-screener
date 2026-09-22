"""모멘텀 전략 스크리너 실행 스크립트.

사용법 (momentum_signals/ 안에서)
----------------------------------
    python run_daily_screen.py              # 국내+미국 통합 1회 실행
    python run_daily_screen.py --limit 30    # 개발/테스트용: 유니버스 앞에서 N종목만

이 서브시스템은 상태가 없는 다른 스크리너들과 달리 보유 포지션을 날짜를
넘어 추적해야 해서, output/ 스크래치 없이 ../docs/momentum/data/*.json을
직접 읽고 쓴다(positions.json·history.json은 그 자체가 누적 상태다).
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import portfolio as pf
from pipeline import run, serialize_result

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "docs" / "momentum" / "data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _trim_history(history: list[dict], max_sessions: int) -> list[dict]:
    dates = sorted({row["date"] for row in history}, reverse=True)[:max_sessions]
    keep = set(dates)
    return [row for row in history if row["date"] in keep]


def main() -> None:
    parser = argparse.ArgumentParser(description="모멘텀 전략 스크리너(국내+미국 통합)")
    parser.add_argument("--limit", type=int, default=None, help="개발/테스트용: 유니버스 앞에서 N종목만")
    args = parser.parse_args()

    positions_path = DATA_DIR / "positions.json"
    history_path = DATA_DIR / "history.json"
    latest_path = DATA_DIR / "latest.json"

    raw_positions = _load_json(positions_path, [])
    positions = [pf.position_from_dict(d) for d in raw_positions]
    history = _load_json(history_path, [])

    logger.info("=== 모멘텀 전략 스크리닝 시작 (보유 포지션 %d개) ===", pf.open_position_count(positions))
    result = run(positions=positions, limit=args.limit)
    logger.info("완료: 통합상위5=%d, 최종후보=%d, 신규진입=%d, 보유포지션=%d",
                len(result["top5"]), len(result["finalists"]), len(result["opened_today"]),
                pf.open_position_count(result["positions"]))

    payload = serialize_result(result)

    import config as cfg
    history.extend(payload["historyEntry"])
    history = _trim_history(history, cfg.HISTORY_MAX_SESSIONS)

    _save_json(positions_path, payload["positions"])
    _save_json(history_path, history)
    _save_json(latest_path, {k: v for k, v in payload.items()
                              if k not in ("positions",)})
    logger.info("저장: %s", DATA_DIR)


if __name__ == "__main__":
    main()
