"""기술적 신호 실행 직후 호출: 이번 판정을 원장에 한 번 고정한다. (python -m ledger.collect_technical --market KR|US|ALL)"""
from __future__ import annotations

import argparse
import datetime as dt

from . import adapters, config


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--market", default="ALL", choices=["KR", "US", "ALL"])
    args = p.parse_args()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    markets = ("kr", "us") if args.market == "ALL" else (args.market.lower(),)
    for market in markets:
        added = adapters.ingest_technical(market, config.ROOT / "docs" / "technical" / "data" / f"latest_{market}.json", now)
        print(f"ledger: technical/{market} 신규 원장 파일 {added}개")


if __name__ == "__main__":
    main()
