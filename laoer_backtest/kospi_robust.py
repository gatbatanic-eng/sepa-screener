"""코스피 검증 보강: (1) 스프레드 민감도 (2) 실제 122630 데이터만으로 2011~ 재검증.
  python -m laoer_backtest.kospi_robust
"""
import pandas as pd

from . import kospi_study as K, sim, strategies as S

PICK = {"2배 보유": None,
        "30% 추세없음": lambda: S.VolTarget2(target_vol=0.30),
        "35% 50/200": lambda: S.VolTarget2(target_vol=0.35, mom="cross", k=0.5),
        "40% 50/200": lambda: S.VolTarget2(target_vol=0.40, mom="cross", k=0.5),
        "40% 200일선현금": lambda: S.VolTarget2(target_vol=0.40, mom="ma", ma=200, k=0.0)}


def go(df2, df1, start, label):
    rows = []
    for n, mk in PICK.items():
        st = S.LumpSum() if mk is None else mk()
        d = df2.copy(); d["U"] = df1["Close"].reindex(d.index).ffill()
        res = sim.backtest(d, st, capital=10000.0, fee=K.FEE, cash_rate=K.kr_rate(d.index))[0]
        s = sim.stats(res.loc[start:])
        rows.append((label, n, f"{s['CAGR']:.1%}", f"{s['MDD']:.1%}", f"{s['칼마']:.1f}"))
    return rows


def main():
    df1 = K.synth(1.0)
    rows = []
    for sp in (0.0, -0.02, -0.0428):
        rows += go(K.synth(2.0, sp), df1, "1998-01-01", f"합성 스프레드{sp:+.3f} 1998~")
        rows += go(K.synth(2.0, sp), df1, "2011-01-01", f"합성 스프레드{sp:+.3f} 2011~")
    real = K.load("ETF122630")[["Open", "High", "Low", "Close"]]
    real = real[(real > 0).all(axis=1)]
    rows += go(real, df1, real.index[260], "실제122630 (워밍업 후)")
    print(pd.DataFrame(rows, columns=["조건", "전략", "CAGR", "MDD", "칼마"]).to_string(index=False))


if __name__ == "__main__":
    main()
