"""무상증자결정 공시의 증자 비율을 받아, 비수정 가격이면 −30% 넘게 하락해야 하는 사건에서 저장 시세가 연속적인지 검증한다."""
import os, glob, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests, pandas as pd, numpy as np

ROOT = Path(__file__).resolve().parents[1]; S = ROOT / "data_store"; R = ROOT / "reports"; KEY = os.environ.get("DART_API_KEY", "")
md = []
def P(t): md.append(str(t) + "\n")
def T(df, idx=False): md.append(df.to_markdown(index=idx) + "\n")
def num(x):
    try: return float(str(x).replace(",", ""))
    except Exception: return np.nan

def call(r):
    d = pd.Timestamp(r.rcept).strftime("%Y%m%d")
    for _ in range(3):
        try:
            j = requests.get("https://opendart.fss.or.kr/api/fricDecsn.json", params={"crtfc_key": KEY, "corp_code": str(r.corp_code).zfill(8), "bgn_de": d, "end_de": d}, timeout=30).json(); break
        except Exception: j = {"status": "ERR"}; time.sleep(2)
    if j.get("status") != "000": return {"rcept_no": r.rcept_no, "status": j.get("status"), "msg": str(j.get("message"))[:40]}
    it = [x for x in j["list"] if x.get("rcept_no") == r.rcept_no] or j["list"]
    x = it[0]; return {"rcept_no": r.rcept_no, "status": "000", "new": num(x.get("nstk_ostk_cnt")), "old": num(x.get("bfic_tisstk_ostk")), "keys": ",".join(list(x.keys())[:12])}

def main():
    ev = pd.read_csv(R / "phase1_dart_events.csv", dtype=str); ev = ev[ev.type == "무상증자"].copy(); ev["rcept"] = pd.to_datetime(ev.rcept)
    with ThreadPoolExecutor(8) as ex: res = list(ex.map(call, ev.itertuples()))
    rs = pd.DataFrame(res); ev = ev.merge(rs, on="rcept_no", how="left")
    P(f"무상증자결정 공시 {len(ev)}건 조회: 성공 {int((ev.status=='000').sum())}, 실패 {int((ev.status!='000').sum())}. 응답 필드 예: {ev.keys.dropna().iloc[0] if ev.keys.notna().any() else '없음'}")
    ev["ratio"] = ev.new / ev.old; ev["expected_drop"] = 1 / (1 + ev.ratio) - 1
    px = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(S / "ohlcv_*.parquet")))], ignore_index=True)
    px["Date"] = pd.to_datetime(px.Date); px = px.sort_values(["ticker", "Date"]); px["ret"] = px.groupby("ticker").Close.pct_change()
    by = {t: g for t, g in px.groupby("ticker")}
    out = []
    for r in ev[ev.ratio.notna()].itertuples():
        if r.stock_code not in by: continue
        g = by[r.stock_code]; w = g[(g.Date >= r.rcept) & (g.Date <= r.rcept + pd.Timedelta(days=120))]
        if len(w) < 5: continue
        i = w.ret.idxmin()
        out.append({"ticker": r.stock_code, "name": r.corp_name, "rcept": r.rcept.date(), "증자비율": round(r.ratio, 3), "비수정시 예상 하락": round(r.expected_drop, 3),
                    "저장 최저 일수익률": round(float(w.loc[i, "ret"]), 4), "그 날짜": w.loc[i, "Date"].date()})
    o = pd.DataFrame(out); o.to_csv(R / "phase1_bonus_check.csv", index=False)
    det = o[o["비수정시 예상 하락"] <= -0.30]
    P(f"저장 시세와 대조된 무상증자 {len(o)}건. 이 중 비수정이면 −30% 이하로 떨어져야 하는 사건(증자비율 ≥ 0.4286) {len(det)}건.")
    if len(det):
        miss = det[det["저장 최저 일수익률"] <= -0.30]
        P(f"그 {len(det)}건 중 저장 시세에도 −30% 이하 급락이 있는 사건(수정 미반영 의심) {len(miss)}건 ({len(miss)/len(det):.1%}), 급락 없이 연속적인 사건(수정 반영 확인) {len(det)-len(miss)}건 ({1-len(miss)/len(det):.1%}).")
        P("검증 표본 5건 (예상 하락 −30% 이하 사건을 종목·날짜순 정렬한 앞 5건, 임의 선택 없음):"); T(det.sort_values(["ticker", "rcept"]).head(5))
        P("미반영 의심 사건 전체:"); T(miss)
    (R / "phase1_bonus_check.md").write_text("# 무상증자 비율 기반 수정주가 검증 (코드 실행 결과)\n" + "\n".join(md), encoding="utf-8"); print("written")
if __name__ == "__main__": main()
