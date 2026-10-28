"""분할매수 전략 모음. 모두 sim.Strategy 인터페이스를 따른다."""
import numpy as np

from .sim import Strategy


# ---------------------------------------------------------------- A. 기준선
class LumpSum(Strategy):
    """첫날 종가에 전액 매수 후 보유."""
    name = "일시불보유"

    def on_bar(self, acct, bar):
        if bar.i == 0:
            acct.buy(acct.cash, bar.c)


class DCA(Strategy):
    """적립식: 초기 현금을 n_buys 회로 나눠 every 거래일마다 종가 매수, 다 쓰면 보유."""

    def __init__(self, every=21, n_buys=40):
        self.every, self.n_buys = every, n_buys
        self.name = f"적립식({every}일x{n_buys}회)"
        self.unit = None; self.done = 0

    def on_bar(self, acct, bar):
        if self.unit is None:
            self.unit = acct.cash / self.n_buys
        if self.done < self.n_buys and bar.i % self.every == 0:
            acct.buy(self.unit if self.done < self.n_buys - 1 else acct.cash, bar.c)
            self.done += 1


# ---------------------------------------------------------------- B. 라오어 계열
class LaoerV1(Strategy):
    """무한매수법 V1 근사 (engine.run 과 같은 규칙, 복리).

    - 사이클 원금을 n_split 회로 분할. 첫날 1회분 종가 매수
    - 이후 매일 절반은 평단 LOC, 절반은 평단x(1+big) LOC
    - 평단x(1+target) 지정가 전량 매도 → 사이클 종료, 그날은 매수 없음
    - 1회분 절반도 못 사면: quarter_cut 이면 보유 1/4 종가 매도
    """

    def __init__(self, n_split=40, target=0.10, big=0.10, quarter_cut=True):
        self.n, self.target, self.big, self.qc = n_split, target, big, quarter_cut
        self.name = f"라오어V1(n{n_split},tp{target:.0%},big{big:.0%}{',QC' if quarter_cut else ''})"
        self.cap = None; self.start = None; self.spent = 0.0
        self._cycles = []

    def on_bar(self, acct, bar):
        if self.cap is None:
            self.cap = acct.cash
        if acct.shares > 0:
            tp = acct.avg * (1 + self.target)
            if bar.h >= tp:
                acct.sell(acct.shares, max(tp, bar.o))
                pnl = acct.cash - self.cap
                self._cycles.append(dict(start=self.start, end=bar.date, days=(bar.date - self.start).days,
                                         ret=pnl / self.cap, used=self.spent / self.cap))
                self.cap = acct.cash; self.spent = 0.0
                return
        unit = self.cap / self.n
        if acct.shares == 0:
            self.spent += unit
            acct.buy(unit, bar.c)
            self.start = bar.date
            return
        avg, half = acct.avg, unit / 2
        if acct.cash >= half - 1e-9:
            for lim in (avg, avg * (1 + self.big)):
                if bar.c <= lim and acct.cash >= half - 1e-9:
                    acct.buy(half, bar.c); self.spent += half
        elif self.qc:
            acct.sell(acct.shares / 4, bar.c)
