"""재무 수집: 퀀트 유니버스 전 종목의 분기 재무를 공시일과 함께 research/quant/fin 에 쌓는다. (python -m quant.collect)

기존 수집기(publish_fundamentals.py, publish_us_fundamentals.py)의 함수를 읽기 전용으로 재사용하고, 기존 산출물(docs/data/fundamentals,
research/fundamentals)은 건드리지 않는다. DART는 하루 호출 한도가 있어 실행당 호출 수 예산을 두고, 모자라면 다음 실행이 이어서 받는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import publish_fundamentals as dart_mod
import publish_us_fundamentals as sec_mod

from . import config, inputs
from .store import read_json, write_json

log = logging.getLogger("quant.collect")
KR_MIN_MARCAP_EOK = 700.0          # 수집 대상(적격 하한 1,000억보다 느슨하게: 시총 변동으로 들락거리는 종목 포함)
REFRESH_DAYS = 7                   # 이보다 오래된 파일만 다시 받는다(확정 분기는 캐시되므로 호출 수가 작다)


class BudgetExceeded(RuntimeError):
    pass


class CountingApi:
    """DART 호출 수를 세고 예산을 넘으면 멈춘다."""

    def __init__(self, api, limit: int):
        self.api, self.limit, self.count, self._lock = api, limit, 0, threading.Lock()

    def request(self, endpoint, **params):
        with self._lock:
            if self.count >= self.limit:
                raise BudgetExceeded("DART 호출 예산 소진")
            self.count += 1
        return self.api.request(endpoint, **params)


def normalize_long(reports: dict) -> list[dict]:
    """DART normalize 는 마지막 8분기만 돌려준다. 앞부분을 따로 정규화해 이어 붙여 최대 16분기를 쓴다(각 분기는 이전 분기에만 의존)."""
    keys = sorted(reports)
    recent = dart_mod.normalize(reports)
    if len(keys) <= 8:
        return recent
    older = dart_mod.normalize({k: reports[k] for k in keys[:-8]})
    merged = {q["period"]: q for q in older}
    merged.update({q["period"]: q for q in recent})
    return sorted(merged.values(), key=lambda q: (q["year"], q["quarter"]))


def _stale(path: Path, now: dt.datetime) -> bool:
    rec = read_json(path)
    if not rec or rec.get("status") != "ok":
        return True
    try:
        return (now - dt.datetime.fromisoformat(rec["checkedAt"])).days >= REFRESH_DAYS
    except (KeyError, ValueError):
        return True


def collect_kr(codes: dict[str, str], now: dt.datetime, *, api, out_dir: Path, cache_dir: Path, budget: int, workers: int = 4) -> dict:
    corps = api.corporations(cache_dir)
    capi = CountingApi(api, budget)
    todo = [c for c in sorted(codes) if _stale(out_dir / f"{c}.json", now)]
    todo.sort(key=lambda c: (out_dir / f"{c}.json").exists())     # 아직 없는 종목 먼저
    done = failed = 0

    def one(code):
        if code not in corps:
            raise RuntimeError("DART corporation mapping unavailable")
        company = capi.request("company.json", corp_code=corps[code])
        return company, normalize_long(dart_mod.fetch_reports(capi, corps[code], code, now, cache_dir))

    exhausted = False
    for i in range(0, len(todo), workers * 2):
        if exhausted or capi.count >= budget:
            break
        chunk = todo[i:i + workers * 2]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {c: pool.submit(one, c) for c in chunk}
        for code, fut in futs.items():
            try:
                company, quarters = fut.result()
                write_json(out_dir / f"{code}.json", {"schemaVersion": 1, "market": "kr", "code": code, "name": codes[code],
                                                       "industryCode": company.get("induty_code"), "industrySystem": "KSIC",
                                                       "checkedAt": now.isoformat(), "status": "ok" if quarters else "unavailable",
                                                       "quarters": quarters})
                done += 1
            except BudgetExceeded:
                exhausted = True
            except Exception as exc:  # noqa: BLE001 — 키·요청 주소를 로그에 남기지 않는다
                failed += 1
                log.warning("%s 수집 실패: %s", code, exc if isinstance(exc, RuntimeError) else "처리 오류")
    return {"targets": len(codes), "stale": len(todo), "done": done, "failed": failed, "requests": capi.count, "budgetExhausted": exhausted}


def shares_from_facts(data: dict, limit: int = 16) -> list[dict]:
    """dei:EntityCommonStockSharesOutstanding → [{end, filedAt, val}]. 같은 공시의 복수 클래스 값은 합산한다."""
    rows = ((data.get("facts") or {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    by_accn: dict[str, dict] = {}
    for r in rows:
        if r.get("form") not in ("10-Q", "10-Q/A", "10-K", "10-K/A") or not isinstance(r.get("val"), (int, float)) or r["val"] <= 0:
            continue
        g = by_accn.setdefault(r["accn"], {"end": r.get("end"), "filedAt": r.get("filed"), "vals": set()})
        g["vals"].add(float(r["val"]))
    out = [{"end": g["end"], "filedAt": g["filedAt"], "val": float(sum(g["vals"]))} for g in by_accn.values() if g["filedAt"]]
    return sorted(out, key=lambda x: (x["filedAt"], x["end"] or ""))[-limit:]


def quarters_long(data: dict, today: dt.date) -> list[dict]:
    """SEC normalize 는 마지막 8분기만 돌려준다. 2년 전 시점 기준으로 한 번 더 정규화해 이어 붙인다(최대 16분기)."""
    recent = sec_mod.normalize(data, today.isoformat())
    older = sec_mod.normalize(data, (today - dt.timedelta(days=730)).isoformat())
    merged = {q["periodEnd"]: q for q in older}
    merged.update({q["periodEnd"]: q for q in recent})
    return [merged[k] for k in sorted(merged)]


def collect_us(symbols: dict[str, str], now: dt.datetime, *, fetch, out_dir: Path) -> dict:
    tickers = fetch("https://www.sec.gov/files/company_tickers.json")
    mapping = {v["ticker"].replace(".", "-").upper(): v["cik_str"] for v in tickers.values()}
    done = failed = skipped = 0
    for sym in sorted(symbols):
        path = out_dir / f"{sym}.json"
        if not _stale(path, now):
            skipped += 1
            continue
        try:
            cik = mapping.get(sym.replace(".", "-").upper())
            if cik is None:
                raise RuntimeError("SEC ticker mapping unavailable")
            data = fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json")
            if int(data["cik"]) != int(cik):
                raise RuntimeError("SEC company identity mismatch")
            sic = fetch(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json").get("sic")
            quarters = quarters_long(data, now.date())
            write_json(path, {"schemaVersion": 1, "market": "us", "code": sym, "name": symbols[sym], "cik": str(cik), "industryCode": sic,
                              "industrySystem": "SIC", "checkedAt": now.isoformat(), "status": "ok" if quarters else "unavailable",
                              "quarters": quarters, "shares": shares_from_facts(data)})
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("%s 수집 실패: %s", sym, exc if isinstance(exc, RuntimeError) else "처리 오류")
    return {"targets": len(symbols), "done": done, "failed": failed, "skippedFresh": skipped}


_TICKER_OVERRIDES = {"BRKB": "BRK-B", "BFB": "BF-B"}


def load_us_universe(path: Path = config.UNIVERSE_US_PATH, listing=None) -> dict[str, str]:
    """S&P 500 구성종목 {티커: 이름}. 목록을 받으면 파일에 남기고, 못 받으면 직전 파일을 쓴다."""
    try:
        if listing is None:
            import FinanceDataReader as fdr
            listing = fdr.StockListing("S&P500")
        syms = {}
        for r in listing.itertuples(index=False):
            s = _TICKER_OVERRIDES.get(str(r.Symbol), str(r.Symbol)).strip()
            if s and getattr(r, "Name", None):
                syms[s] = str(r.Name)
        if len(syms) >= 400:
            write_json(path, {"asOf": dt.date.today().isoformat(), "symbols": [{"symbol": s, "name": n} for s, n in sorted(syms.items())]}, indent=1)
            return syms
    except Exception as exc:  # noqa: BLE001
        log.warning("S&P500 목록을 받지 못해 직전 파일을 씁니다: %s", exc)
    raw = read_json(path, {}) or {}
    return {r["symbol"]: r.get("name", "") for r in raw.get("symbols", [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["kr", "us", "all"], default="all")
    ap.add_argument("--budget", type=int, default=8000, help="DART 호출 예산(일 한도 20,000 중 이 실행에서 쓸 수)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    now = dt.datetime.now(dt.timezone.utc)
    failed = False
    if args.market in ("kr", "all"):
        key = os.environ.get("DART_API_KEY", "").strip()
        if not key:
            log.error("DART_API_KEY 없음 — 한국 재무 수집 건너뜀")
            failed = True
        else:
            uni = inputs.kr_universe(config.ROOT / "research" / "nhplug" / "kr_extra.json")
            codes = {c: u["name"] for c, u in uni.items() if u["marcapEok"] >= KR_MIN_MARCAP_EOK}
            log.info("한국: %s", collect_kr(codes, now, api=dart_mod.Dart(key), out_dir=config.FIN_DIR / "kr", cache_dir=config.CACHE_DIR / "kr", budget=args.budget))
    if args.market in ("us", "all"):
        if not os.environ.get("SEC_USER_AGENT", "").strip():
            log.error("SEC_USER_AGENT 없음 — 미국 재무 수집 건너뜀")
            failed = True
        else:
            log.info("미국: %s", collect_us(load_us_universe(), now, fetch=sec_mod.fetch, out_dir=config.FIN_DIR / "us"))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
