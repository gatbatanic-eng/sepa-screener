"""최근 4개월 상승 종목 역분석. (python -m leader_backtest.rally_4m) 탐색적 분석이며 매수 신호가 아니다.
정의는 2026-11-09 결과를 보기 전에 고정했다.

기간: 각 시장 지수의 마지막 거래일 T1, 그로부터 84거래일 전 T0 (약 4개월). 한국 지수 ^KS11, 미국 ^GSPC 의 거래일 기준.
유니버스: 한국 = KRX 코스피·코스닥 전 종목(우선주·스팩 제외) 중 T0 직전 20거래일 평균 거래대금 >= 5억원.
          미국 = S&P500 현재 구성종목. T0 에 종가가 있는 종목만.
상승 종목: R4M = T1 종가 / T0 종가 - 1.
  WINNER = 시장 안 R4M 상위 10% 이면서 R4M >= +20%.  STRONG = R4M >= +50%.
분석
  1) 요약·상위 종목 목록(구간 중 최고 수익, T1 기준 고점 대비 낙폭, 섹터).
  2) T0 시점 특징(그때 알 수 있던 값만): 각 특징을 T0 유니버스 5분위로 나눠 분위별 WINNER 비율 / 전체 비율(리프트), 그리고 WINNER·나머지 중앙값.
     이진 특징(8/8 추세템플릿, 조건 1~7, RS v2 >= 80 등)도 리프트로 본다. 섹터별 리프트.
  3) 포착 시점: 아래 신호가 구간 안에서 각 WINNER 에 처음 뜬 날, 그다음 거래일 종가 진입 시 T1 까지 남아 있던 상승 몫(로그 수익 비율).
     TREND   sepa 조건 1~7 + RS v2 >= 80 (현재 스크리너 8/8)
     HI60V   종가 >= 전일까지 60일 최고가 & 거래량 >= 전일까지 50일 평균 x 1.5
     HI20V   종가 >= 전일까지 20일 최고가 & 거래량 >= 전일까지 50일 평균 x 1.5
     RSACC   종가 > 50일선 & 50일선 > 10거래일 전 & RS v2 20일 변화 >= +15
     EARLY   RSACC & 종가 >= 52주 저가 x 1.3 & RS 라인(종목/지수) 63일 신고가
  4) 정밀도(사후확증 방지): 구간 전반(T0 ~ T0+42) 에 각 신호가 뜬 모든 종목 중 WINNER 가 된 비율과,
     첫 신호 다음 날 종가 → T1 수익률 중앙값을 같은 날짜 전체 종목 중앙값과 비교.
한계: 4개월 한 구간의 사후 관찰이다(한국은 구간 후반 급락 포함). 찾은 특징은 가설이며 앞으로 다시 검증해야 한다.
      섹터는 무료 목록 기준이라 비어 있을 수 있다. 미국은 S&P500 만 본다."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

import numpy as np
import pandas as pd

from sepa.universe import is_preferred_kr, is_spac_kr

from .main import ROOT, fetch_ohlcv
from .sepa_go import CFG_NEW, add_rs, stock_features
from .winners import extra_features

log = logging.getLogger("leader_backtest.rally_4m")
OUT_DIR = ROOT / "research" / "leader_backtest" / (os.environ.get("LB_OUT") or "rally_4m")
FETCH_START = os.environ.get("LB_FETCH_START") or "2024-11-01"
WIN_DAYS, EARLY_DAYS = 84, 42
TOP_Q, MIN_WIN, STRONG = 0.10, 0.20, 0.50
KR_MIN_TV = 5e8
SIGNALS = ("TREND", "HI60V", "HI20V", "RSACC", "EARLY")
NUM_FEATS = {"rs": "RS v2 점수", "ret20": "직전 20일 수익률", "ret60": "직전 60일 수익률", "ret120": "직전 120일 수익률",
             "distHigh": "52주 고가 대비 위치", "distLow": "52주 저가 대비 위치", "distSma50": "50일선 대비 거리", "slope50": "50일선 20일 기울기",
             "atrPct": "ATR14/종가", "range20": "최근 20일 고저폭/종가", "vol5v50": "5일/50일 거래량", "udVol": "상승일/하락일 거래량(50일)",
             "rsi": "RSI14", "up10": "최근 10일 상승일 수", "tv20": "20일 평균 거래대금", "rsChg": "RS v2 20일 변화"}
BIN_FEATS = {"trend": "8/8 (조건1~7 + RS>=80)", "c17": "조건 1~7 전부", "c1": "조건1 종가>150·200일선", "c2": "조건2 150>200일선",
             "c3": "조건3 200일선 상승", "c4": "조건4 50>150·200일선", "c5": "조건5 종가>50일선", "c6": "조건6 저가+30%", "c7": "조건7 고가-25% 이내",
             "rs80": "RS v2 >= 80", "rsUp15": "RS 20일 변화 >= +15", "above50up": "종가>50일선 & 50일선 상승"}


def listing(market: str) -> pd.DataFrame:
    import FinanceDataReader as fdr
    if market == "kr":
        try:
            from screening import fetch_stock_listing
            df = fetch_stock_listing("KRX")
        except Exception as exc:  # noqa: BLE001
            log.warning("KRX 목록 실패(%s) — latest_kr.json 사용", exc)
            rows = json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8"))
            df = pd.DataFrame([{"Code": r["code"], "Name": r.get("name"), "Market": r.get("market")} for r in rows])
        df = df[df["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])].copy()
        df["Market"] = df["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
        df = df[~df["Name"].map(is_preferred_kr) & ~df["Name"].map(is_spac_kr)]
        df["Sector"] = None
        try:
            desc = fdr.StockListing("KRX-DESC")
            sec = desc.set_index("Code")["Sector"] if "Sector" in desc else desc.set_index("Code")["Industry"]
            df["Sector"] = df["Code"].map(sec)
        except Exception as exc:  # noqa: BLE001
            log.warning("KRX 섹터 실패: %s", exc)
        df["sym"] = df["Code"].astype(str).str.zfill(6) + np.where(df["Market"] == "KOSDAQ", ".KQ", ".KS")
        return df[["sym", "Code", "Name", "Market", "Sector"]].drop_duplicates("sym")
    try:
        df = fdr.StockListing("S&P500").rename(columns={"Symbol": "Code"})
    except Exception as exc:  # noqa: BLE001
        log.warning("S&P500 목록 실패(%s) — latest_us.json 사용", exc)
        rows = json.loads((ROOT / "docs" / "data" / "latest_us.json").read_text(encoding="utf-8"))
        df = pd.DataFrame([{"Code": r["code"], "Name": r.get("name")} for r in rows])
    df["Code"] = df["Code"].astype(str).replace({"BRKB": "BRK-B", "BFB": "BF-B"}).str.replace(".", "-", regex=False)
    df["Market"] = "US"
    if "Sector" not in df:
        df["Sector"] = None
    df["sym"] = df["Code"]
    return df[["sym", "Code", "Name", "Market", "Sector"]].drop_duplicates("sym")


def index_close(sym: str) -> pd.Series:
    import yfinance as yf
    f = yf.download(sym, start=FETCH_START, interval="1d", auto_adjust=True, progress=False)
    c = f["Close"].iloc[:, 0] if isinstance(f.columns, pd.MultiIndex) else f["Close"]
    c.index = pd.DatetimeIndex(c.index).tz_localize(None)
    return c.dropna()


def build(data: dict[str, pd.DataFrame], bench_of: dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    for s, df in data.items():
        a = stock_features(df, bench_of[s])
        b = extra_features(df)
        f = a.join(b[["ret20", "ret60", "ret120", "distHigh", "distSma50", "slope50", "atrPct", "range20", "vol5v50", "udVol", "rsi", "up10"]])
        c, v, h = df["close"], df["volume"], df["high"]
        f["distLow"] = c / df["low"].rolling(252, min_periods=120).min() - 1
        f["tv20"] = (c * v).rolling(20).mean()
        sma50 = c.rolling(50).mean()
        f["above50up"] = (c > sma50) & (sma50 > sma50.shift(10))
        av50p = v.rolling(50).mean().shift(1)
        f["HI60V"] = (c >= h.rolling(60).max().shift(1)) & (v >= 1.5 * av50p)
        f["HI20V"] = (c >= h.rolling(20).max().shift(1)) & (v >= 1.5 * av50p)
        line = c / bench_of[s].reindex(c.index).ffill()
        f["rsLineHi63"] = line >= line.rolling(63, min_periods=40).max()
        f["code"] = s
        rows.append(f)
    p = pd.concat(rows)
    p.index.name = "date"
    p = add_rs(p.reset_index()).reset_index(drop=True)
    p["sym"] = p["code"]
    p["rsChg"] = p["rs"] - p["rs20"]
    p["trend"] = p["ok"] & p["c17"] & (p["rs"] >= CFG_NEW.rs.rs_min)
    p["rs80"] = p["rs"] >= 80
    p["rsUp15"] = p["rsChg"] >= 15
    p["TREND"] = p["trend"]
    p["RSACC"] = p["above50up"] & p["rsUp15"]
    p["EARLY"] = p["RSACC"] & (p["distLow"] >= 0.30) & p["rsLineHi63"]
    return p


def lift_numeric(t0: pd.DataFrame) -> list[dict]:
    base = t0["win"].mean()
    out = []
    for f, lab in NUM_FEATS.items():
        s = t0[f].replace([np.inf, -np.inf], np.nan)
        ok = s.notna()
        if ok.sum() < 50:
            continue
        q = pd.qcut(s[ok].rank(method="first"), 5, labels=False)
        bins = []
        for k in range(5):
            m = q == k
            sub = t0.loc[ok][m]
            bins.append({"q": k + 1, "lo": round(float(s[ok][m].min()), 4), "hi": round(float(s[ok][m].max()), 4), "n": int(m.sum()),
                         "lift": round(float(sub["win"].mean() / base), 2) if base else None})
        out.append({"feature": f, "label": lab, "medWin": round(float(s[t0["win"]].median()), 4), "medRest": round(float(s[~t0["win"]].median()), 4), "bins": bins})
    return out


def lift_binary(t0: pd.DataFrame) -> list[dict]:
    base = t0["win"].mean()
    out = []
    for f, lab in BIN_FEATS.items():
        m = t0[f].fillna(False).astype(bool)
        out.append({"feature": f, "label": lab, "share": round(float(m.mean() * 100), 1), "shareWin": round(float(m[t0["win"]].mean() * 100), 1),
                    "liftTrue": round(float(t0.loc[m, "win"].mean() / base), 2) if m.any() and base else None,
                    "liftFalse": round(float(t0.loc[~m, "win"].mean() / base), 2) if (~m).any() and base else None})
    return out


def analyze(market: str, lst: pd.DataFrame, bench_syms: dict[str, str]) -> dict:
    idx = {k: index_close(v) for k, v in bench_syms.items()}
    cal = idx["KOSPI" if market == "kr" else "US"].index
    T1, T0 = cal[-1], cal[-1 - WIN_DAYS]
    TE = cal[-1 - WIN_DAYS + EARLY_DAYS]
    data = fetch_ohlcv(list(lst["sym"]), start=FETCH_START)
    meta = lst.set_index("sym")
    bench_of = {s: idx["KOSDAQ" if s.endswith(".KQ") else ("KOSPI" if market == "kr" else "US")] for s in data}
    p = build(data, bench_of)
    p = p[(p["date"] >= cal[0])]
    close = p.pivot(index="date", columns="sym", values="close").sort_index().reindex(cal).ffill(limit=3)
    c0, c1 = close.loc[T0], close.loc[T1]
    win_slice = close.loc[T0:T1]
    t0 = p[p["date"] == T0].set_index("sym")
    t0 = t0[c0.reindex(t0.index).notna() & c1.reindex(t0.index).notna()]
    if market == "kr":
        t0 = t0[t0["tv20"] >= KR_MIN_TV]
    syms = t0.index
    R = (c1[syms] / c0[syms] - 1)
    peak = win_slice[syms].max() / c0[syms] - 1
    dd_now = c1[syms] / win_slice[syms].max() - 1
    above50 = (p[(p["date"] >= T0) & (p["date"] <= T1)].assign(a=lambda d: d["c5"]).groupby("sym")["a"].mean()).reindex(syms)
    thr = max(R.quantile(1 - TOP_Q), MIN_WIN)
    t0["R"], t0["win"], t0["strong"] = R, R >= thr, R >= STRONG
    base_idx = {k: round(float(v.loc[T1] / v.asof(T0) - 1) * 100, 1) for k, v in idx.items()}
    res = {"market": market, "T0": str(T0.date()), "T1": str(T1.date()), "earlyEnd": str(TE.date()), "universe": int(len(syms)),
           "indexReturnPct": base_idx, "medianReturnPct": round(float(R.median() * 100), 1), "winnerThresholdPct": round(float(thr * 100), 1),
           "winners": int(t0["win"].sum()), "strong": int(t0["strong"].sum()),
           "shareUpPct": round(float((R > 0).mean() * 100), 1)}
    # 상위 목록
    top = t0[t0["win"]].sort_values("R", ascending=False)
    first_sig = {}
    wp = p[(p["date"] >= T0) & (p["date"] < T1) & p["sym"].isin(top.index)]
    for sig in SIGNALS:
        first_sig[sig] = wp[wp[sig].fillna(False)].groupby("sym")["date"].min()
    lst_rows = []
    for s, r in top.head(40).iterrows():
        lst_rows.append({"sym": s, "name": meta.loc[s, "Name"] if s in meta.index else s, "sector": meta.loc[s, "Sector"] if s in meta.index else None,
                         "market": meta.loc[s, "Market"] if s in meta.index else market,
                         "R": round(float(r["R"] * 100), 1), "peak": round(float(peak[s] * 100), 1), "ddNow": round(float(dd_now[s] * 100), 1),
                         "above50Pct": round(float(above50[s] * 100), 0) if above50[s] == above50[s] else None,
                         "t0Trend": bool(r["trend"]), "t0Rs": None if pd.isna(r["rs"]) else round(float(r["rs"]), 0),
                         "t0DistHigh": None if pd.isna(r["distHigh"]) else round(float(r["distHigh"] * 100), 1),
                         **{f"first{sig}": (str(first_sig[sig][s].date()) if s in first_sig[sig].index else None) for sig in SIGNALS}})
    res["topWinners"] = lst_rows
    res["numericLift"] = lift_numeric(t0)
    res["binaryLift"] = lift_binary(t0)
    # 섹터
    t0["sector"] = [meta.loc[s, "Sector"] if s in meta.index else None for s in t0.index]
    t0["mkt"] = [meta.loc[s, "Market"] if s in meta.index else market for s in t0.index]
    base = t0["win"].mean()
    sec = t0.dropna(subset=["sector"]).groupby("sector").agg(n=("win", "size"), w=("win", "sum"))
    sec["lift"] = (sec["w"] / sec["n"]) / base
    res["sectorLift"] = [{"sector": k, "n": int(v["n"]), "winners": int(v["w"]), "lift": round(float(v["lift"]), 2)}
                         for k, v in sec[sec["w"] >= 2].sort_values("w", ascending=False).head(15).iterrows()]
    res["byMarket"] = {k: {"n": int(len(g)), "winners": int(g["win"].sum()), "medianR": round(float(g["R"].median() * 100), 1)} for k, g in t0.groupby("mkt")}
    # 포착 시점(WINNER 만)
    timing = {}
    for sig in SIGNALS:
        fs = first_sig[sig].reindex(top.index)
        caught = fs.dropna()
        rem = []
        for s, d in caught.items():
            pos = int(close.index.get_indexer([d], method="pad")[0])
            if pos < 0 or pos + 1 >= len(close.index):
                continue
            entry = close[s].iloc[pos + 1]
            tot = np.log(c1[s] / c0[s])
            rem.append(float(np.log(c1[s] / entry) / tot) if tot > 0 else np.nan)
        rem = np.array([x for x in rem if x == x])
        lag = [int(close.index.get_indexer([d], method="pad")[0]) - close.index.get_loc(T0) for d in caught]
        timing[sig] = {"caughtPct": round(100 * len(caught) / max(len(top), 1), 1), "medianDaysAfterT0": float(np.median(lag)) if lag else None,
                       "medianShareRemainingPct": round(float(np.median(rem) * 100), 1) if len(rem) else None,
                       "caughtWithHalfLeftPct": round(100 * float((rem >= 0.5).sum()) / max(len(top), 1), 1),
                       "t0AlreadyOnPct": round(100 * float(t0.loc[top.index, sig].fillna(False).mean()), 1)}
    res["timing"] = timing
    # 정밀도: 전반 구간 신호 → 이후 수익
    early = p[(p["date"] >= T0) & (p["date"] <= TE) & p["sym"].isin(syms)]
    prec = {}
    for sig in SIGNALS:
        fsig = early[early[sig].fillna(False)].groupby("sym")["date"].min()
        fwd, ex = [], []
        for s, d in fsig.items():
            pos = int(close.index.get_indexer([d], method="pad")[0])
            entry = close[s].iloc[pos + 1]
            fwd.append(float(c1[s] / entry - 1))
            same = close.iloc[pos + 1][syms]
            ex.append(float((c1[syms] / same - 1).median()))
        fwd, ex = np.array(fwd), np.array(ex)
        prec[sig] = {"stocks": int(len(fsig)), "becameWinnerPct": round(100 * float(t0.loc[fsig.index, "win"].mean()), 1) if len(fsig) else None,
                     "baseWinnerPct": round(100 * float(base), 1), "medianFwdPct": round(float(np.median(fwd) * 100), 1) if len(fwd) else None,
                     "medianUniverseSameDayPct": round(float(np.median(ex) * 100), 1) if len(ex) else None,
                     "winRateVsUniversePct": round(100 * float((fwd > ex).mean()), 1) if len(fwd) else None}
    res["precision"] = prec
    # 상승 경로
    w = top.index
    mdd_run = []
    for s in w:
        path = win_slice[s].dropna()
        mdd_run.append(float((path / path.cummax() - 1).min()))
    res["path"] = {"medianPeakPct": round(float(peak[w].median() * 100), 1), "medianDdFromPeakNowPct": round(float(dd_now[w].median() * 100), 1),
                   "medianMaxPullbackDuringPct": round(float(np.median(mdd_run) * 100), 1),
                   "shareWithPullbackOver15Pct": round(100 * float((np.array(mdd_run) <= -0.15).mean()), 1),
                   "medianAbove50Pct": round(float(above50[w].median() * 100), 0)}
    return res


def to_markdown(res: dict) -> str:
    o = ["# 최근 4개월 상승 종목 역분석", "", f"> 실행 {res['ranAt']} · 정의 2026-11-09 고정 · 탐색적 분석(가설) · 매수 신호 아님", "", "```", __doc__.strip(), "```", ""]
    for m in res["markets"].values():
        o += [f"## {m['market'].upper()} — {m['T0']} ~ {m['T1']} (전반 끝 {m['earlyEnd']})", "",
              f"유니버스 {m['universe']} · 지수 {m['indexReturnPct']}% · 종목 중앙값 {m['medianReturnPct']}% · 상승 종목 비율 {m['shareUpPct']}% · "
              f"WINNER 기준 {m['winnerThresholdPct']}% 이상 {m['winners']}개 · +50% 이상 {m['strong']}개 · 시장별 {m['byMarket']}", "",
              f"상승 경로(WINNER 중앙값): {m['path']}", "", "### 상위 WINNER",
              "| 종목 | 섹터 | R4M% | 구간최고% | 고점대비 지금% | 50일선 위 % | T0 8/8 | T0 RS | T0 52주고가대비% | 첫 TREND | 첫 HI60V | 첫 RSACC | 첫 EARLY |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in m["topWinners"]:
            o.append(f"| {r['name']} ({r['sym']}) | {r['sector']} | {r['R']} | {r['peak']} | {r['ddNow']} | {r['above50Pct']} | {'O' if r['t0Trend'] else '-'} | {r['t0Rs']} | {r['t0DistHigh']} | {r['firstTREND']} | {r['firstHI60V']} | {r['firstRSACC']} | {r['firstEARLY']} |")
        o += ["", "### T0 이진 특징 리프트 (WINNER 비율 / 전체 비율)", "| 특징 | 전체 중 % | WINNER 중 % | 참일 때 리프트 | 거짓일 때 리프트 |", "|---|---|---|---|---|"]
        for r in m["binaryLift"]:
            o.append(f"| {r['label']} | {r['share']} | {r['shareWin']} | {r['liftTrue']} | {r['liftFalse']} |")
        o += ["", "### T0 수치 특징 (5분위 리프트, 1분위=가장 낮음)", "| 특징 | WINNER 중앙값 | 나머지 중앙값 | Q1 | Q2 | Q3 | Q4 | Q5 |", "|---|---|---|---|---|---|---|---|"]
        for r in m["numericLift"]:
            o.append(f"| {r['label']} | {r['medWin']} | {r['medRest']} | " + " | ".join(f"{b['lift']} ({b['lo']}~{b['hi']})" for b in r["bins"]) + " |")
        o += ["", "### 섹터 리프트 (WINNER 2개 이상)", "| 섹터 | 종목 | WINNER | 리프트 |", "|---|---|---|---|"]
        for r in m["sectorLift"]:
            o.append(f"| {r['sector']} | {r['n']} | {r['winners']} | {r['lift']} |")
        o += ["", "### 포착 시점 (WINNER 기준)", "| 신호 | T0에 이미 켜짐% | 구간 중 포착% | T0 후 중앙 일수 | 진입 후 남은 상승 몫 중앙% | 절반 이상 남기고 포착% |", "|---|---|---|---|---|---|"]
        for k, v in m["timing"].items():
            o.append(f"| {k} | {v['t0AlreadyOnPct']} | {v['caughtPct']} | {v['medianDaysAfterT0']} | {v['medianShareRemainingPct']} | {v['caughtWithHalfLeftPct']} |")
        o += ["", "### 정밀도 (전반 구간에 신호가 뜬 모든 종목)", "| 신호 | 종목 수 | WINNER 된 % (기준 %) | 이후 수익 중앙% | 같은 날 전체 중앙% | 전체 중앙을 이긴 % |", "|---|---|---|---|---|---|"]
        for k, v in m["precision"].items():
            o.append(f"| {k} | {v['stocks']} | {v['becameWinnerPct']} ({v['baseWinnerPct']}) | {v['medianFwdPct']} | {v['medianUniverseSameDayPct']} | {v['winRateVsUniversePct']} |")
        o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    res = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "markets": {}}
    res["markets"]["us"] = analyze("us", listing("us"), {"US": "^GSPC"})
    res["markets"]["kr"] = analyze("kr", listing("kr"), {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "rally_4m.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT_DIR / "rally_4m.md").write_text(to_markdown(res), encoding="utf-8")
    print(to_markdown(res))


if __name__ == "__main__":
    main()
