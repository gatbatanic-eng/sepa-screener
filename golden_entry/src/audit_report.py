"""data_store 를 읽어 3장 감사 수치를 계산하고 reports/phase1_audit_auto.md (+ csv)를 만든다. 모든 숫자는 이 코드의 출력."""
import random, glob, time, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd
from src.collect import is_common

ROOT = Path(__file__).resolve().parents[1]; S = ROOT / "data_store"; R = ROOT / "reports"; R.mkdir(exist_ok=True)
md = []
def H(t): md.append(f"\n## {t}\n")
def P(t): md.append(str(t) + "\n")
def T(df, idx=False): md.append(df.to_markdown(index=idx) + "\n")

lst = pd.read_parquet(S / "listing.parquet"); dl = pd.read_parquet(S / "delisting.parquet")
st = pd.read_parquet(S / "fetch_status.parquet")
px = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(S / "ohlcv_*.parquet")))], ignore_index=True)
px["Date"] = pd.to_datetime(px["Date"]); px = px.sort_values(["ticker", "Date"]).reset_index(drop=True)
ix = pd.read_parquet(S / "index.parquet"); ix["Date"] = pd.to_datetime(ix["Date"])
cal = pd.DatetimeIndex(sorted(ix[ix["index"] == "KOSPI"]["Date"].unique()))
dl["DelistingDate"] = pd.to_datetime(dl["DelistingDate"], errors="coerce"); dl["ListingDate"] = pd.to_datetime(dl["ListingDate"], errors="coerce")
P(f"생성 시각 {dt.datetime.now():%Y-%m-%d %H:%M}, 수집 구간 {px.Date.min():%Y-%m-%d} ~ {px.Date.max():%Y-%m-%d}, 거래일 {len(cal)}일(KOSPI 지수 기준), 시세 행 {len(px):,}, 종목 {px.ticker.nunique():,}")

# 1 수집 현황
H("1. 수집 현황")
dlc = dl[dl["Market"].isin(["KOSPI", "KOSDAQ"]) & (dl["DelistingDate"] >= "2020-01-01")]
dlc = dlc[[is_common(r.Symbol, r.Name) for r in dlc.itertuples()]]
st["group"] = np.where(st.ticker.isin(dlc.Symbol), "상폐(2020~)", "현재 상장")
g = st.groupby("group").agg(요청=("ticker", "size"), 확보=("rows", lambda s: int((s > 0).sum())), 실패=("rows", lambda s: int((s == 0).sum())))
g["확보율"] = (g["확보"] / g["요청"]).round(4); T(g, True)
P("확보 소스별 종목 수:"); T(st[st.rows > 0].groupby("source").size().rename("종목수").to_frame(), True)
fails = st[st.rows == 0]; fails.to_csv(R / "phase1_fetch_failures.csv", index=False)
P(f"실패 종목 {len(fails)}개 전체 목록: reports/phase1_fetch_failures.csv. 실패 사유 상위:"); T(fails.error.str[:80].value_counts().head(8).rename("건수").to_frame(), True)

# 2 유니버스/생존편향
H("2. 연도별 유니버스와 이후 상폐")
first = px.groupby("ticker").Date.min(); okset = set(st[st.rows > 0].ticker)
cur = lst[lst["Market"].isin(["KOSPI", "KOSDAQ"])]; cur = cur[[is_common(r.Code, r.Name) for r in cur.itertuples()]]
rows = []
for d in ["2020-12-30", "2021-12-30", "2022-12-29", "2023-12-28", "2024-12-30", "2025-12-30"]:
    d = pd.Timestamp(d)
    c_in = [t for t in cur.Code if t in first.index and first[t] <= d + pd.Timedelta(days=5)]   # 현재 상장 종목 중 d 이전부터 시세가 있는 종목
    dl_in = dlc[(dlc.ListingDate <= d) & (dlc.DelistingDate > d)]
    later = len(dl_in); with_px = int(dl_in.Symbol.isin(okset).sum())
    rows.append({"기준일": d.date(), "현재상장 중 존재": len(c_in), "이후 상폐(목록 기준)": later, "그중 시세 확보": with_px,
                 "유니버스 합계(목록)": len(c_in) + later, "상폐 비율": round(later / max(1, len(c_in) + later), 4)})
