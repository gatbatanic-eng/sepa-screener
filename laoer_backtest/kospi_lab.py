"""국장 전략 탐색 (지수 레버리지 외 전략 포함).
  python -m laoer_backtest.kospi_lab

블록 A (1997~, 코스피 지수 기반 합성): 지수 타이밍·눌림목 매수·레버리지 낙폭방어 등
블록 B (2012~, 실제 국내 상장 ETF 수정주가): 업종 순환, 모멘텀, 채권·금·미국 혼합 자산배분
비용은 편도 0.1%(B), 0.05%(A). 세금은 반영하지 않음(국내 주식형 ETF는 매매차익 비과세, 그 외는 15.4%).
"""
import numpy as np
import pandas as pd

from . import kospi_study as K
from .kospi_mix import sim as sim_expo, targets

OUT = K.OUT
DIV = 0.015  # 코스피 배당수익률 근사 (가격지수에 더함)


def metrics(eq, start=None, end=None):
    e = eq.loc[start:end]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
    dd = e / e.cummax() - 1
    r = e.pct_change().dropna()
    return dict(CAGR=cagr, MDD=dd.min(), 변동성=r.std() * np.sqrt(252), 칼마=cagr / -dd.min(),
                샤프=(r.mean() * 252) / (r.std() * np.sqrt(252)))


# ------------------------------------------------------------------ 블록 A
def rsi(close, n=2):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def block_a():
    d1, d2 = K.synth(1.0), K.synth(2.0, -0.0428)
    k = d1["Close"]
    r1 = k.pct_change().fillna(0) + DIV / 252
    r2 = d2["Close"].pct_change().fillna(0)
    rate = K.kr_rate(k.index)
    n = len(k); warm = np.arange(n) > 260
    sig = lambda s: s.shift(1).fillna(0).to_numpy()  # 전일 정보로 판단
    ma = lambda m: (k > k.rolling(m).mean()).astype(float)
    mom = lambda m: (k > k.shift(m)).astype(float)
    r = rsi(k, 2)
    pos = np.zeros(n); hold = 0; days = 0
    for i in range(n):  # 눌림목: RSI2<10 매수, RSI2>65 또는 10일 보유 후 청산
        if hold:
            days += 1
            if r.iloc[i] > 65 or days >= 10:
                hold = 0
        elif r.iloc[i] < 10:
            hold, days = 1, 0
        pos[i] = hold
    dip = pd.Series(pos, index=k.index)
    dip_tr = dip * (k > k.rolling(200).mean())
    cfg = {
        "코스피 1배 보유": np.where(warm, 1.0, 0.0),
        "200일선 위에서만 보유(아래는 현금)": sig(ma(200)),
        "10개월(210일) 모멘텀 타이밍": sig(mom(210)),
        "50/200 골든크로스": sig((k.rolling(50).mean() > k.rolling(200).mean()).astype(float)),
        "눌림목 매수(RSI2<10, 평균 며칠 보유)": sig(dip),
        "눌림목 매수 + 200일선 위에서만": sig(dip_tr),
        "평소 200일선 위 1배 / 눌림목엔 2배": sig(ma(200)) + sig(dip_tr),
        "낙폭방어 레버(2배→절반)": targets(k, 99, dd_in=0.15).copy(),
        "낙폭방어 레버(1.5배 상한)": np.minimum(targets(k, 99, dd_in=0.15), 1.5),
        "낙폭방어 2배 + 200일선 아래는 현금": targets(k, 99, dd_in=0.15) * sig(ma(200)),
        "변동성조절 35% + 낙폭방어": targets(k, 0.35, dd_in=0.15).copy(),
    }
    rows = []
    for name, E in cfg.items():
        E = np.asarray(E, float).copy(); E[:260] = 0
        eq, ex = sim_expo(r1, r2, rate, E)
        row = {"전략": name, "평균노출": ex.loc["1998":].mean()}
        for lab, a, b in (("1998~2010", "1998-01-01", "2010-12-31"), ("2011~", "2011-01-01", None), ("전체 1998~", "1998-01-01", None)):
            m = metrics(eq, a, b)
            row[f"{lab} CAGR"], row[f"{lab} MDD"] = m["CAGR"], m["MDD"]
        row["전체 칼마"] = metrics(eq, "1998-01-01")["칼마"]
        rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 블록 B
