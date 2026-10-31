"""Phase 1 수집기. pykrx 우선, 실패 시 FDR 대체. 실패는 data/cache/_failures.csv 에 기록하고 값을 채우지 않는다."""
import os, time, datetime as dt
from pathlib import Path
import pandas as pd, yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load(open(ROOT / "config.yaml"))
CACHE = ROOT / CFG["collect"]["cache_dir"]; CACHE.mkdir(parents=True, exist_ok=True)
SLEEP = CFG["collect"]["sleep_sec"]
FAIL = CACHE / "_failures.csv"

def log_fail(item, source, err):
    row = pd.DataFrame([{"ts": dt.datetime.now().isoformat(timespec="seconds"), "item": item, "source": source, "error": str(err)[:300]}])
    row.to_csv(FAIL, mode="a", header=not FAIL.exists(), index=False)

def _cached(name, fn):
    p = CACHE / f"{name}.parquet"
    if p.exists(): return pd.read_parquet(p)
    df = fn()
    if df is not None and len(df): df.to_parquet(p); time.sleep(SLEEP)
    return df

def ticker_snapshot(date, market):
    """date 시점 상장 종목 목록(pykrx). 실패 시 None."""
    def f():
        from pykrx import stock
        t = stock.get_market_ticker_list(date, market=market)
        return pd.DataFrame({"ticker": t, "market": market, "asof": date})
    try: return _cached(f"universe_{market}_{date}", f)
    except Exception as e: log_fail(f"universe {market} {date}", "pykrx", e); return None

def delisting_list():
    def f():
        import FinanceDataReader as fdr
        return fdr.StockListing("KRX-DELISTING")
    try: return _cached("delisting_fdr", f)
    except Exception as e: log_fail("KRX-DELISTING", "fdr", e); return None

def ohlcv(ticker, start, end):
    """수정주가 OHLCV. pykrx -> FDR 순서. 둘 다 실패하면 None (추정 금지)."""
    def f_pykrx():
        from pykrx import stock
        d = stock.get_market_ohlcv(start.replace("-", ""), end.replace("-", ""), ticker, adjusted=True)
        return d.reset_index()
    def f_fdr():
        import FinanceDataReader as fdr
        return fdr.DataReader(ticker, start, end).reset_index()
    for src, f in (("pykrx", f_pykrx), ("fdr", f_fdr)):
        try:
            df = _cached(f"ohlcv_{src}_{ticker}", f)
            if df is not None and len(df): df["source"] = src; return df
        except Exception as e: log_fail(f"ohlcv {ticker}", src, e)
    return None

def index_ohlcv(code, name, start, end):
    def f():
        from pykrx import stock
        return stock.get_index_ohlcv(start.replace("-", ""), end.replace("-", ""), code).reset_index()
    try: return _cached(f"index_{name}", f)
    except Exception as e: log_fail(f"index {name}", "pykrx", e); return None