T(pd.DataFrame(rows))
P("주의: 현재 상장 종목의 상장일은 소스에 없어 '수집 시작 이후 시세가 있는 종목'으로 근사. 2020-01 이전 상폐분은 수집 대상이 아니다.")

# 3 상폐 확보율
H("3. 상폐 종목 시세 확보율")
dlm = dlc.copy(); dlm["확보"] = dlm.Symbol.isin(okset); dlm["연도"] = dlm.DelistingDate.dt.year
t3 = dlm.groupby("연도").agg(상폐수=("Symbol", "size"), 확보=("확보", "sum")); t3["확보율"] = (t3["확보"] / t3["상폐수"]).round(3)
tot = pd.DataFrame({"상폐수": [t3.상폐수.sum()], "확보": [t3.확보.sum()], "확보율": [round(t3.확보.sum() / max(1, t3.상폐수.sum()), 3)]}, index=["합계"])
T(pd.concat([t3, tot]), True)
P("상폐 사유 상위(전체 상폐 대상):"); T(dlm.Reason.fillna("(없음)").str[:40].value_counts().head(8).rename("건수").to_frame(), True)

# 4 수정주가 검증
H("4. 수정주가 검증 (pykrx 수정 vs 비수정 비교로 액면분할·무상증자 사건 탐지)")
try:
    from pykrx import stock
    random.seed(0); pool = sorted(t for t in cur.Code if t in okset); samp = random.sample(pool, min(400, len(pool))); ev = []; scanned = 0
    def pair(tk):
        a = stock.get_market_ohlcv("20200101", dt.date.today().strftime("%Y%m%d"), tk, adjusted=True)
        u = stock.get_market_ohlcv("20200101", dt.date.today().strftime("%Y%m%d"), tk, adjusted=False)
        return a, u
    from concurrent.futures import ThreadPoolExecutor
    def scan(tk):
        try:
            a, u = pair(tk); j = pd.concat([a["종가"].rename("adj"), u["종가"].rename("unadj")], axis=1).dropna()
            j = j[(j > 0).all(axis=1)]; f = (j.adj / j.unadj); ch = f / f.shift(1)
            out = []
            for d in ch[(ch - 1).abs() > 0.2].index:
                i = j.index.get_loc(d)
                out.append({"ticker": tk, "date": d.date(), "factor": round(float(ch[d]), 4), "비수정 전일": j.unadj.iloc[i-1], "비수정 당일": j.unadj.iloc[i],
                            "수정 전일": j.adj.iloc[i-1], "수정 당일": j.adj.iloc[i], "비수정 수익률": round(j.unadj.iloc[i] / j.unadj.iloc[i-1] - 1, 4),
                            "수정 수익률": round(j.adj.iloc[i] / j.adj.iloc[i-1] - 1, 4)})
            return out
        except Exception: return []
    with ThreadPoolExecutor(6) as ex:
        for o in ex.map(scan, samp): ev += o
    P(f"표본 {len(samp)}종목(seed=0) 스캔, 수정/비수정 종가 비율이 하루에 20% 넘게 바뀐 사건 {len(ev)}건 발견.")
    if ev:
        ev = pd.DataFrame(ev).sort_values(["ticker", "date"]); ev.to_csv(R / "phase1_adjustment_events.csv", index=False)
        pick = ev.head(5); T(pick); 
        okc = (pick["수정 수익률"].abs() < 0.3).sum()
        P(f"선정 규칙: 사건을 종목코드·날짜 순으로 정렬한 앞 5건(임의 선택 없음). 수정주가 수익률이 ±30% 이내인 건 {okc}/{len(pick)}. 전체 사건 목록: reports/phase1_adjustment_events.csv")
        chk = []
        for r in pick.itertuples():
            s = px[(px.ticker == r.ticker) & (px.Date.between(pd.Timestamp(r.date) - pd.Timedelta(days=7), pd.Timestamp(r.date) + pd.Timedelta(days=7)))]
            s = s.reset_index(drop=True); i = s.index[s.Date == pd.Timestamp(r.date)]
            if len(i) and i[0] > 0: chk.append({"ticker": r.ticker, "date": r.date, "저장데이터 전일종가": float(s.Close[i[0]-1]), "저장데이터 당일종가": float(s.Close[i[0]]), "저장데이터 수익률": round(float(s.Close[i[0]] / s.Close[i[0]-1] - 1), 4)})
        P("우리가 저장한 데이터(주 소스)의 같은 사건 구간:"); T(pd.DataFrame(chk))
    else:
        P("사건이 발견되지 않았다 → 수정주가 연속성 검증은 이 방법으로는 수행하지 못함(비수정 가격을 받지 못했거나 표본에 사건 없음).")
