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
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path

from funnel import prices as px
from funnel import rules, sectorheat, validation

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "data" / "funnel_manual.csv"
BASKET = ROOT / "data" / "ai_infra_basket.csv"
BENCHMARK = {"kr": "^KS11", "us": "^GSPC"}
UNIT = {"kr": 1e8, "us": 1e6}          # 수작업 P1 입력 단위: 한국 억원, 미국 백만$
MIN_MARCAP = {"kr": 500e8, "us": 3e8}
DETAIL_WORKERS = {"kr": 4, "us": 2}  # SEC는 초당 10건 제한이 있어 미국은 2건만 동시에


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=0), encoding="utf-8")

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
        price = (r["prices"] or {}).get("price")
        if market == "us":
            r["marcap"] = price * r["sharesNow"] if price and r.get("sharesNow") else None
            r["marcapEstimated"] = True
        elif price and r.get("listedShares"):
            # 목록 시총(장전에는 전일값·보완값)보다 상장주식수 × 최근 종가가 일관적이다.
            r["marcap"] = price * r["listedShares"]
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
              and results[r["symbol"]]["composite"] is not None and results[r["symbol"]]["meetsS1Floor"]]
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


def sector_heat(records: list[dict], results: dict[str, dict], market: str) -> dict:
    basket = sectorheat.load_basket(BASKET, market)
    by_symbol = {r["symbol"]: r for r in records}
    members = []
    for sym, info in basket.items():
        r = by_symbol.get(sym)
        if r is None:
            continue
        res = results[sym]
        members.append({"symbol": sym, "tier": info["tier"], "ret6m": (r.get("prices") or {}).get("ret6m"),
                        "opTTM": res["metrics"]["opTTM"], "shareGrowth": res["shareGrowth"],
                        "dilutionEvents12m": r.get("dilutionEvents12m")})
    heat = sectorheat.compute(members)
    heat["missing"] = sorted(set(basket) - set(by_symbol))
    return heat


def fmt_pct(v) -> str:
    return "N/A" if v is None else f"{v * 100:.0f}%"


def fmt_cap(v, market) -> str:
    if v is None:
        return "N/A"
    return f"{v / 1e8:,.0f}억" if market == "kr" else f"${v / 1e9:,.2f}B"