EQ = ["KODEX200", "코스닥150", "반도체", "2차전지", "은행", "자동차", "바이오", "IT", "건설", "배당성장", "삼성그룹",
      "에너지화학", "철강", "증권", "헬스케어", "경기소비재"]
DEF = ["국고채3년", "국고채10년", "골드", "미국나스닥100", "달러선물"]
CODES = {"KODEX200": "069500", "코스닥150": "229200", "반도체": "091160", "2차전지": "305720", "은행": "091170",
         "자동차": "091180", "바이오": "244580", "IT": "139260", "건설": "117700", "배당성장": "211900",
         "삼성그룹": "102780", "에너지화학": "117460", "철강": "117680", "증권": "102970", "헬스케어": "266420",
         "경기소비재": "266390", "국고채3년": "114260", "국고채10년": "148070", "골드": "132030",
         "미국나스닥100": "133690", "달러선물": "261240"}


def prices():
    cols = {}
    for n, c in CODES.items():
        d = K.load(f"E_{c}") if False else pd.read_csv(f"{K.DIR}/E_{c}.csv", index_col=0, parse_dates=True)["Close"]
        cols[n] = d[~d.index.duplicated()]
    p = pd.DataFrame(cols).sort_index()
    p = p.loc["2011-01-01":]
    p = p.ffill(limit=5)
    p["현금"] = (1 + K.kr_rate(p.index) / 365 * pd.Series(p.index.to_series().diff().dt.days.fillna(1).values, index=p.index)).cumprod()
    return p


def run_alloc(p, pick, start="2012-01-02", fee=0.001, rebal="ME"):
    """pick(p, i, ret) -> {자산: 비중}. 월말 신호, 다음 거래일 종가 체결(드리프트 후 월 단위 재조정)."""
    R = p.pct_change().fillna(0)
    ids = np.where(p.index >= start)[0]
    dates = p.index
    me = pd.Series(dates, index=dates).groupby([dates.year, dates.month]).max().values
    sig_days = set(me)
    cols = list(p.columns)
    v = np.zeros(len(cols)); v[cols.index("현금")] = 1.0
    eq = []; pend = None; turn = 0.0
    for i in ids:
        v = v * (1 + R.iloc[i].to_numpy())
        if pend is not None:
            tot = v.sum(); tgt = np.array([pend.get(c, 0.0) for c in cols]) * tot
            t = np.abs(tgt - v).sum() / 2 / tot
            turn += t; tot -= np.abs(tgt - v).sum() / 2 * fee * 2; v = tgt / tgt.sum() * tot; pend = None
        eq.append(v.sum())
        if dates[i] in sig_days:
            pend = pick(p, i)
    s = pd.Series(eq, index=dates[ids])
    return s / s.iloc[0], turn / (len(s) / 252)


def avail(p, i, lb):
    return [c for c in EQ if i >= lb and not np.isnan(p[c].iloc[i - lb]) and not np.isnan(p[c].iloc[i])]


def ret(p, i, c, lb):
    return p[c].iloc[i] / p[c].iloc[i - lb] - 1


def top_n(n, lb, bond="국고채3년", filt=None, score="ret", pool=None):
    def f(p, i):
        cands = [c for c in avail(p, i, lb) if (pool is None or c in pool)]
        if filt and not filt(p, i):
            return {bond: 1.0}
        sc = {}
        for c in cands:
            r = ret(p, i, c, lb)
            if score == "ret":
                sc[c] = r
            else:
                v = p[c].pct_change().iloc[i - 60:i + 1].std() * np.sqrt(252)
                sc[c] = r / max(v, 1e-6)
        best = [c for c, _ in sorted(sc.items(), key=lambda x: -x[1])[:n] if ret(p, i, c, lb) > 0]
        w = {c: 1 / n for c in best}
        w[bond] = w.get(bond, 0) + 1 - len(best) / n
        return w
    return f


def mkt_ok(p, i):
    return p["KODEX200"].iloc[i] > p["KODEX200"].iloc[i - 200:i + 1].mean()


