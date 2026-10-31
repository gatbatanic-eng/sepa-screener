"""Actions 러너용 전 종목 수집기. 소스 폴백: FDR -> pykrx -> yfinance. 못 받은 종목은 값을 채우지 않고 status 에 기록한다.
산출: data_store/{listing,delisting,index,fetch_status}.parquet, data_store/ohlcv_YYYY.parquet"""
import time, sys, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd, FinanceDataReader as fdr

OUT = Path(__file__).resolve().parents[1] / "data_store"; OUT.mkdir(exist_ok=True)
START = "2020-01-01"           # 워밍업 포함. 분석 구간은 2021-01-01~
END = dt.date.today().isoformat()
COLS = ["Open", "High", "Low", "Close", "Volume"]

def is_common(code, name):
    n = str(name)
    return str(code).endswith("0") and "스팩" not in n and "리츠" not in n and "SPAC" not in n.upper()

def get_fdr(tk, mkt):
    return fdr.DataReader(tk, START, END)
def get_pykrx(tk, mkt):
    from pykrx import stock
    d = stock.get_market_ohlcv(START.replace("-", ""), END.replace("-", ""), tk, adjusted=True)
    return d.rename(columns={"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"})
def get_yf(tk, mkt):
    import yfinance as yf
    d = yf.download(f"{tk}.{'KS' if mkt=='KOSPI' else 'KQ'}", start=START, end=END, progress=False, auto_adjust=False, threads=False)
    if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
    return d

def fetch(args):
    tk, mkt = args
    err = []
    for src, f in (("fdr", get_fdr), ("pykrx", get_pykrx), ("yfinance", get_yf)):
        try:
            d = f(tk, mkt)
            d = d[[c for c in COLS if c in d.columns]].copy() if d is not None else None
            if d is not None and len(d) and "Close" in d:
                d.index = pd.to_datetime(d.index).tz_localize(None); d.index.name = "Date"
                d = d.reset_index(); d["ticker"] = tk
                time.sleep(0.05)
                return tk, mkt, src, d, ""
            err.append(f"{src}:empty")
        except Exception as e:
            err.append(f"{src}:{type(e).__name__} {str(e)[:60]}")
    return tk, mkt, "", None, "; ".join(err)

def main():
    lst = fdr.StockListing("KRX"); dl = fdr.StockListing("KRX-DELISTING")
    lst.to_parquet(OUT / "listing.parquet"); dl.to_parquet(OUT / "delisting.parquet")
    print("listing", len(lst), "delisting", len(dl), flush=True)
    lst = lst[lst["Market"].isin(["KOSPI", "KOSDAQ"])]
    cur = [(r.Code, r.Market) for r in lst.itertuples() if is_common(r.Code, r.Name)]
    dl["DelistingDate"] = pd.to_datetime(dl["DelistingDate"], errors="coerce")
    d2 = dl[(dl["Market"].isin(["KOSPI", "KOSDAQ"])) & (dl["DelistingDate"] >= START)]
    old = [(r.Symbol, r.Market) for r in d2.itertuples() if is_common(r.Symbol, r.Name)]
    jobs = list(dict.fromkeys(cur + old)); print("jobs: current", len(cur), "delisted>=2020", len(old), "total", len(jobs), flush=True)
    idx = []
    for sym, nm in (("KS11", "KOSPI"), ("KQ11", "KOSDAQ")):
        d = fdr.DataReader(sym, START, END); d = d[[c for c in COLS if c in d.columns]].reset_index(); d["index"] = nm; idx.append(d)
    pd.concat(idx).to_parquet(OUT / "index.parquet")
    status, frames, t0 = [], [], time.time()
    with ThreadPoolExecutor(6) as ex:
        for i, (tk, mkt, src, d, err) in enumerate(ex.map(fetch, jobs), 1):
            status.append({"ticker": tk, "market": mkt, "source": src, "rows": 0 if d is None else len(d),
                           "first": None if d is None else d["Date"].min(), "last": None if d is None else d["Date"].max(), "error": err})
            if d is not None: frames.append(d)
            if i % 200 == 0: print(f"{i}/{len(jobs)} {time.time()-t0:.0f}s", flush=True)
    st = pd.DataFrame(status); st.to_parquet(OUT / "fetch_status.parquet")
    df = pd.concat(frames, ignore_index=True)
    mk = dict(jobs); df["market"] = df["ticker"].map(mk)
    for c in ["Open", "High", "Low", "Close"]: df[c] = df[c].astype("float32")
    df["Volume"] = df["Volume"].astype("float64")
    for y, g in df.groupby(df["Date"].dt.year):
        g.to_parquet(OUT / f"ohlcv_{y}.parquet", compression="zstd", index=False)
    print("done rows", len(df), "tickers ok", st[st.rows > 0].shape[0], "fail", st[st.rows == 0].shape[0])

if __name__ == "__main__": main()
