"""S&P500 현재 구성종목 일봉을 수집해 data_cache에 저장하고 manifest를 만든다.

실행: python -m golden_backtest.data.build [--limit N] [--workers 8]
읽기 전용 소스, 쓰기는 golden_backtest/data_cache/ 와 golden_backtest/manifest/ 뿐이다.

캐시 보호: 재수집 결과가 기존 캐시보다 행이 적거나 과거 구간 값이 바뀌었으면 덮어쓰지 않고 경고 후 기존 캐시를 유지한다.
수집 자체가 실패(또는 쓸 수 없는 데이터)해도 기존 캐시가 있으면 그것을 쓴다. 변경 내역은 manifest의 cache_changes에 남는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from golden_backtest import config
from golden_backtest.data import adjust, cache_guard, calendar, clean, quality, store
from golden_backtest.data import universe as uni
from golden_backtest.data.providers import fdr_provider as prov

log = logging.getLogger("golden_backtest.build")


def _fetch_new(symbol: str, cfg: dict, cutoff: pd.Timestamp):
    """공급자에서 받아 정리·가공한 (저장명, 프레임, 분할, 메타). 쓸 수 없는 데이터면 예외."""
    start = cfg["fetch"]["start"]
    raw, used = prov.fetch_ohlcv(symbol, start, cfg["universe"]["dual_class_retry"])
    cleaned, info = clean.clean_ohlcv(raw, cutoff)
    if cleaned.empty:
        raise RuntimeError(f"정리 후 데이터 없음(공급자 반환 {len(raw)}행)")
    starts_at_floor = bool(cleaned.index[0] <= pd.Timestamp(start))  # 시작 구간 절단 전의 첫 봉으로 판단
    cleaned, stripped = clean.strip_leading_placeholders(cleaned, cfg["placeholder_strip"]["min_run"])
    if cleaned.empty:
        raise RuntimeError("시작 구간 절단 후 데이터 없음")
    splits_ok, splits_err = True, None
    try:
        splits = prov.fetch_splits(used)
    except Exception as exc:  # noqa: BLE001
        splits, splits_ok, splits_err = pd.Series(dtype=float), False, str(exc)[:200]
    frame = adjust.build_adjusted(cleaned, splits, splits_ok)
    meta = {"unclosed_dropped": info["unclosed_dropped"], "nan_dropped": info["nan_dropped"], "nan_runs": info["nan_runs"],
            "stripped_leading": stripped, "starts_at_floor": starts_at_floor, "splits_ok": splits_ok, "splits_error": splits_err}
    return used, frame, splits, meta


def _cached_name(symbol: str, dual_retry: bool) -> str | None:
    have = set(store.cached_symbols())
    for name in (symbol, prov.dual_class_variant(symbol) if dual_retry else None):
        if name and name in have:
            return name
    return None


def _metrics(symbol: str, used: str, frame: pd.DataFrame, splits: pd.Series, meta: dict, cfg: dict) -> dict:
    cfg_q, cfg_a1, adj = cfg["quality"], cfg["a1_history_check"], cfg["adjustment"]
    since = pd.Timestamp(cfg_q["anomaly_since"])
    rec = {
        "requested_as": symbol, "stored_as": used, "ok": True,
        "rows": int(len(frame)), "first": frame.index[0].date().isoformat(), "last": frame.index[-1].date().isoformat(),
        "splits": int(len(splits)), "splits_ok": meta.get("splits_ok", True), "splits_error": meta.get("splits_error"),
        "unclosed_dropped": meta.get("unclosed_dropped", 0), "nan_dropped_total": len(meta.get("nan_dropped", [])),
        "nan_dropped_since": [d for d in meta.get("nan_dropped", []) if pd.Timestamp(d) >= since],
        "nan_runs": meta.get("nan_runs", []), "stripped_leading": meta.get("stripped_leading"),
        "meta_missing": not meta,
        "min_raw_close": None if frame["raw_close"].isna().all() else round(float(frame["raw_close"].min()), 4),
        "a1": quality.a1_history_check(frame, cfg_a1["asof"], cfg_a1["first_bars"], bool(meta.get("starts_at_floor", False))),
        "divergences": quality.adjustment_artifacts(frame, splits, adj["divergence"]["threshold"], adj["split_match_days"]),
        "errors": quality.adjustment_errors(frame, splits, adj["dhr_type"]["divergence"], adj["dhr_type"]["min_adj_ret"],
                                            adj["split_match_days"]),
    }
    rec.update(quality.anomalies_since(frame, cfg_q["anomaly_since"], cfg_q["big_move_pct"]))
    return rec


def process_symbol(symbol: str, cfg: dict, cutoff: pd.Timestamp) -> dict:
    """한 종목 수집·가공·저장. 실패는 예외 대신 {"ok": False, "error": ...}로 돌려준다."""
    dual = cfg["universe"]["dual_class_retry"]
    change: dict | None = None
    try:
        used, frame, splits, meta = _fetch_new(symbol, cfg, cutoff)
        action = "new"
        if used in store.cached_symbols():
            old = store.load_ohlcv(used)
            problem = cache_guard.compare_frames(old, frame)
            if problem:
                log.warning("[%s] 재수집 결과가 기존 캐시와 달라 기존 캐시를 유지한다: %s", used, problem)
                frame, splits, meta = old, store.load_splits(used), store.load_meta(used) or {}
                action, change = "kept_old", problem
            else:
                action = "updated"
        if action in ("new", "updated"):
            try:
                store.save_ohlcv(used, frame)
                store.save_meta(used, meta)
                if meta.get("splits_ok", True):
                    store.save_splits(used, splits)
            except OSError as exc:
                return {"requested_as": symbol, "ok": False, "error": f"저장 실패: {exc}"[:200]}
    except Exception as exc:  # noqa: BLE001
        used = _cached_name(symbol, dual)
        if used is None:
            return {"requested_as": symbol, "ok": False, "error": str(exc)[:200]}
        log.warning("[%s] 수집 실패(%s) — 기존 캐시 %s를 사용한다", symbol, str(exc)[:120], used)
        frame, splits, meta = store.load_ohlcv(used), store.load_splits(used), store.load_meta(used) or {}
        action, change = "fetch_failed_used_cache", {"error": str(exc)[:200]}
    rec = _metrics(symbol, used, frame, splits, meta, cfg)
    rec["cache_action"], rec["cache_change"] = action, change
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
            old = None
            try:
                old = store.load_index(name)
            except FileNotFoundError:
                pass
            if old is not None and len(df) < len(old):
                log.warning("[%s] 지수 재수집 행 감소(%d→%d) — 기존 캐시 유지", name, len(old), len(df))
                bench[name] = {"cache_action": "kept_old", "rows_old": len(old), "rows_new": len(df)}
                continue
            store.save_index(name, df)
            bench[name] = {"rows": len(df), "first": df.index[0].date().isoformat(), "last": df.index[-1].date().isoformat(),
                           "nan_dropped": info["nan_dropped"], "unclosed_dropped": info["unclosed_dropped"]}
        except Exception as exc:  # noqa: BLE001
            bench[name] = {"error": str(exc)[:200]}

    ok = {r["stored_as"]: r for r in results if r["ok"]}  # manifest 키는 저장명(BRK-B). 요청 티커는 requested_as
    exclusions = uni.compute_exclusions(ok, cfg)
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest = {
        "collected_at": collected_at,
        "spec_version": "1.3",
        "source": "가격·지수: FinanceDataReader / 분할 이벤트: yfinance(Yahoo)",
        "fetch_start": start, "closed_session_cutoff": cutoff.date().isoformat(),
        "universe": cfg["universe"]["name"], "survivorship_bias": True,
        "symbols_requested": len(symbols), "symbols_ok": len(ok),
        "symbols_failed": {r["requested_as"]: r["error"] for r in results if not r["ok"]},
        "cache_changes": {k: {"action": r["cache_action"], "detail": r["cache_change"]}
                          for k, r in sorted(ok.items()) if r["cache_action"] in ("kept_old", "fetch_failed_used_cache")},
        "benchmarks": bench,
        "symbols": {k: {**{f: r[f] for f in ("requested_as", "rows", "first", "last", "splits", "splits_ok", "splits_error",
                                             "unclosed_dropped", "nan_dropped_total", "stripped_leading", "a1")},
                        "cache_action": r["cache_action"]}
                    for k, r in sorted(ok.items())},
    }
    quality_report = {
        "collected_at": collected_at, "since": cfg["quality"]["anomaly_since"],
        "big_move_pct": cfg["quality"]["big_move_pct"], "adjustment": cfg["adjustment"],
        "per_symbol": {k: {"zero_volume": r["zero_volume"], "nan_rows": r["nan_dropped_since"], "nan_runs": r["nan_runs"],
                           "big_moves": r["big_moves"], "adjustment_divergences": r["divergences"],
                           "adjustment_errors": r["errors"], "min_raw_close": r["min_raw_close"]}
                       for k, r in sorted(ok.items())},
    }
    universe_p1 = {
        "collected_at": collected_at, "spec_version": "1.3",
        "included": sorted(set(ok) - set(exclusions)),
        "excluded": {k: exclusions[k] for k in sorted(exclusions)},
    }
    # CTVA형(분리상장 미보정 등 공급자·수정 수익률이 같이 크게 움직이는 봉)은 자동 탐지가 안 되므로 ±40% 봉 전체를 수동 검토 대상으로 남긴다
    review = sorted(({"symbol": k, "date": m["date"], "adj_ret": m["ret"], "vendor_close_ret": m.get("vendor_close_ret"),
                      "volume_ratio": m.get("volume_ratio"), "sets_new_ath": m["sets_new_ath"],
                      "holds_current_ath": m["holds_current_ath"], "in_p1_universe": k not in exclusions,
                      "review_status": "pending"}
                     for k, r in ok.items() for m in r["big_moves"]), key=lambda e: (e["date"], e["symbol"]))
    review_doc = {"collected_at": collected_at, "since": cfg["quality"]["anomaly_since"],
                  "threshold": cfg["quality"]["big_move_pct"], "events": review}
    store.write_manifest("manifest.json", manifest)
    store.write_manifest("quality_report.json", quality_report)
    store.write_manifest("universe_p1.json", universe_p1)
    store.write_manifest("review_big_moves.json", review_doc)
    log.info("완료: 성공 %d / 요청 %d, 실패 %s, 캐시 유지·대체 %s, P1 제외 %d", len(ok), len(symbols),
             list(manifest["symbols_failed"]), list(manifest["cache_changes"]), len(exclusions))


if __name__ == "__main__":
    main()
