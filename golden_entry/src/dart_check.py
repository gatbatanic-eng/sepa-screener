"""DART 공시(액면분할·감자/병합·무상증자)와 저장 시세를 대조해 수정주가 반영 여부를 검증한다. 키는 출력하지 않는다."""
import os, re, time, glob, datetime as dt
from pathlib import Path
import requests, pandas as pd, numpy as np

ROOT = Path(__file__).resolve().parents[1]; S = ROOT / "data_store"; R = ROOT / "reports"
KEY = os.environ.get("DART_API_KEY", "")
URL = "https://opendart.fss.or.kr/api/list.json"
md = []
def H(t): md.append(f"\n## {t}\n")
def P(t): md.append(str(t) + "\n")
def T(df, idx=False): md.append(df.to_markdown(index=idx) + "\n")

def fetch_window(b, e, ty):
    out, page = [], 1
    while True:
        for k in range(3):
            try:
                r = requests.get(URL, params={"crtfc_key": KEY, "bgn_de": b, "end_de": e, "pblntf_ty": ty, "page_no": page, "page_count": 100}, timeout=30).json(); break
            except Exception as ex:
                r = {"status": "ERR", "message": type(ex).__name__}; time.sleep(2)
        if r.get("status") == "013": return out, "000"
        if r.get("status") != "000": return out, f"{r.get('status')} {r.get('message')}"
        out += r["list"]
        if page >= int(r.get("total_page", 1)): return out, "000"
        page += 1; time.sleep(0.1)

