"""S&P500 편입일 수집·적용. 설계는 2026-11-04에 결과를 보기 전에 고정했다. python -m leader_backtest.sp500_members
목적: 유니버스가 '현재 S&P500 구성종목'이라서 지수에 편입되기 전(주로 성장하던) 구간의 신호가 들어 있는 편향을 줄인다.
규칙: 종목은 편입일(위키백과 현재 구성종목 표의 'Date added') 이전에는 신호 대상에서 뺀다. 표에 있으나 편입일이 비어 있으면 처음부터 구성종목으로 보고,
표에 아예 없는 종목(S&P500 구성종목이 아닌 유니버스 종목)은 전 구간 제외한다.
한계: 편출·상장폐지 종목은 여전히 빠져 있고(생존편향 일부 잔존), 한 번 빠졌다 재편입된 종목의 공백은 반영하지 않으며, 편입일은 위키백과 값이다."""
from __future__ import annotations

import io
import logging
import os
import urllib.request

import numpy as np
import pandas as pd

from .main import ROOT

log = logging.getLogger("leader_backtest.sp500_members")
URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CSV = ROOT / "research" / "leader_backtest" / "sp500_members.csv"


def norm_symbol(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.replace(".", "-", regex=False).str.upper()


def parse_members(html: str) -> pd.DataFrame:
    """현재 구성종목 표 -> (symbol, date_added). 표 머리글에 Symbol 과 Date added 가 있는 첫 표."""
    for t in pd.read_html(io.StringIO(html)):
        cols = {str(c).strip().lower(): c for c in t.columns}
        sym = next((cols[k] for k in cols if k in ("symbol", "ticker")), None)
        added = next((cols[k] for k in cols if k.startswith("date")), None)
        if sym is not None and added is not None and len(t) > 100:
            out = pd.DataFrame({"symbol": norm_symbol(t[sym]), "date_added": pd.to_datetime(t[added], errors="coerce")})
            return out.dropna(subset=["symbol"]).drop_duplicates("symbol").reset_index(drop=True)
    raise RuntimeError("S&P500 구성종목 표를 찾지 못했습니다")


def fetch() -> pd.DataFrame:
    req = urllib.request.Request(URL, headers={"User-Agent": "GoldenCodeResearch/1.0 (research; gatbatanic@gmail.com)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return parse_members(r.read().decode("utf-8", "replace"))


def eligibility(dates: np.ndarray, didx: np.ndarray, cidx: np.ndarray, codes: np.ndarray, members: pd.DataFrame) -> np.ndarray:
    """행별 불리언: 해당 날짜가 종목의 편입일 이후인가. 표에 있으나 편입일이 비면 항상 참, 표에 없는 종목은 항상 거짓. dates 는 정렬된 np.datetime64 배열."""
    added = members.set_index("symbol")["date_added"]
    start = np.array([(np.datetime64(added.get(c)) if pd.notna(added.get(c)) else np.datetime64("1900-01-01")) if c in added.index else np.datetime64("2262-01-01") for c in codes], dtype="datetime64[ns]")
    return dates[didx] >= start[cidx]


def apply_to(data, market: str) -> None:
    path = os.environ.get("MEMBERS_CSV")
    if not path or market != "us":
        return
    members = pd.read_csv(path, parse_dates=["date_added"])
    members["symbol"] = norm_symbol(members["symbol"])
    data.elig = eligibility(data.dates.astype("datetime64[ns]"), data.didx, data.cidx, data.codes, members)
    # 비교 기준선(같은 날 전 종목 평균)도 편입 이후 행만으로 다시 계산한다: 편입 전 구간의 높은 수익이 기준선을 부풀리지 않게
    data.base = {k: data._date_mean(data.out[k], data.valid[k] & data.elig) for k in data.out}
    log.info("편입일 기준 적용: 행의 %.1f%%가 편입 이후", float(data.elig.mean() * 100))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    df = fetch()
    CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV, index=False)
    print(f"{len(df)}종목, 편입일 있음 {int(df['date_added'].notna().sum())}, 2015년 이후 편입 {int((df['date_added'] >= '2015-01-01').sum())}")
    print(df.sort_values("date_added").tail(8).to_string())


if __name__ == "__main__":
    main()
