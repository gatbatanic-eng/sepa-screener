"""신호 행의 공통 형식과 날짜 규칙.

신호 행: {id, strategy, market, group, date(유효 거래일), symbol, name, price, exchange, rank, score, outcomes?}
  - market: 'kr' | 'us'
  - exchange: KOSPI | KOSDAQ | US (모르면 None → 가격 조회 때 확인)
"""
from __future__ import annotations

import datetime as dt
import random

from .config import CLOSE_FINAL_UTC_HOUR, CONTROL, CONTROL_PER_DATE


def effective_date(recorded_at: str, market: str) -> dt.date:
    """기록 시각 기준으로 가격이 가리키는 거래일(주말·휴일은 가격 조회 때 직전 거래일로 맞춘다)."""
    t = dt.datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    t = t.astimezone(dt.timezone.utc)
    day = t.date()
    return day if t.hour >= CLOSE_FINAL_UTC_HOUR[market] else day - dt.timedelta(days=1)


def signal_id(strategy: str, market: str, file_date: str, symbol: str, group: str) -> str:
    return f"{strategy}:{market}:{file_date}:{symbol}:{group}"


def control_sample(strategy: str, market: str, date: str, symbols: list[str]) -> list[str]:
    """같은 날·같은 유니버스에서 신호와 무관하게 뽑는 대조군. 날짜로 시드를 고정해 재현된다."""
    pool = sorted(set(symbols))
    if len(pool) <= CONTROL_PER_DATE:
        return pool
    return sorted(random.Random(f"{strategy}:{market}:{date}").sample(pool, CONTROL_PER_DATE))


def expand_file(strategy: str, market: str, file_date: str, doc: dict) -> list[dict]:
    """원장 파일 하나(행마다 groups 목록) → 그룹별 신호 행."""
    out = []
    for r in doc["rows"]:
        for group in r["groups"]:
            out.append({
                "id": signal_id(strategy, market, file_date, r["symbol"], group),
                "strategy": strategy, "market": market, "group": group,
                "date": doc["effectiveDate"], "symbol": r["symbol"], "name": r.get("name"),
                "price": r.get("price"), "exchange": r.get("exchange"),
                "rank": r.get("rank"), "score": r.get("score"),
            })
    return out


def is_control(group: str) -> bool:
    return group == CONTROL
