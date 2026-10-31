"""롱베이스 돌파 스크리너 CLI.

    python -m longbase.run --market ALL            # KR + US
    python -m longbase.run --market KR --limit 50  # 개발용 소규모

결과: output/longbase/longbase_{kr,us}.csv, docs/data/longbase_{kr,us}.json, output/longbase/REPORT.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .config import CONFIG
from .detect import avg_trading_value, find_longbase_breakout, momentum_score

logger = logging.getLogger("longbase")
ROOT = Path(__file__).resolve().parents[1]
MAX_WORKERS = 8
MAX_RETRIES = 3


def _fdr():
    try:
        import FinanceDataReader as fdr
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("pip install finance-datareader") from exc
    return fdr


def kr_universe() -> pd.DataFrame:
    fdr = _fdr()
    try:
        lst = pd.concat([fdr.StockListing("KOSPI"), fdr.StockListing("KOSDAQ")], ignore_index=True)
    except Exception as exc:  # noqa: BLE001  KRX 장애 시 네이버 대체
        logger.error("KRX 목록 실패(%s) — 네이버 대체", exc)
        sys.path.insert(0, str(ROOT))
        from naver_listing import naver_kr_listing
        lst = naver_kr_listing()
    lst = lst.dropna(subset=["Code", "Name"])
    lst = lst[~lst["Name"].astype(str).str.contains("스팩", regex=False)]
    # 우선주(코드 끝자리가 0이 아님)·리츠 등 제외
    lst = lst[lst["Code"].astype(str).str.endswith("0")]
    return lst[["Code", "Name"]].assign(Market="KR").reset_index(drop=True)


def us_universe() -> pd.DataFrame:
    fdr = _fdr()
    lst = fdr.StockListing("S&P500")
    code = "Symbol" if "Symbol" in lst.columns else "Code"
    name = "Name" if "Name" in lst.columns else "name"
    out = lst[[code, name]].rename(columns={code: "Code", name: "Name"}).dropna()
    return out.assign(Market="US").reset_index(drop=True)


def _fetch(code: str, start: dt.date) -> pd.DataFrame:
    fdr = _fdr()
    last = None
    for i in range(MAX_RETRIES):
        try:
            df = fdr.DataReader(code, start)
            if df is not None and not df.empty:
                return df.dropna(subset=["Close"])
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(1.6 ** i)
    raise RuntimeError(f"OHLCV 실패: {last}")


def _one(code, name, market, start):
    try:
        df = _fetch(code, start)
    except Exception as exc:  # noqa: BLE001
        return {"code": code, "name": name, "ok": False, "err": str(exc)}
    min_tv = CONFIG.min_trading_value_kr if market == "KR" else CONFIG.min_trading_value_us
    tv = avg_trading_value(df)
    mom = momentum_score(df["Close"])
    if tv is None or tv < min_tv:
        return {"code": code, "name": name, "ok": True, "mom": mom, "bo": None}
    return {"code": code, "name": name, "ok": True, "mom": mom, "tv": tv,
            "bo": find_longbase_breakout(df)}


def run_market(market: str, limit: int | None = None) -> pd.DataFrame:
    uni = kr_universe() if market == "KR" else us_universe()
    if limit:
        uni = uni.head(limit)
    start = dt.date.today() - dt.timedelta(days=CONFIG.history_calendar_days)
    results = []
    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        futs = [pool.submit(_one, r.Code, r.Name, market, start) for r in uni.itertuples()]
        for i, f in enumerate(as_completed(futs), 1):
            results.append(f.result())
            if i % 100 == 0 or i == len(futs):
                logger.info("%s %d/%d", market, i, len(futs))
    failed = sum(1 for r in results if not r["ok"])
    logger.info("%s: 조회 실패 %d / %d", market, failed, len(results))

    # RS 백분위: 데이터가 충분한 전 종목(유동성 필터 전) 기준
    moms = pd.Series({r["code"]: r["mom"] for r in results if r.get("mom") is not None})
    pct = moms.rank(pct=True) * 100
    rows = []
    for r in results:
        bo = r.get("bo")
        if bo is None:
            continue
        rs = float(pct.get(r["code"], float("nan")))
        if not rs >= CONFIG.rs_min_percentile:
            continue
        rows.append({"market": market, "code": r["code"], "name": r["name"],
                     "rs_pct": round(rs, 1), "avg_trading_value": r["tv"], **bo.to_dict()})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["base_days", "vol_ratio"], ascending=False).reset_index(drop=True)
    return df


def write_outputs(frames: dict[str, pd.DataFrame]) -> None:
    out = ROOT / "output" / "longbase"
    docs = ROOT / "docs" / "data"
    out.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    lines = [f"# 롱베이스 돌파 후보 ({now})", "",
             f"기준: 베이스 ≥{CONFIG.min_base_days}거래일, 깊이 ≤{CONFIG.max_depth:.0%}, "
             f"거래량 ≥{CONFIG.min_vol_ratio}배, 최근 {CONFIG.recent_days}일 이내, "
             f"RS ≥{CONFIG.rs_min_percentile:.0f}, 200일선 위, 피벗 +{CONFIG.max_extension:.0%} 이내", ""]
    for m, df in frames.items():
        df.to_csv(out / f"longbase_{m.lower()}.csv", index=False, encoding="utf-8-sig")
        (docs / f"longbase_{m.lower()}.json").write_text(
            json.dumps({"generatedAt": now, "market": m, "rows": json.loads(df.to_json(orient="records"))},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        lines += [f"## {m} — {len(df)}종목", ""]
        if df.empty:
            lines += ["해당 없음", ""]
            continue
        lines += ["| 종목 | 돌파일 | 베이스(일) | 깊이% | 거래량배 | CLV | 피벗대비% | RS |", "|---|---|---|---|---|---|---|---|"]
        for r in df.itertuples():
            lines.append(f"| {r.name}({r.code}) | {r.breakout_date} | {r.base_days} | {r.depth_pct:.1f} | "
                         f"{r.vol_ratio:.2f} | {r.clv:.2f} | {r.extension_pct:+.1f} | {r.rs_pct:.0f} |")
        lines.append("")
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["KR", "US", "ALL"], default="ALL")
    ap.add_argument("--limit", type=int, default=None, help="유니버스 앞에서 N종목만")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    markets = ["KR", "US"] if a.market == "ALL" else [a.market]
    write_outputs({m: run_market(m, a.limit) for m in markets})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
