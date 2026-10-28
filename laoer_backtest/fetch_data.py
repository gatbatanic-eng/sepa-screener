"""백테스트용 일봉 수집 → laoer_backtest/data/*.csv

GitHub Actions(laoer_data.yml)에서 실행한다. 세션 네트워크가 막혀 있어도 저장소의 CSV만으로 백테스트할 수 있게 하기 위함.
- TQQQ: 실데이터 (2010-02~)
- QQQ, ^NDX: 합성 TQQQ(일간수익률 x3 - 비용) 생성용 장기 데이터
- KRW=X: 원/달러 환율 (원화 기준 성과 계산용)
- ^IRX: 13주 T-bill 금리(%) — 합성 TQQQ 차입비용·현금 이자용
yfinance 실패 시 FinanceDataReader로 재시도한다.
"""
import os
import sys

import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "data")
TICKERS = {"TQQQ": "TQQQ", "QQQ": "QQQ", "NDX": "^NDX", "USDKRW": "KRW=X", "IRX": "^IRX"}
FDR_ALIAS = {"^NDX": "NASDAQ100", "KRW=X": "USD/KRW"}  # FinanceDataReader 심볼
COLS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def _yf(sym):
    import yfinance as yf
    d = yf.download(sym, start="1985-01-01", progress=False, auto_adjust=False, threads=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d


def _fdr(sym):
    import FinanceDataReader as fdr
    d = fdr.DataReader(FDR_ALIAS.get(sym, sym), "1985-01-01")
    if "Adj Close" not in d:
        d["Adj Close"] = d["Close"]
    return d


def fetch(sym):
    for src in (_yf, _fdr):
        try:
            d = src(sym)
            if len(d):
                d = d[[c for c in COLS if c in d]].dropna(subset=["Close"])
                d.index = pd.to_datetime(d.index).tz_localize(None)
                d.index.name = "Date"
                return d, src.__name__.strip("_")
        except Exception as e:  # 소스 하나 실패해도 다음 소스로
            print(f"  {sym} {src.__name__} 실패: {e}")
    return None, None


def main():
    os.makedirs(OUT, exist_ok=True)
    failed = []
    for name, sym in TICKERS.items():
        d, src = fetch(sym)
        if d is None:
            failed.append(name)
            continue
        d.round(6).to_csv(os.path.join(OUT, f"{name}.csv"))
        print(f"{name:7s} {src:3s} {len(d):6d}행 {d.index[0].date()} ~ {d.index[-1].date()}")
    if "TQQQ" in failed:
        sys.exit("TQQQ 수집 실패")


if __name__ == "__main__":
    main()
