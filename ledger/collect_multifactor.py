"""멀티팩터 실행 직후 호출: 이번 결과를 원장에 한 번 고정한다. (python -m ledger.collect_multifactor)"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from . import adapters, config


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", default=str(config.ROOT / "output" / "screening_result.csv"))
    args = p.parse_args()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    added = adapters.ingest_multifactor(Path(args.input), now)
    print(f"ledger: multifactor 신규 원장 파일 {added}개")


if __name__ == "__main__":
    main()
