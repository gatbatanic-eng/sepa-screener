"""S&P500 현재 구성종목 일봉을 수집해 data_cache에 저장하고 manifest를 만든다.

실행: python -m golden_backtest.data.build [--limit N] [--workers 8]
읽기 전용 소스, 쓰기는 golden_backtest/data_cache/ 와 golden_backtest/manifest/ 뿐이다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from golden_backtest import config
from golden_backtest.data import adjust, calendar, clean, quality, store
from golden_backtest.data import universe as uni
from golden_backtest.data.providers import fdr_provider as prov

log = logging.getLogger("golden_backtest.build")


def process_symbol(symbol: str, cfg: dict, cutoff: pd.Timestamp) -> dict:
    """한 종목 수집·가공·저장. 실패는 예외 대신 {"ok": False, "error": ...}로 돌려준다."""
    start = cfg["fetch"]["start"]
    cfg_q, cfg_a1 = cfg["quality"], cfg["a1_history_check"]
    try:
        raw, used = prov.fetch_ohlcv(symbol, start, cfg["universe"]["dual_class_retry"])
    except Exception as exc:  # noqa: BLE001
        return {"requested_as": symbol, "ok": False, "error": str(exc)[:200]}
    cleaned, info = clean.clean_ohlcv(raw, cutoff)
    if cleaned.empty:
        return {"requested_as": symbol, "ok": False, "error": f"정리 후 데이터 없음(공급자 반환 {len(raw)}행)"}
    starts_at_floor = cleaned.index[0] <= pd.Timestamp(start)  # 시작 구간 절단 전의 첫 봉으로 판단
    cleaned, stripped = clean.strip_leading_placeholders(cleaned, cfg["placeholder_strip"]["min_run"])
    if cleaned.empty:
        return {"requested_as": symbol, "ok": False, "error": "시작 구간 절단 후 데이터 없음"}
    splits_ok, splits_err = True, None
    try:
        splits = prov.fetch_splits(used)
    except Exception as exc:  # noqa: BLE001
        splits, splits_ok, splits_err = pd.Series(dtype=float), False, str(exc)[:200]
    frame = adjust.build_adjusted(cleaned, splits, splits_ok)
    try:
        store.save_ohlcv(used, frame)
        if splits_ok:
            store.save_splits(used, splits)
    except OSError as exc:
        return {"requested_as": symbol, "ok": False, "error": f"저장 실패: {exc}"[:200]}
    since = pd.Timestamp(cfg_q["anomaly_since"])
    nan_since = [d for d in info["nan_dropped"] if pd.Timestamp(d) >= since]
    art_cfg = cfg["adjustment_artifact"]
    rec = {
        "requested_as": symbol, "stored_as": used, "ok": True,
        "rows": int(len(frame)), "first": frame.index[0].date().isoformat(), "last": frame.index[-1].date().isoformat(),
        "splits": int(len(splits)), "splits_ok": splits_ok, "splits_error": splits_err,
        "unclosed_dropped": info["unclosed_dropped"], "nan_dropped_total": len(info["nan_dropped"]),
        "nan_dropped_since": nan_since, "nan_runs": info["nan_runs"],
        "stripped_leading": stripped,
        "min_raw_close": None if frame["raw_close"].isna().all() else round(float(frame["raw_close"].min()), 4),
        "a1": quality.a1_history_check(frame, cfg_a1["asof"], cfg_a1["first_bars"], bool(starts_at_floor)),
        "artifacts": quality.adjustment_artifacts(frame, splits, art_cfg["threshold"], art_cfg["split_match_days"]),
    }
    rec.update(quality.anomalies_since(frame, cfg_q["anomaly_since"], cfg_q["big_move_pct"]))
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종목만 (점검용)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    cfg = config.load("universe")
    start = cfg["fetch"]["start"]
    cutoff = calendar.latest_closed_us_session()
    symbols = prov.fetch_sp500_symbols()
    if args.limit:
        symbols = symbols[: args.limit]
    log.info("유니버스 %d종목, 마감 컷오프 %s", len(symbols), cutoff.date())

    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(lambda s: process_symbol(s, cfg, cutoff), symbols))

    bench = {}
    for name, code in cfg["fetch"]["benchmarks"].items():
        try:
            raw, _ = prov.fetch_ohlcv(code, start, False)
            df, info = clean.clean_ohlcv(raw, cutoff)
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            store.save_index(name, df)
            bench[name] = {"rows": len(df), "first": df.index[0].date().isoformat(), "last": df.index[-1].date().isoformat(),
                           "nan_dropped": info["nan_dropped"], "unclosed_dropped": info["unclosed_dropped"]}
        except Exception as exc:  # noqa: BLE001
            bench[name] = {"error": str(exc)[:200]}

    ok = {r["stored_as"]: r for r in results if r["ok"]}  # manifest 키는 저장명(BRK-B). 요청 티커는 requested_as
    exclusions = uni.compute_exclusions(ok, cfg)
    manifest = {
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "spec_version": "1.2",
        "source": "가격·지수: FinanceDataReader / 분할 이벤트: yfinance(Yahoo)",
        "fetch_start": start, "closed_session_cutoff": cutoff.date().isoformat(),
        "universe": cfg["universe"]["name"], "survivorship_bias": True,
        "symbols_requested": len(symbols), "symbols_ok": len(ok),
        "symbols_failed": {r["requested_as"]: r["error"] for r in results if not r["ok"]},
        "benchmarks": bench,
        "symbols": {k: {f: r[f] for f in ("requested_as", "rows", "first", "last", "splits", "splits_ok", "splits_error",
                                          "unclosed_dropped", "nan_dropped_total", "stripped_leading", "a1")}
                    for k, r in sorted(ok.items())},
    }
    quality_report = {
        "collected_at": manifest["collected_at"], "since": cfg["quality"]["anomaly_since"],
        "big_move_pct": cfg["quality"]["big_move_pct"],
        "adjustment_artifact": cfg["adjustment_artifact"],
        "per_symbol": {k: {"zero_volume": r["zero_volume"], "nan_rows": r["nan_dropped_since"], "nan_runs": r["nan_runs"],
                           "big_moves": r["big_moves"], "adjustment_artifacts": r["artifacts"],
                           "min_raw_close": r["min_raw_close"]} for k, r in sorted(ok.items())},
    }
    universe_p1 = {
        "collected_at": manifest["collected_at"], "spec_version": "1.2",
        "included": sorted(set(ok) - set(exclusions)),
        "excluded": {k: exclusions[k] for k in sorted(exclusions)},
    }
    store.write_manifest("manifest.json", manifest)
    store.write_manifest("quality_report.json", quality_report)
    store.write_manifest("universe_p1.json", universe_p1)
    log.info("완료: 성공 %d / 요청 %d, 실패 %s, P1 제외 %d", len(ok), len(symbols), list(manifest["symbols_failed"]), len(exclusions))


if __name__ == "__main__":
    main()