def block_b():
    p = prices()
    s = {
        "KODEX200 보유": lambda p, i: {"KODEX200": 1.0},
        "미국나스닥100(원화) 보유": lambda p, i: {"미국나스닥100": 1.0},
        "200 60 / 국채 40": lambda p, i: {"KODEX200": .6, "국고채3년": .4},
        "4자산 균등(200·나스닥·국채10·금)": lambda p, i: {"KODEX200": .25, "미국나스닥100": .25, "국고채10년": .25, "골드": .25},
        "200일선 타이밍(아래면 국채)": lambda p, i: {"KODEX200": 1.0} if mkt_ok(p, i) else {"국고채3년": 1.0},
        "듀얼모멘텀(200 vs 나스닥, 12개월)": lambda p, i: (lambda a, b: {"KODEX200": 1.0} if a >= b and a > 0 else {"미국나스닥100": 1.0} if b > a and b > 0 else {"국고채3년": 1.0})(ret(p, i, "KODEX200", 252), ret(p, i, "미국나스닥100", 252)),
        "공격형 듀얼(200·코스닥·나스닥·금, 6개월 1위)": top_n(1, 126, pool=["KODEX200", "코스닥150", "미국나스닥100", "골드"]) if False else None,
    }
    s.pop("공격형 듀얼(200·코스닥·나스닥·금, 6개월 1위)")
    for n in (1, 3, 5):
        for lb in (63, 126, 252):
            s[f"업종 상위{n} 모멘텀 {lb//21}개월"] = top_n(n, lb)
    s["업종 상위3 6개월 + 코스피 200일선 필터"] = top_n(3, 126, filt=mkt_ok)
    s["업종 상위3 12개월 + 코스피 200일선 필터"] = top_n(3, 252, filt=mkt_ok)
    s["업종 상위3 위험조정(6개월)"] = top_n(3, 126, score="risk")
    s["업종 상위5 위험조정(6개월)+필터"] = top_n(5, 126, filt=mkt_ok, score="risk")
    # 업종 + 미국/금 포함 전체 모멘텀
    def glob(n, lb):
        def f(p, i):
            c = [x for x in EQ + ["미국나스닥100", "골드"] if i >= lb and not np.isnan(p[x].iloc[i - lb]) and not np.isnan(p[x].iloc[i])]
            sc = sorted(((ret(p, i, x, lb), x) for x in c), reverse=True)[:n]
            w = {x: 1 / n for r_, x in sc if r_ > 0}
            w["국고채3년"] = 1 - sum(w.values())
            return w
        return f
    s["전체(업종·나스닥·금) 상위3 6개월"] = glob(3, 126)
    s["전체(업종·나스닥·금) 상위3 12개월"] = glob(3, 252)
    rows = []
    for name, f in s.items():
        eq, turn = run_alloc(p, f)
        row = {"전략": name, "연회전율": turn}
        for lab, a in (("2012~", "2012-01-01"), ("2018~", "2018-01-01")):
            m = metrics(eq, a)
            row[f"{lab} CAGR"], row[f"{lab} MDD"] = m["CAGR"], m["MDD"]
        row["칼마(2012~)"] = metrics(eq, "2012-01-01")["칼마"]
        row["샤프(2012~)"] = metrics(eq, "2012-01-01")["샤프"]
        rows.append(row)
    return pd.DataFrame(rows)


def show(df):
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 60)
    f = df.copy()
    for c in f.columns[1:]:
        f[c] = f[c].map(lambda v: f"{v:.1%}" if ("CAGR" in c or "MDD" in c or "노출" in c or "회전" in c) else f"{v:.2f}")
    print(f.to_string(index=False))


def main():
    a = block_a(); b = block_b()
    a.to_csv(f"{OUT}/kospi_lab_A.csv", index=False, encoding="utf-8-sig")
    b.to_csv(f"{OUT}/kospi_lab_B.csv", index=False, encoding="utf-8-sig")
    print("== 블록 A: 코스피 지수 기반 (합성, 1998~) =="); show(a)
    print("\n== 블록 B: 국내 상장 ETF (실제 수정주가, 2012~) =="); show(b)


if __name__ == "__main__":
    main()
