"""공통 시뮬레이터: 계좌 + 전략 루프 + 성과지표.

전략은 Strategy.on_bar(acct, bar) 하나만 구현한다. bar 는 당일 OHLC 와 date, i(인덱스), hist(전일까지 종가 배열).
체결 규칙은 전략이 주문 종류에 맞게 직접 판단한다:
  - LOC 매수/매도: 당일 종가 c 로 조건 판정·체결 (원래 주문 성격상 종가를 보고 판단해도 미래참조 아님)
  - 지정가 매도: 고가 h >= 지정가면 max(지정가, 시가) 체결
  - 그 외 지표(이평 등)는 hist(전일까지)만 쓴다
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


class Account:
    def __init__(self, cash, fee=0.0007):
        self.cash = float(cash)
        self.shares = 0.0
        self.cost = 0.0  # 보유분 매수금액(수수료 포함) — 평단 = cost / shares
        self.fee = fee
        self.trades = 0

    @property
    def avg(self):
        return self.cost / self.shares if self.shares > 1e-12 else np.nan

    def buy(self, amount, px):
        amount = min(amount, self.cash)
        if amount <= 1e-9:
            return 0.0
        q = amount * (1 - self.fee) / px
        self.shares += q; self.cost += amount; self.cash -= amount; self.trades += 1
        return q

    def sell(self, qty, px):
        qty = min(qty, self.shares)
        if qty <= 1e-12:
            return 0.0
        proceeds = qty * px * (1 - self.fee)
        self.cost *= 1 - qty / self.shares
        self.shares -= qty; self.cash += proceeds; self.trades += 1
        if self.shares < 1e-12:
            self.shares = self.cost = 0.0
        return proceeds

    def value(self, px):
        return self.cash + self.shares * px


class Strategy:
    name = "base"

    def on_bar(self, acct, bar):
        raise NotImplementedError

    def cycles(self):
        return pd.DataFrame(getattr(self, "_cycles", []))


@dataclass
class Bar:
    date: pd.Timestamp
    i: int
    o: float
    h: float
    l: float
    c: float
    hist: np.ndarray = field(repr=False)


def backtest(df, strategy, capital=10000.0, fee=0.0007, cash_rate=None):
    """cash_rate: 대기현금 연이자(소수 상수 또는 날짜 인덱스 Series). None 이면 이자 없음."""
    acct = Account(capital, fee)
    closes = df["Close"].to_numpy(float)
    O, H, L = (df[k].to_numpy(float) for k in ("Open", "High", "Low"))
    if isinstance(cash_rate, pd.Series):
        cr = cash_rate.reindex(df.index).ffill().fillna(0).to_numpy()
    else:
        cr = np.full(len(df), cash_rate or 0.0)
    eq = np.empty(len(df)); expo = np.empty(len(df))
    for i, dt in enumerate(df.index):
        if i and acct.cash > 0:
            acct.cash *= 1 + cr[i] * (dt - df.index[i - 1]).days / 365
        strategy.on_bar(acct, Bar(dt, i, O[i], H[i], L[i], closes[i], closes[:i]))
        eq[i] = acct.value(closes[i])
        expo[i] = acct.shares * closes[i] / eq[i] if eq[i] > 0 else 0
    return pd.DataFrame({"equity": eq, "exposure": expo}, index=df.index), acct


def stats(res, cycles=None):
    eq = res["equity"]
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    mult = eq.iloc[-1] / eq.iloc[0]
    cagr = mult ** (1 / yrs) - 1 if mult > 0 else -1
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    # 최장 수면 아래 기간(일): 전고점 회복까지 걸린 최대 일수
    peak_dates = eq.index.to_series().where(dd == 0).ffill()
    uw = (eq.index.to_series() - peak_dates).dt.days.max()
    yearly = eq.resample("YE").last().pct_change()
    yearly.iloc[0] = eq.resample("YE").last().iloc[0] / eq.iloc[0] - 1
    out = dict(배수=mult, CAGR=cagr, MDD=mdd, 칼마=cagr / -mdd if mdd < 0 else np.nan,
               최장수면일=int(uw), 최악연도=yearly.min(), 평균투입률=res["exposure"].mean())
    if cycles is not None and len(cycles):
        out.update(사이클=len(cycles), 손실사이클=int((cycles.ret < 0).sum()),
                   평균사이클일=cycles.days.mean(), 최장사이클일=int(cycles.days.max()))
    return out
