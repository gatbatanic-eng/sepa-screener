"""폭락 이후 흐름 연구용 지수 일봉 수집 → crash_study/data/*.csv

GitHub Actions(crash_data.yml)에서 실행한다. 세션 네트워크는 야후 접속이 막혀 있다.
- IXIC: 나스닥 종합 (1971-02~)
- NDX : 나스닥100 (1985-10~)
"""
import os

import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "data")
TICKERS = {"IXIC": "^IXIC", "NDX": "^NDX"}
FDR_ALIAS = {"^IXIC": "IXIC", "^NDX": "NASDAQ100"}
COLS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def _yf(sym):
    import yfinance as yf
    d = yf.download(sym, start="1970-01-01", progress=False, auto_adjust=False, threads=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d


def _fdr(sym):
    import FinanceDataReader as fdr
    d = fdr.DataReader(FDR_ALIAS.get(sym, sym), "1970-01-01")
    if "Adj Close" not in d:
        d["Adj Close"] = d["Close"]
    return d


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, sym in TICKERS.items():
        for src in (_yf, _fdr):
            try:
                d = src(sym)
                if not len(d):
                    continue
                d = d[[c for c in COLS if c in d]].dropna(subset=["Close"])
                d.index = pd.to_datetime(d.index).tz_localize(None)
                d.index.name = "Date"
                path = os.path.join(OUT, f"{name}.csv")
                if os.path.exists(path):
                    old = pd.read_csv(path, index_col=0, parse_dates=True)
                    d = pd.concat([old[~old.index.isin(d.index)], d]).sort_index()
                d.round(6).to_csv(path)
                print(f"{name:5s} {src.__name__.strip('_'):3s} {len(d):6d}행 {d.index[0].date()} ~ {d.index[-1].date()}")
                break
            except Exception as e:
                print(f"  {name} {src.__name__} 실패: {e}")


if __name__ == "__main__":
    main()
