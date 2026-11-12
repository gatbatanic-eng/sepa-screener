"""코스피 1~2배 연속 조절 + 낙폭 방어 백테스트.
  python -m laoer_backtest.kospi_mix

노출도 E = min(2, 목표변동성 / 코스피 20일 변동성) (지수 변동성 기준). E<=1 이면 1배 ETF에 E, 나머지 현금.
1<E<=2 이면 2배 ETF 비중 E-1, 1배 ETF 비중 2-E. 신호는 전일까지 데이터, 체결은 당일 종가.
낙폭 방어(dd): 지수가 250일 고점 대비 -dd_in 이하면 E x dd_k, -dd_out 보다 회복하면 해제.
"""
import numpy as np
import pandas as pd

from . import kospi_study as K

BAND = 0.10


def sim(r1, r2, rate, E_fn, fee=K.FEE, start=260):
    n = len(r1)
    cash_d = rate.to_numpy() / 365 * np.r_[1, np.diff(r1.index.to_numpy().astype("datetime64[D]").astype(int))]
    v = np.array([0.0, 0.0, 1.0]); eq = np.empty(n); expo = np.zeros(n)
    idx_r1 = r1.to_numpy(); idx_r2 = r2.to_numpy()
    E_t = E_fn  # 사전 계산된 목표 노출도 (전일 정보로 계산, 인덱스 t 에 적용)
    for t in range(n):
        v = v * np.array([1 + idx_r1[t], 1 + idx_r2[t], 1 + cash_d[t]])
        tot = v.sum()
        if t >= start:
            E = E_t[t]
            cur = (v[0] + 2 * v[1]) / tot
            if abs(cur - E) > BAND or (E <= 0.001 < cur):
                a = max(0.0, E - 1); b = min(E, 2 - E) if E > 1 else E
                tgt = np.array([b, a, 1 - a - b]) * tot
                turn = np.abs(tgt[:2] - v[:2]).sum()
                tot -= turn * fee
                v = np.array([b, a, 1 - a - b]) * tot
        eq[t] = v.sum(); expo[t] = (v[0] + 2 * v[1]) / eq[t]
    return pd.Series(eq, index=r1.index), pd.Series(expo, index=r1.index)


def stats(eq, start):
    e = eq.loc[start:]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
    mdd = (e / e.cummax() - 1).min()
    return cagr, mdd


def targets(close, tv, lb=20, dd_in=None, dd_out=0.075, dd_k=0.5, cap=2.0):
    r = np.log(close).diff()
    vol = r.rolling(lb).std() * np.sqrt(252)
    E = (tv / vol).clip(upper=cap).shift(1)
    if dd_in:
        dd = close / close.rolling(250, min_periods=1).max() - 1
        flag = np.zeros(len(close), bool); on = False
        for i, d in enumerate(dd.to_numpy()):
            on = (d <= -dd_in) if not on else (d < -dd_out)
            flag[i] = on
        E = E * np.where(pd.Series(flag, index=close.index).shift(1).fillna(False), dd_k, 1.0)
    return E.fillna(0).to_numpy()


def run(df1, df2, label, start_list, rows):
    k = df1["Close"]
    r1, r2 = df1["Close"].pct_change().fillna(0), df2["Close"].pct_change().fillna(0)
    rate = K.kr_rate(df1.index)
    cfgs = {"1배 보유": np.where(np.arange(len(k)) > 260, 1.0, 0.0), "2배 보유": np.where(np.arange(len(k)) > 260, 2.0, 0.0)}
    for tv in (0.26, 0.30, 0.35, 0.40, 0.50):
        cfgs[f"지수변동성 {tv:.0%}"] = targets(k, tv)
        cfgs[f"지수변동성 {tv:.0%}+낙폭방어"] = targets(k, tv, dd_in=0.15)
    cfgs["낙폭방어만(2배)"] = targets(k, 99, dd_in=0.15)
    cfgs["낙폭방어만(1.5배)"] = np.minimum(targets(k, 99, dd_in=0.15), 1.5)
    for name, E in cfgs.items():
        eq, ex = sim(r1, r2, rate, E)
        for st in start_list:
            c, m = stats(eq, st)
            rows.append((label, st[:4] + "~", name, c, m, c / -m, ex.loc[st:].mean()))


def main():
    rows = []
    for sp in (-0.02, -0.0428):
        d1, d2 = K.synth(1.0), K.synth(2.0, sp)
        run(d1, d2, f"합성 스프레드{sp:+.3f}", ["1998-01-01", "2011-01-01"], rows)
    r2 = K.load("ETF122630")[["Close"]]; r1 = K.load("ETF069500")[["Close"]]
    idx = r2.index.intersection(r1.index)
    run(r1.loc[idx], r2.loc[idx], "실제 ETF(069500/122630)", [str(idx[300].date())], rows)
    df = pd.DataFrame(rows, columns=["조건", "구간", "전략", "CAGR", "MDD", "칼마", "평균노출"])
    df.to_csv(f"{K.OUT}/kospi_mix.csv", index=False, encoding="utf-8-sig")
    for (cond, per), g in df.groupby(["조건", "구간"], sort=False):
        print(f"\n[{cond} / {per}]")
        print(g.drop(columns=["조건", "구간"]).to_string(index=False, formatters={
            "CAGR": "{:.1%}".format, "MDD": "{:.1%}".format, "칼마": "{:.2f}".format, "평균노출": "{:.2f}".format}))


if __name__ == "__main__":
    main()
