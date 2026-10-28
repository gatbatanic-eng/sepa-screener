"""라오어 무한매수법 백테스트 엔진 (V1 기본형 근사)

가정 (원작 규칙을 단순화한 것):
- 원금을 N(기본 40)회로 분할, 하루 최대 1회분 매수
- 1회분의 절반: 평단 이하 LOC (종가 <= 평단이면 종가 체결)
- 1회분의 나머지 절반: 큰수 LOC (종가 <= 평단*(1+big)이면 체결, 사실상 대부분 체결)
- 첫 매수일은 종가로 1회분 전량 매수
- 매도: 평단*(1+target) 지정가. 당일 고가가 목표가 이상이면 목표가에 전량 매도 → 사이클 종료
- 원금 소진(N회 다 씀) 시: quarter_cut=True면 보유수량 1/4을 종가 매도해 그 금액을 다시 매수 여력으로(쿼터손절 근사),
  False면 익절가 도달까지 보유만
- 사이클 종료 시 수익은 원금에 합산(복리) 또는 고정(단리) 선택
- 수수료 0.07% 반영, 세금 미반영
"""
import pandas as pd, numpy as np


def run(df, n_split=40, target=0.10, big=0.10, compound=True, quarter_cut=True,
        fee=0.0007, start_cap=10000.0):
    cash_total = start_cap  # 사이클 원금
    equity_curve = []
    cycles = []
    shares = 0.0; cost = 0.0; spent = 0.0
    unit = cash_total / n_split
    cash = cash_total
    cyc_start = None; realized = 0.0
    for dt, r in df.iterrows():
        o, h, l, c = r.Open, r.High, r.Low, r.Close
        # 1) 매도 체크 (보유 중이면 지정가)
        if shares > 0:
            avg = cost / shares
            tp = avg * (1 + target)
            if h >= tp:
                px = max(tp, o)  # 갭상승이면 시가 체결
                proceeds = shares * px * (1 - fee)
                cash += proceeds
                pnl = cash - cash_total
                cycles.append(dict(start=cyc_start, end=dt, days=(dt - cyc_start).days,
                                   ret=pnl / cash_total, pnl=pnl, used=spent / cash_total))
                realized += pnl
                shares = cost = spent = 0.0
                cash_total = cash if compound else start_cap
                if not compound:
                    cash = start_cap
                unit = cash_total / n_split
                equity_curve.append((dt, cash if compound else start_cap + realized))
                continue
        # 2) 매수
        if shares == 0:
            amt = min(unit, cash)
            if amt > 1e-9:
                q = amt * (1 - fee) / c
                shares += q; cost += amt; spent += amt; cash -= amt
                cyc_start = dt
        else:
            avg = cost / shares
            if cash >= unit * 0.5 - 1e-9:
                half = unit / 2
                for lim in (avg, avg * (1 + big)):
                    if c <= lim and cash >= half - 1e-9:
                        q = half * (1 - fee) / c
                        shares += q; cost += half; spent += half; cash -= half
            elif quarter_cut:
                # 원금 소진: 1/4 종가 매도 → 손실 확정, 현금 확보
                q = shares / 4
                cash += q * c * (1 - fee)
                cost *= 0.75  # 평단 유지
                shares -= q
        val = cash + shares * c
        base = val if compound else start_cap + realized + (val - cash_total)
        equity_curve.append((dt, base))
    eq = pd.Series(dict(equity_curve)).sort_index()
    return eq, pd.DataFrame(cycles)


def stats(eq, cycles, bench=None):
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    out = dict(최종배수=eq.iloc[-1] / eq.iloc[0], CAGR=cagr, MDD=dd, 사이클수=len(cycles))
    if len(cycles):
        out.update(평균사이클일수=cycles.days.mean(), 최장사이클일수=cycles.days.max(),
                   손실사이클=int((cycles.ret < 0).sum()))
    return out


if __name__ == "__main__":
    # 합성 데이터로 엔진 자체 점검
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2015-01-01", periods=2000)
    ret = rng.normal(0.0012, 0.04, len(idx))
    c = 100 * np.exp(np.cumsum(ret))
    df = pd.DataFrame(dict(Open=c * (1 + rng.normal(0, .005, len(c))), Close=c), index=idx)
    df["High"] = df[["Open", "Close"]].max(axis=1) * (1 + abs(rng.normal(0, .015, len(c))))
    df["Low"] = df[["Open", "Close"]].min(axis=1) * (1 - abs(rng.normal(0, .015, len(c))))
    eq, cy = run(df)
    print(stats(eq, cy))
    print(cy.tail())
