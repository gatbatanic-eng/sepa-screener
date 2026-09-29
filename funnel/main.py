"""깔때기 스크리너 CLI.

    python -m funnel.main --market kr|us|all [--limit N] [--shortlist 150] [--top 50]

산출물
- docs/research/funnel_{market}.json   최신 결과(관문 통과 상위 + 탈락 사유 요약)
- research/funnel/{market}/report.md   사람이 읽는 요약
- research/funnel/{market}/snapshots/YYYY-MM-DD.json.gz   기록 시점 스냅샷(수정 금지)
- research/funnel/{market}/validation.json   스냅샷별 1·3·6·12개월 성과
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import logging
import os
from collections import Counter
from pathlib import Path

from funnel import prices as px
from funnel import rules, validation

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "data" / "funnel_manual.csv"
BENCHMARK = {"kr": "^KS11", "us": "^GSPC"}
UNIT = {"kr": 1e8, "us": 1e6}          # 수작업 P1 입력 단위: 한국 억원, 미국 백만$
MIN_MARCAP = {"kr": 500e8, "us": 3e8}

log = logging.getLogger("funnel")


def load_manual(market: str) -> dict[str, dict]:
    """수작업 평가(S3·S5·P1). 같은 종목은 가장 최근 recordedAt 행을 쓴다."""
    if not MANUAL.exists():
        return {}
    out: dict[str, dict] = {}
    with MANUAL.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("market") != market or not row.get("symbol"):
                continue
            prev = out.get(row["symbol"])
            if prev is None or row.get("recordedAt", "") >= prev.get("recordedAt", ""):
                out[row["symbol"]] = row
    return out


def apply_manual(row: dict, manual: dict | None, market: str) -> None:
    if not manual:
        return
    num = rules.finite
    tam, other = num(manual.get("tam")), num(manual.get("other"))
    p1 = rules.p1_multiple(tam * UNIT[market] if tam is not None else None, num(manual.get("share")),
                           num(manual.get("margin")), num(manual.get("multiple")), row.get("marcap"),
                           other * UNIT[market] if other is not None else None)
    row["manual"] = {k: manual.get(k) for k in ("recordedAt", "S3", "S5", "tam", "share", "margin", "multiple", "other", "note")}
    row["P1"] = p1


def attach_prices(records: list[dict], series: dict, market: str) -> list[dict]:
    kept = []
    for r in records:
        s = series.get(r["yahoo"])
        r["prices"] = px.price_metrics(s) if s is not None else None
        if market == "us":
            price = (r["prices"] or {}).get("price")
            r["marcap"] = price * r["sharesNow"] if price and r.get("sharesNow") else None
            r["marcapEstimated"] = True
        if r.get("marcap") is None or r["marcap"] < MIN_MARCAP[market]:
            continue
        kept.append(r)
    return kept


def evaluate_all(records: list[dict]) -> dict[str, dict]:
    past = {r["symbol"]: (r.get("prices") or {}).get("ret36to6m") for r in records}
    pct = rules.percentile_ranks(past)
    return {r["symbol"]: rules.evaluate(r, pct[r["symbol"]]) for r in records}


def rank(records: list[dict], results: dict[str, dict]) -> list[dict]:
    passed = [r for r in records if not results[r["symbol"]]["gates"]["excluded"]
              and results[r["symbol"]]["composite"] is not None]
    return sorted(passed, key=lambda r: -results[r["symbol"]]["composite"])


def row_out(r: dict, res: dict, rank_no: int | None) -> dict:
    p = r.get("prices") or {}
    return {
        "rank": rank_no, "symbol": r["symbol"], "name": r["name"], "exchange": r.get("exchange"),
        "marcap": r.get("marcap"), "marcapEstimated": r.get("marcapEstimated", False),
        "price": p.get("price"), "priceDate": p.get("priceDate"),
        "high52Ratio": p.get("high52Ratio"), "ret6m": p.get("ret6m"), "ret36to6m": p.get("ret36to6m"),
        **res,
        "manual": r.get("manual"), "P1": r.get("P1"),
    }


def fmt_pct(v) -> str:
    return "N/A" if v is None else f"{v * 100:.0f}%"


def fmt_cap(v, market) -> str:
    if v is None:
        return "N/A"
    return f"{v / 1e8:,.0f}억" if market == "kr" else f"${v / 1e9:,.2f}B"


def write_report(path: Path, market: str, today: dt.date, top: list[dict], stats: dict) -> None:
    lines = [f"# 깔때기 스크리너 — {market.upper()} ({today.isoformat()})", "",
             "투자 추천이 아니라 검증·분석용 후보 목록입니다. S3·S5·P1·비중은 수작업 단계입니다.", "",
             f"- 유니버스 {stats['universe']}종목 → 관문 탈락 {stats['excluded']} / 점수 불가(실적 결측) {stats['noScore']} / 순위 대상 {stats['ranked']}",
             f"- 관문 탈락 사유: {', '.join(f'{k} {v}' for k, v in sorted(stats['gateHits'].items())) or '없음'}",
             "", "| # | 종목 | 시총 | 종합 | S1 | S2 | S6 | S4 | T1 | 매출 YoY(최근→) | 근거 | P1 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in top:
        s = r["scores"]
        yoy = " → ".join(fmt_pct(v) for v in r["metrics"]["revYoY"])
        why = "; ".join(r["reasons"]["S1"] + r["reasons"]["S2"][:1])
        flag = " ⚠G4" if r["gates"]["flags"] else ""
        p1 = r["P1"]["multipleX"] if r.get("P1") and r["P1"].get("multipleX") is not None else "—"
        cell = lambda v: "—" if v is None else f"{v:.0f}"  # noqa: E731
        lines.append(f"| {r['rank']} | {r['name']} ({r['symbol']}){flag} | {fmt_cap(r['marcap'], market)} | {r['composite']:.0f} | "
                     f"{cell(s['S1'])} | {cell(s['S2'])} | {cell(s['S6'])} | {cell(s['S4'])} | {r['T1']} | {yoy} | {why} | {p1} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_market(market: str, args, today: dt.date) -> None:
    if market == "kr":
        from funnel import data_kr
        from publish_fundamentals import Dart
        key = os.environ.get("DART_API_KEY", "").strip()
        if not key:
            raise RuntimeError("DART_API_KEY is not configured")
        api = Dart(key)
        records, _ = data_kr.collect(api, today, args.limit)
    else:
        from funnel import data_us
        records = data_us.collect(today, args.limit)

    bench = BENCHMARK[market]
    series = px.download_weekly([r["yahoo"] for r in records] + [bench])
    bench_metrics = px.price_metrics(series[bench]) if bench in series else None
    universe_n = len(records)
    records = attach_prices(records, series, market)
    log.info("%s: 가격·시총 하한 통과 %d/%d", market, len(records), universe_n)

    results = evaluate_all(records)
    shortlist = rank(records, results)[:args.shortlist]
    # 정밀 조회(현금흐름·주식 수)는 1차 상위 후보에만
    for r in shortlist:
        if market == "kr" and r.get("corpCode"):
            from funnel import data_kr
            period = results[r["symbol"]]["metrics"]["latestPeriod"]
            if period:
                y, q = int(period[:4]), int(period[-1])
                r.update({k: v for k, v in data_kr.enrich_detail(api, r["corpCode"], y, q).items() if v is not None})
        elif market == "us":
            from funnel import data_us
            detail = data_us.enrich_detail(r["cik"], today)
            r.update({k: v for k, v in detail.items() if v is not None})
            price = (r.get("prices") or {}).get("price")
            if detail.get("sharesNow") and price:
                r["marcap"] = price * detail["sharesNow"]
    results = evaluate_all(records)
    manual = load_manual(market)
    for r in records:
        apply_manual(r, manual.get(r["symbol"]), market)

    ranked = rank(records, results)
    ranks = {r["symbol"]: i + 1 for i, r in enumerate(ranked)}
    top = [row_out(r, results[r["symbol"]], ranks[r["symbol"]]) for r in ranked[:args.top]]
    stats = {
        "universe": len(records),
        "excluded": sum(1 for v in results.values() if v["gates"]["excluded"]),
        "noScore": sum(1 for v in results.values() if not v["gates"]["excluded"] and v["composite"] is None),
        "ranked": len(ranked),
        "gateHits": dict(Counter(h["code"] for v in results.values() for h in v["gates"]["hits"])),
    }
    manual_rows = [row_out(r, results[r["symbol"]], ranks.get(r["symbol"])) for r in records
                   if r.get("manual") and r["symbol"] not in {t["symbol"] for t in top}]

    doc = {"schemaVersion": 1, "market": market, "recordedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
           "note": "투자 추천이 아닌 검증·분석용 규칙 판정. 결측은 null.",
           "thresholds": {"minMarcap": MIN_MARCAP[market], "weights": rules.WEIGHTS, "P1Cutoff": rules.P1_CUTOFF},
           "stats": stats, "top": top, "manualTracked": manual_rows,
           "excludedSample": [{"symbol": r["symbol"], "name": r["name"], "hits": results[r["symbol"]]["gates"]["hits"]}
                              for r in sorted(records, key=lambda r: -(r.get("marcap") or 0))
                              if results[r["symbol"]]["gates"]["excluded"]][:100]}
    public = ROOT / "docs" / "research" / f"funnel_{market}.json"
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")

    base = ROOT / "research" / "funnel" / market
    write_report(base / "report.md", market, today, top, stats)
    snap = {"recordedAt": doc["recordedAt"], "market": market, "topK": args.top,
            "benchmark": {"symbol": bench, "price": (bench_metrics or {}).get("price")},
            "rows": [{"symbol": r["symbol"], "price": (r.get("prices") or {}).get("price"), "rank": ranks.get(r["symbol"]),
                      "composite": results[r["symbol"]]["composite"], "excluded": results[r["symbol"]]["gates"]["excluded"],
                      "T1": results[r["symbol"]]["T1"]} for r in records]}
    snap_dir = base / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(snap_dir / f"{today.isoformat()}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
    current = {r["symbol"]: (r.get("prices") or {}).get("price") for r in records}
    validation.update(snap_dir, base / "validation.json", current,
                      (bench_metrics or {}).get("price"), today, args.top)
    log.info("%s 완료: 순위 대상 %d, 상위 %d 기록", market, len(ranked), len(top))


def main() -> None:
    parser = argparse.ArgumentParser(description="대박주 선별 깔때기 스크리너")
    parser.add_argument("--market", choices=["kr", "us", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="점검용: 유니버스 일부만")
    parser.add_argument("--shortlist", type=int, default=150, help="정밀 조회 대상 수")
    parser.add_argument("--top", type=int, default=50, help="기록할 상위 후보 수")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    today = dt.date.today()
    failed = []
    for market in (["kr", "us"] if args.market == "all" else [args.market]):
        try:
            run_market(market, args, today)
        except Exception:  # noqa: BLE001 - 한 시장 실패가 다른 시장을 막지 않게
            log.exception("%s 실행 실패", market)
            failed.append(market)
    if failed:
        raise SystemExit(f"실패 시장: {', '.join(failed)}")


if __name__ == "__main__":
    main()
