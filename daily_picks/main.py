"""오늘의 추천: 시장별 하루 한 번 기록(수정 금지) + 공개 JSON + 알림용 마크다운. 주문하지 않는다."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path

from . import config as C
from .entry import fetch_entry_metrics
from . import context as X, v2 as V2
from .pool import CORE, RESEARCH, ROOT, load
from .select import needs_direct, pick

log = logging.getLogger("daily_picks")
NAMES = {**CORE, **RESEARCH}
MARKET_LABEL = {"kr": "한국", "us": "미국"}


def _yahoo_hint(code: str, pool: dict) -> str:
    ex = (pool["funnel"].get(code, {}).get("exchange") or pool["sepaRows"].get(code, {}).get("market") or "").upper()
    return code + ".KQ" if ex.startswith("KOSDAQ") else code + ".KS" if ex.startswith("KOSPI") else code


def build_market(market: str, root: Path, fetch=fetch_entry_metrics, fetch_sector=None) -> dict:
    pool = load(market, root)
    core_codes = [c for c, s in pool["selected"].items() if set(s) & set(CORE)]
    missing = [c for c in core_codes if needs_direct(c, pool)]
    direct = fetch(missing, {c: _yahoo_hint(c, pool) for c in missing}) if missing else {}
    out = pick(market, pool, direct)
    out["session"] = pool["asOf"]
    rows = out.pop("allRows")
    out["poolPrices"] = {r["code"]: r["price"] for r in rows if r.get("price")}
    out["directComputed"] = sorted(direct)
    out["entryUnknown"] = len(missing) - len(direct)
    out["rules"] = {"maxRiskPct": C.MAX_RISK_PCT, "stopLossPct": C.STOP_LOSS_PCT, "holdDays": C.HOLD_DAYS, "maxPicks": C.MAX_PICKS}
    # v2: 업종 강도·시장 환경. 미국 업종은 Yahoo 캐시(없는 종목만 받는다), 한국은 NHPLUG 업종 파일.
    if market == "us" and fetch_sector is not False:
        try:
            X.fetch_us_sectors(list(pool["sepaRows"]) + core_codes, root, fetch=fetch_sector)
        except Exception:  # noqa: BLE001 — 업종을 못 받아도 v1·v2(업종 0점)는 낸다
            log.exception("미국 업종 수집 실패")
    sectors = X.sector_map(market, root)
    ctx = {"sectorStats": X.sector_strength(pool["sepaRows"], sectors), "regimes": X.market_regimes(pool["sepaRows"]),
           "macro": X.macro_summary(root), "krClose": X.kr_close_context(pool["asOf"], root) if market == "kr" else {}}
    v2 = V2.rank(rows, ctx, sectors, market)
    out["picksV2"] = v2["picks"]
    rs = {c: r.get("rsRank") for c, r in pool["sepaRows"].items()}
    v3 = V2.rank_v3(rows, ctx, sectors, market, rs)
    out["picksV3"] = v3["picks"]
    out["v3"] = {"candidates": v3["candidates"], "skipped": v3["skipped"]}
    v4 = V2.rank_v4(rows, ctx, sectors, market, pool["sepaRows"], lambda c: X.atr_pct_from_chart(c, root))
    out["picksV4"] = v4["picks"]
    out["v4"] = {k: v4[k] for k in ("candidates", "missingAtr", "skipped") if k in v4}
    out["context"] = {"regimes": ctx["regimes"], "macro": ctx["macro"],
                      "sectorCoverage": {"known": sum(1 for r in rows if sectors.get(r["code"])), "candidates": len(rows)},
                      "sectors": sorted(({"sector": s, **v} for s, v in ctx["sectorStats"].items()), key=lambda x: x["rank"])}
    return out


def record_path(root: Path, market: str, session: str) -> Path:
    return root / "research" / "daily_picks" / market / f"{session}.json"


def write_once(root: Path, market: str, rec: dict, now: dt.datetime) -> bool:
    if not rec.get("session"):
        log.warning("%s: 기준 거래일을 알 수 없어 기록하지 않습니다", market)
        return False
    path = record_path(root, market, rec["session"])
    if path.exists():
        return False
    rec = dict(rec, recordedAt=now.isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def render_md(recs: dict[str, dict]) -> str:
    lines = ["## 오늘의 추천 (참고용, 투자 권유 아님)", ""]
    for m in ("kr", "us"):
        r = recs.get(m)
        if not r:
            continue
        regs = ", ".join(f"{k} {v}" for k, v in (r.get("context") or {}).get("regimes", {}).items()) or "-"
        lines.append(f"### {MARKET_LABEL[m]} · 기준 {r['session']} · 시장 {regs}")
        picks = r.get("picksV2") or []
        for i, p in enumerate(picks, 1):
            lines.append(f"{i}. **{p['name']}** ({p['code']}) 종합 {p['v2']['score']}점 (v1 {p['score']})")
            lines += [f"   - {w['text']}" for w in p.get("why", [])]
        if not picks:
            lines.append("- 오늘은 진입 적합 종목이 없습니다.")
        v1 = [p["code"] for p in r["picks"]]
        v2 = [p["code"] for p in picks]
        if v1 != v2:
            lines.append(f"- 참고(v1, 섹터 미반영): {', '.join(p['name'] for p in r['picks']) or '없음'}")
        v3 = r.get("picksV3") or []
        if v3:
            lines.append("- 실험 v3(손절폭 필터 없이 비중 조절): " + ", ".join(f"{p['name']}(비중 {p['weight']:.0%}, 손절폭 {p['entry']['riskPct']}%)" for p in v3))
        v4 = r.get("picksV4") or []
        if v4:
            lines.append("- 실험 v4(추세 통과 + 변동성 3% 이상, 미국 전용): " + ", ".join(f"{p['name']}(ATR {p['atrPct']}%, 비중 {p['weight']:.0%})" for p in v4))
        if r.get("shortfall") and len(picks) < C.MAX_PICKS:
            lines.append(f"- {r['shortfall']}")
        if r["rejectSummary"]:
            lines.append("- 탈락 사유: " + ", ".join(f"{k} {v}" for k, v in r["rejectSummary"].items()))
        lines.append("")
    lines.append(f"보유 규칙: 종가 -{C.STOP_LOSS_PCT:g}% 이하 청산, {C.HOLD_DAYS}거래일. 장중 손절은 가정하지 않음. 지정학·뉴스 해석은 점수에 넣지 않음.")
    return "\n".join(lines) + "\n"


def history(root: Path, limit: int = 30) -> list[dict]:
    out = []
    for m in ("kr", "us"):
        for f in sorted((root / "research" / "daily_picks" / m).glob("*.json"))[-limit:]:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out.append({"market": m, "session": d.get("session"), "recordedAt": d.get("recordedAt"),
                        "picks": [{"code": p["code"], "name": p["name"], "score": p["v2"]["score"] if "v2" in p else p["score"], "riskPct": p["entry"]["riskPct"]}
                      for p in (d.get("picksV2") or d["picks"])],
                        "suitable": d["suitable"], "candidates": d["candidates"]})
    return sorted(out, key=lambda r: (r["session"] or "", r["market"]), reverse=True)


def run(root: Path = ROOT, now: dt.datetime | None = None, new_list: Path | None = None, fetch=fetch_entry_metrics, record: bool = True, fetch_sector=None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    recs, fresh = {}, {}
    for m in ("kr", "us"):
        try:
            recs[m] = build_market(m, root, fetch, fetch_sector)
        except Exception:  # noqa: BLE001 — 한 시장 실패가 다른 시장 기록을 막지 않게 한다
            log.exception("%s 추천 계산 실패", m)
            continue
        if record and write_once(root, m, recs[m], now):
            fresh[m] = recs[m]
    public = {"schemaVersion": 1, "generatedAt": now.isoformat(), "latest": recs, "history": history(root),
              "rules": {"maxPicks": C.MAX_PICKS, "maxRiskPct": C.MAX_RISK_PCT, "stopLossPct": C.STOP_LOSS_PCT, "holdDays": C.HOLD_DAYS},
              "strategyNames": NAMES, "coreStrategies": list(CORE)}
    out = root / "docs" / "research" / "daily_picks.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(public, ensure_ascii=False, indent=1), encoding="utf-8")
    if fresh and new_list:
        day = max(r["session"] for r in fresh.values())
        md = root / "research" / "daily_picks" / "reports" / f"{day}.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render_md(recs), encoding="utf-8")
        with open(new_list, "a", encoding="utf-8") as fh:
            fh.write(str(md) + "\n")
    return public


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--new-list", default=None)
    p.add_argument("--no-record", action="store_true", help="코드 변경 실행용: 기록·알림 없이 공개 JSON만 갱신")
    a = p.parse_args()
    run(new_list=Path(a.new_list) if a.new_list else None, record=not a.no_record)


if __name__ == "__main__":
    main()
