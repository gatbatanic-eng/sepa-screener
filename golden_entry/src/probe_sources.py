"""Actions 러너에서 각 데이터 소스가 실제로 되는지만 점검한다 (키·토큰은 출력하지 않음)."""
import os, time, requests

def t(name, f):
    s = time.time()
    try:
        r = f()
        print(f"OK   {name}: {r}  ({time.time()-s:.1f}s)", flush=True)
    except Exception as e:
        print(f"FAIL {name}: {type(e).__name__} {str(e)[:160]}", flush=True)

def summ(df):
    return f"rows={len(df)} first={df.index[0] if len(df) else None} last={df.index[-1] if len(df) else None} cols={list(df.columns)[:8]}"

from pykrx import stock
import FinanceDataReader as fdr, yfinance as yf
t("pykrx ticker_list 2020-12-28 KOSPI", lambda: len(stock.get_market_ticker_list("20201228", market="KOSPI")))
t("pykrx ohlcv 005930 2013~", lambda: summ(stock.get_market_ohlcv("20130101", "20260101", "005930", adjusted=True)))
t("pykrx index KOSPI", lambda: summ(stock.get_index_ohlcv("20130101", "20260101", "1001")))
t("fdr KRX listing", lambda: len(fdr.StockListing("KRX")))
t("fdr KRX-DELISTING", lambda: (lambda d: f"rows={len(d)} cols={list(d.columns)}")(fdr.StockListing("KRX-DELISTING")))
t("fdr DataReader 005930", lambda: summ(fdr.DataReader("005930", "2013-01-01")))
t("fdr DataReader KS11", lambda: summ(fdr.DataReader("KS11", "2013-01-01")))
t("yfinance 005930.KS", lambda: summ(yf.download("005930.KS", start="2013-01-01", progress=False, auto_adjust=False)))
try:
    dl = fdr.StockListing("KRX-DELISTING")
    code = next(c for c in dl.columns if "Symbol" in c or "Code" in c or "종목코드" in c)
    for tk in dl[code].astype(str).str.zfill(6).head(5):
        t(f"fdr DataReader delisted {tk}", lambda tk=tk: summ(fdr.DataReader(tk, "2013-01-01")))
        t(f"pykrx ohlcv delisted {tk}", lambda tk=tk: summ(stock.get_market_ohlcv("20130101", "20260101", tk, adjusted=True)))
except Exception as e:
    print("delisted sample skipped:", type(e).__name__, str(e)[:120])
k, s = os.environ.get("NHPLUG_APPKEY"), os.environ.get("NHPLUG_APPSECRET")
print("NHPLUG secrets present:", bool(k and s))
if k and s:
    def nh():
        r = requests.post("https://api.nhplug.com:8443/oauth2/token", params={"appkey": k, "appsecretkey": s, "grant_type": "client_credentials", "scope": "oob"}, timeout=30)
        return f"token HTTP {r.status_code}"
    t("nhplug token", nh)