def main():
    if not KEY:
        P("DART_API_KEY 가 설정되지 않아 DART 대조를 수행하지 못했다."); return finish()
    rows, errs, wins = [], [], []
    d0 = dt.date(2020, 1, 1)
    while d0 <= dt.date.today():
        d1 = min(d0 + dt.timedelta(days=85), dt.date.today()); wins.append((d0, d1)); d0 = d1 + dt.timedelta(days=1)
    from concurrent.futures import ThreadPoolExecutor
    def run(w):      # B 주요사항보고(증자·감자·분할 결정 공시). 거래소공시(I)는 용량이 커서 제외 -> 한계로 보고
        lst, st = fetch_window(w[0].strftime("%Y%m%d"), w[1].strftime("%Y%m%d"), "B")
        print(f"{w[0]}~{w[1]} B {len(lst)}건 {st}", flush=True); return w, lst, st
    with ThreadPoolExecutor(5) as ex:
        for w, lst, st in ex.map(run, wins):
            if st != "000": errs.append(f"{w[0]}~{w[1]} B: {st}")
            rows += lst
    windows = len(wins)
    df = pd.DataFrame(rows)
    P(f"조회 창 {windows}개, 공시 {len(df):,}건 수신, 조회 오류 {len(errs)}건" + (f": {errs[:5]}" if errs else ""))
    if df.empty: return finish()
    df = df[df.stock_code.fillna("").str.len() == 6].copy()
    nm = df.report_nm
    typ = np.select([nm.str.contains("무상증자"), nm.str.contains("액면분할|주식분할"), nm.str.contains("감자|주식병합|액면병합|주식의병합")],
                    ["무상증자", "액면분할", "감자·병합"], "")
    df["type"] = typ; ev = df[(df.type != "") & ~df.report_nm.str.contains("정정|철회|취소")].copy()
    ev["rcept"] = pd.to_datetime(ev.rcept_dt); ev = ev.drop_duplicates(["stock_code", "type", "rcept"])
    ev.to_csv(R / "phase1_dart_events.csv", index=False)

    px = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(S / "ohlcv_*.parquet")))], ignore_index=True)
    px["Date"] = pd.to_datetime(px.Date); px = px.sort_values(["ticker", "Date"]).reset_index(drop=True)
    px["ret"] = px.groupby("ticker").Close.pct_change()
    byt = {t: g for t, g in px.groupby("ticker")}
    ev = ev[ev.stock_code.isin(byt)].copy()
    H("A. DART 사건 수")
    T(ev.groupby("type").size().rename("건수(저장 종목 한정)").to_frame(), True)
    H("B. 사건 후 150일 내 저장 시세의 최대 급변")
    res = []
    for r in ev.itertuples():
        g = byt[r.stock_code]; w = g[(g.Date >= r.rcept) & (g.Date <= r.rcept + pd.Timedelta(days=150))]
        if len(w) < 5: continue
        i = w.ret.abs().idxmax(); d = w.loc[i]
        pre = g[(g.Date < d.Date)].tail(5).Volume; post = g[g.Date >= d.Date].head(5).Volume
        vr = (post[post > 0].mean() / pre[pre > 0].mean()) if (pre > 0).any() and (post > 0).any() else np.nan
        res.append({"type": r.type, "ticker": r.stock_code, "name": r.corp_name, "rcept": r.rcept.date(), "report": r.report_nm[:30],
                    "급변일": d.Date.date(), "급변 수익률": round(float(d.ret), 4), "거래량 비(후/전5일)": round(float(vr), 2) if vr == vr else None})
    rs = pd.DataFrame(res); rs.to_csv(R / "phase1_dart_check.csv", index=False)
    P(f"저장 시세와 대조 가능한 사건 {len(rs)}건 (사건 후 150일 내 시세 5행 이상).")
    # 방향별 미반영 판정: 분할·무상 -> 급락(<=-30%), 감자·병합 -> 급등(>=+30%). 가격제한폭(±30%)을 넘는 일일 변동은 정상 거래로 불가능.
    def flag(x): return (x["급변 수익률"] <= -0.3) if x["type"] in ("액면분할", "무상증자") else (x["급변 수익률"] >= 0.3)
    rs["미반영 의심"] = rs.apply(flag, axis=1)
    t = rs.groupby("type")["미반영 의심"].agg(["size", "sum"]).rename(columns={"size": "사건수", "sum": "급변(±30% 초과, 예상 방향)"})
    t["비율"] = (t.iloc[:, 1] / t["사건수"]).round(3); T(t, True)
    P("해석 기준: 수정주가가 정상 반영됐다면 사건 구간에 예상 방향의 ±30% 초과 일일 변동이 없다. 분할 비율이 작은 사건(예: 무상증자 20%)은 이 방법으로 구분되지 않아 '의심' 비율은 하한에 가깝다.")

    H("C. 사건 유형별 표본 5건 (종목·날짜순 앞 5건, 임의 선택 없음)")
    for ty in ("액면분할", "무상증자", "감자·병합"):
        P(f"**{ty}**"); T(rs[rs.type == ty].sort_values(["ticker", "rcept"]).head(5))

    H("D. 이상치 799행 대조")
    out = pd.read_csv(R / "phase1_outliers.csv"); out["Date"] = pd.to_datetime(out.Date); out["ticker"] = out.ticker.astype(str).str.zfill(6)
    exp = []
    for r in out.itertuples():
        e = ev[(ev.stock_code == r.ticker) & (ev.rcept <= r.Date) & (ev.rcept + pd.Timedelta(days=150) >= r.Date)]
        exp.append(",".join(sorted(set(e.type))) if len(e) else "")
    out["dart_event"] = exp; out.to_csv(R / "phase1_outliers_dart.csv", index=False)
    n = len(out); m = (out.dart_event != "").sum()
    P(f"이상치 {n}행 중 해당 종목에 150일 이내 선행하는 DART 분할·감자·무상증자 공시가 있는 행 {m}행 ({m/n:.1%}), 없는 행 {n-m}행.")
    T(out[out.dart_event != ""].dart_event.value_counts().rename("행수").to_frame(), True)
    P("공시가 없는 이상치(상위 15, 절댓값순):"); T(out[out.dart_event == ""].reindex(out[out.dart_event == ""].ret.abs().sort_values(ascending=False).index).head(15)[["ticker", "Date", "Close", "ret", "Volume"]])
    finish()

def finish():
    (R / "phase1_dart_check.md").write_text("# Phase 1 DART 대조 출력 (코드 실행 결과)\n" + "\n".join(md), encoding="utf-8"); print("written")

if __name__ == "__main__": main()
