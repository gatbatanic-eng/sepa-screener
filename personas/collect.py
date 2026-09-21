"""personas/collect.py — 페르소나 입력 데이터 수집: 밸류에이션(Yahoo) + 성과 트래커 요약/점검.

    python -m personas.collect --market all

하는 일
-------
1. (미국) 추세통과 종목의 Yahoo 밸류에이션을 ``docs/data/valuation_us.json`` 캐시에 갱신한다.
   실패한 종목은 이전 값을 지우지 않고 stale 로 남긴다.
2. (한국/미국) 성과 트래커를 읽어 종목당 1개로 중복을 정리한 기저율과 수집 누락 점검 결과를
   ``docs/data/track_record_{kr,us}.json`` 에 쓴다.
3. 문제는 조용히 넘기지 않는다: 경고는 전부 출력(GitHub Actions 에서는 ``::warning::`` 주석)하고,
   트래커 파일이 없거나 밸류에이션이 전부 실패하면 종료 코드 1 을 돌려준다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from personas import track_record, valuation

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def collect_market(market: str, root: Path = ROOT, *, skip_valuation: bool = False,
                   fetcher: Optional[Callable] = None, today: Optional[dt.date] = None,
                   write: bool = True) -> dict:
    m = market.lower()
    result: dict = {"market": m, "problems": [], "warnings": []}

    if m == "us" and not skip_valuation:
        symbols = valuation.trend_symbols("us", root)
        cache_path = root / "docs" / "data" / "valuation_us.json"
        summ = valuation.collect(symbols, cache_path, fetcher=fetcher or valuation.fetch_one, write=write)
        result["valuation"] = summ
        if summ["requested"] and summ["fetched"] + summ["cached"] == 0:
            result["problems"].append(f"US 밸류에이션이 전부 실패({summ['requested']}개 요청) — Yahoo 접근 확인 필요")
        elif summ["failed"]:
            sample = ", ".join(f"{k}: {v}" for k, v in list(summ["failures"].items())[:5])
            result["warnings"].append(f"US 밸류에이션 {summ['failed']}개 실패(이전 값 유지 {summ['staleKept']}개): {sample}")

    state = track_record.load_state(m, root)
    rates = {g: track_record.build_base_rate(state, m, g) for g in track_record.GROUPS}
    audit = track_record.audit(state, m, today=today)
    result["baseRates"], result["audit"] = rates, audit
    for issue in audit["issues"]:
        if issue["level"] == "error":
            result["problems"].append(issue["message"])
        elif issue["level"] == "warn":
            result["warnings"].append(issue["message"])
    if write:
        generated = dt.datetime.now(dt.timezone.utc).isoformat()
        _write_json(root / "docs" / "data" / f"track_record_{m}.json",
                    {"schemaVersion": 1, "market": m, "generatedAt": generated,
                     "baseRates": rates, "audit": audit})
    return result


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="페르소나 입력 데이터 수집")
    ap.add_argument("--market", choices=["kr", "us", "all"], default="all")
    ap.add_argument("--skip-valuation", action="store_true", help="Yahoo 밸류에이션 수집 생략")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 결과만 출력")
    a = ap.parse_args(argv)
    on_actions = bool(os.environ.get("GITHUB_ACTIONS"))
    exit_code = 0
    for m in (["kr", "us"] if a.market == "all" else [a.market]):
        r = collect_market(m, skip_valuation=a.skip_valuation, write=not a.dry_run)
        v = r.get("valuation")
        if v:
            print(f"[{m.upper()}] 밸류에이션: 요청 {v['requested']} | 새로 받음 {v['fetched']} | 캐시 재사용 {v['cached']} | 실패 {v['failed']}")
        tr = r["baseRates"]["TREND"]
        print(f"[{m.upper()}] 성과 트래커 TREND: 상태={tr['status']}"
              + (f" — {tr['reason']}" if tr.get("reason") else "")
              + f" | 원본 {tr.get('raw', 0)} → 고유 {tr.get('unique', 0)}")
        for msg in r["warnings"]:
            print(f"  경고: {msg}")
            if on_actions:
                print(f"::warning::[personas/{m}] {msg}")
        for msg in r["problems"]:
            print(f"  문제: {msg}", file=sys.stderr)
            if on_actions:
                print(f"::error::[personas/{m}] {msg}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