except Exception as e:
    P(f"수정주가 검증 실행 실패: {type(e).__name__} {str(e)[:150]}. 이 항목은 미수행.")

# 5 결측/정지
H("5. 결측일, 거래정지, 0 거래량")
g = px.groupby("ticker")
span = g.Date.agg(["min", "max", "size"])
exp = span.apply(lambda r: int(((cal >= r["min"]) & (cal <= r["max"])).sum()), axis=1)
miss = (exp - span["size"]).clip(lower=0); extra = (span["size"] - exp).clip(lower=0)
P(f"기대 거래일 합계 {int(exp.sum()):,}, 결측일 합계 {int(miss.sum()):,} ({miss.sum()/exp.sum():.4%}). 결측 있는 종목 {(miss>0).sum()}개, 결측 6일 이상 종목 {(miss>5).sum()}개. 달력에 없는 날짜 행(초과) 합계 {int(extra.sum())}.")
T(pd.DataFrame({"결측일": miss, "기대일": exp, "첫날": span["min"].dt.date, "끝날": span["max"].dt.date}).sort_values("결측일", ascending=False).head(15), True)
z = px[px.Volume == 0].copy(); P(f"거래량 0인 행 {len(z):,}개 ({len(z)/len(px):.4%}), 해당 종목 {z.ticker.nunique()}개.")
px["_z"] = (px.Volume == 0).astype(int); px["_run"] = px.groupby("ticker")["_z"].transform(lambda s: s.groupby((s != s.shift()).cumsum()).cumsum())
runs = px[px._z == 1].groupby("ticker")["_run"].max()
P(f"거래량 0이 연속 5거래일 이상인 종목(거래정지 후보) {(runs>=5).sum()}개, 최장 {int(runs.max()) if len(runs) else 0}일. 거래정지 여부 자체는 소스에 없어 거래량 0·결측으로만 근사한다.")
T(runs.sort_values(ascending=False).head(10).rename("최장 연속 0거래량(일)").to_frame(), True)

# 6 이상치
H("6. 일일 수익률 ±30% 초과")
px["ret"] = px.groupby("ticker").Close.pct_change(); px["gap"] = px.groupby("ticker").Date.diff().dt.days
o = px[px.ret.abs() > 0.3][["ticker", "Date", "Close", "ret", "gap", "Volume"]].copy(); o["Date"] = o.Date.dt.date
o.to_csv(R / "phase1_outliers.csv", index=False)
P(f"±30% 초과 행 {len(o)}개 ({len(o)/px.ret.notna().sum():.5%}), 종목 {o.ticker.nunique()}개. 이전 행과 7일 넘게 떨어진 경우 {(o.gap>7).sum()}건. 전체 목록: reports/phase1_outliers.csv. 절댓값 상위 25:")
T(o.reindex(o.ret.abs().sort_values(ascending=False).index).head(25))
P("연도별 건수:"); T(pd.to_datetime(o.Date).dt.year.value_counts().sort_index().rename("건수").to_frame(), True)
P("한국 가격제한폭이 ±30%이므로 이를 넘는 값은 액면분할·병합·신규상장·거래재개 또는 데이터 오류 후보다(수정주가 미반영 의심).")
(R / "phase1_audit_auto.md").write_text("# Phase 1 자동 감사 출력 (코드 실행 결과)\n" + "\n".join(md), encoding="utf-8")
print("written")