def write_report(path: Path, market: str, today: dt.date, top: list[dict], stats: dict, heat: dict) -> None:
    lines = [f"# 깔때기 스크리너 — {market.upper()} ({today.isoformat()})", "",
             "투자 추천이 아니라 검증·분석용 후보 목록입니다. S3·S5·P1·비중은 수작업 단계입니다.", "",
             f"- 유니버스 {stats['universe']}종목 → 관문 탈락 {stats['excluded']} / 점수 불가(실적 결측) {stats['noScore']} / "
             f"S1 최소 조건 미달 {stats['belowS1Floor']} / 순위 대상 {stats['ranked']}",
             f"- 상위 {len(top)} 중 사이클 업종(⚠P2, 정상 이익 기준 평가 필요) {stats['cyclicalInTop']}종목",
             f"- 관문 탈락 사유: {', '.join(f'{k} {v}' for k, v in sorted(stats['gateHits'].items())) or '없음'}",
             "", "## 관찰 지표 7번: 섹터 동반 급등 (AI 인프라 바스켓)", "",
             f"- 판정 {({'g': '양호', 'y': '주의', 'r': '경계'}).get(heat['level'], '미확인')} (경계 세부 {heat['hits']}개)",
             f"- A 6개월 +100% 이상 비율 {fmt_pct(heat['A_share100'])} · B 변두리−대장 {fmt_pct(heat['B_fringeMinusCore'])} · "
             f"C 적자−흑자 {fmt_pct(heat['C_lossMinusProfit'])} · D 주식 수 증가율 중앙값 {fmt_pct(heat['D_shareGrowthMedian'])}, "
             f"증자·CB 공시 종목 비율 {fmt_pct(heat['D_dilutionEventShare'])}",
             "- 6개월 상위: " + ", ".join("{}({}) {}".format(m["symbol"], m["tier"], fmt_pct(m["ret6m"])) for m in heat["topMovers"][:5]),
             "", "## 깔때기 상위 후보", "", "| # | 종목 | 시총 | 종합 | S1(이익 가속) | S2(이익의 질) | S6(자본 배분) | S4(소외도) | T1(타이밍) | 매출 YoY(최근→과거) | 근거 | P1(10배 산수) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in top:
        s = r["scores"]
        yoy = " → ".join(fmt_pct(v) for v in r["metrics"]["revYoY"])
        why = "; ".join(r["reasons"]["S1"] + r["reasons"]["S2"][:1])
        flag = "".join(" ⚠" + f["code"] for f in r["gates"]["flags"])
        flag += f" ⚠P2({r['cyclical']})" if r.get("cyclical") else ""
        p1 = r["P1"]["multipleX"] if r.get("P1") and r["P1"].get("multipleX") is not None else "—"
        cell = lambda v: "—" if v is None else f"{v:.0f}"  # noqa: E731
        lines.append(f"| {r['rank']} | {r['name']} ({r['symbol']}){flag} | {fmt_cap(r['marcap'], market)} | {r['composite']:.0f} | "
                     f"{cell(s['S1'])} | {cell(s['S2'])} | {cell(s['S6'])} | {cell(s['S4'])} | {r['T1']} | {yoy} | {why} | {p1} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_market(market: str, args, today: dt.date) -> None:
    from funnel import data_kr, data_us

    if market == "kr":
        from publish_fundamentals import Dart
        key = os.environ.get("DART_API_KEY", "").strip()
        if not key:
            raise RuntimeError("DART_API_KEY is not configured")
        api = Dart(key)
        records, _ = data_kr.collect(api, today, args.limit, ROOT / "research" / "funnel" / "cache" / "kr")
    else:
        api = None
        records = data_us.collect(today, args.limit)

    bench = BENCHMARK[market]
    series = px.download_weekly([r["yahoo"] for r in records] + [bench])
    bench_metrics = px.price_metrics(series[bench]) if bench in series else None
    universe_n = len(records)
    records = attach_prices(records, series, market)
    log.info("%s: 가격·시총 하한 통과 %d/%d", market, len(records), universe_n)

    manual = load_manual(market)
    results = evaluate_all(records)
    shortlist = rank(records, results)[:args.shortlist]
    detail_set = {r["symbol"] for r in shortlist} | set(manual)
    g3_set = {sym for sym, v in results.items() if any(h["code"] == "G3" for h in v["gates"]["hits"])}
    # 업종 조회(G3 해당: 금융업 판별 / 정밀 조회 대상: 사이클 태그)와 정밀 조회를 종목별로 병렬 처리.
    # 업종 코드는 거의 바뀌지 않아 캐시한다.
    industry_path = ROOT / "research" / "funnel" / "cache" / market / "industry.json"
    industry_cache = load_json(industry_path, {})

    def enrich(r: dict) -> None:
        sym = r["symbol"]
        if sym in detail_set | g3_set:
            code = industry_cache.get(sym)
            if code is None:
                if market == "kr":
                    code = data_kr.fetch_industry(api, r["corpCode"]) if r.get("corpCode") else None
                else:
                    code = data_us.fetch_sic(r["cik"])
                if code:
                    industry_cache[sym] = code
            r["industry"] = rules.classify_industry(market, code, sym)
        if sym not in detail_set:
            return
        # 정밀 조회(현금흐름·주식 수, 미국은 실제 회계분기 실적)는 1차 상위 + 추적 후보에만
        if market == "kr" and r.get("corpCode"):
            period = results[sym]["metrics"]["latestPeriod"]
            if period:
                y, q = int(period[:4]), int(period[-1])
                r.update({k: v for k, v in data_kr.enrich_detail(api, r["corpCode"], y, q).items() if v is not None})
        elif market == "us":
            detail = data_us.enrich_detail(r["cik"], today)
            r.update({k: v for k, v in detail.items() if v is not None})
            price = (r.get("prices") or {}).get("price")
            if detail.get("sharesNow") and price:
                r["marcap"] = price * detail["sharesNow"]

    targets = [r for r in records if r["symbol"] in detail_set | g3_set]
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS[market]) as pool:
        list(pool.map(enrich, targets))
    save_json(industry_path, industry_cache)
    log.info("%s: 업종·정밀 조회 %d종목 완료 (업종 캐시 %d)", market, len(targets), len(industry_cache))
    results = evaluate_all(records)
    for r in records:
        apply_manual(r, manual.get(r["symbol"]), market)

    ranked = rank(records, results)
    if not ranked:
        raise RuntimeError(f"{market}: 순위 대상 0종목 — 입력 데이터 이상으로 보고 결과를 기록하지 않음")
    ranks = {r["symbol"]: i + 1 for i, r in enumerate(ranked)}
    top = [row_out(r, results[r["symbol"]], ranks[r["symbol"]]) for r in ranked[:args.top]]
    stats = {
        "universe": len(records),
        "excluded": sum(1 for v in results.values() if v["gates"]["excluded"]),
        "noScore": sum(1 for v in results.values() if not v["gates"]["excluded"] and v["composite"] is None),
        "belowS1Floor": sum(1 for v in results.values() if not v["gates"]["excluded"] and v["composite"] is not None
                            and not v["meetsS1Floor"]),
        "cyclicalInTop": sum(1 for t in top if t.get("cyclical")),
        "ranked": len(ranked),
        "gateHits": dict(Counter(h["code"] for v in results.values() for h in v["gates"]["hits"])),
    }
    manual_rows = [row_out(r, results[r["symbol"]], ranks.get(r["symbol"])) for r in records
                   if r.get("manual") and r["symbol"] not in {t["symbol"] for t in top}]

    heat = sector_heat(records, results, market)
    doc = {"schemaVersion": 1, "market": market, "recordedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
           "note": "투자 추천이 아닌 검증·분석용 규칙 판정. 결측은 null.",
           "thresholds": {"minMarcap": MIN_MARCAP[market], "weights": rules.WEIGHTS, "P1Cutoff": rules.P1_CUTOFF},
           "stats": stats, "sectorHeat": heat, "top": top, "manualTracked": manual_rows,
           "excludedSample": [{"symbol": r["symbol"], "name": r["name"], "hits": results[r["symbol"]]["gates"]["hits"]}
                              for r in sorted(records, key=lambda r: -(r.get("marcap") or 0))
                              if results[r["symbol"]]["gates"]["excluded"]][:100]}

    base = ROOT / "research" / "funnel" / market
    write_report(base / "report.md", market, today, top, stats, heat)
    snap = {"recordedAt": doc["recordedAt"], "market": market, "topK": args.top,
            "benchmark": {"symbol": bench, "price": (bench_metrics or {}).get("price")},
            "sectorHeat": {k: heat[k] for k in ("level", "hits", "flags", "A_share100", "B_fringeMinusCore",
                                                 "C_lossMinusProfit", "D_shareGrowthMedian", "D_dilutionEventShare")},
            "rows": [{"symbol": r["symbol"], "price": (r.get("prices") or {}).get("price"), "rank": ranks.get(r["symbol"]),
                      "composite": results[r["symbol"]]["composite"], "excluded": results[r["symbol"]]["gates"]["excluded"],
                      "meetsS1Floor": results[r["symbol"]]["meetsS1Floor"],
                      "T1": results[r["symbol"]]["T1"], "exchange": r.get("exchange")} for r in records]}
    snap_dir = base / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(snap_dir / f"{today.isoformat()}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
    current = {r["symbol"]: (r.get("prices") or {}).get("price") for r in records}
    doc["validation"] = validation.update(snap_dir, base / "validation.json", current,
                                          (bench_metrics or {}).get("price"), today, args.top)
    public = ROOT / "docs" / "research" / f"funnel_{market}.json"
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
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
