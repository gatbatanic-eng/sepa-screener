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
        self.scale = 1.0  # 1회분 배율 (하위 클래스용)
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
        unit = self.cap / self.n * self.scale
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


class LaoerX(LaoerV1):
    """V1 확장판. 기본값이면 LaoerV1 과 같다.

    cut: 'quarter'(V1 쿼터손절) / 'none'(소진 시 익절까지 보유) / 'all'(소진 시 전량 종가 손절, 사이클 종료)
    trend: >0 이면 전일 종가가 그 기간 이평선 아래일 때 새 사이클을 시작하지 않는다 (진행 중인 사이클은 유지)
    """

    def __init__(self, n_split=40, target=0.10, big=0.10, cut="quarter", trend=0, vol_target=0.0, vol_lb=20):
        super().__init__(n_split, target, big, cut == "quarter")
        self.cut, self.trend, self.vt, self.vlb = cut, trend, vol_target, vol_lb
        self.name = (f"라오어(n{n_split},tp{target:.1%},big{big:.0%},{cut}{f',MA{trend}' if trend else ''}"
                     f"{f',vol{vol_target:.0%}' if vol_target else ''})")

    def on_bar(self, acct, bar):
        if self.cap is None:
            self.cap = acct.cash
        if acct.shares == 0 and self.trend and not _above_ma(bar.hist, self.trend):
            self.cap = acct.cash  # 대기 중 붙은 이자를 다음 사이클 원금에 반영
            return
        if self.cut == "all" and acct.shares > 0 and acct.cash < self.cap / self.n / 2 - 1e-9 \
                and bar.h < acct.avg * (1 + self.target):
            acct.sell(acct.shares, bar.c)
            pnl = acct.cash - self.cap
            self._cycles.append(dict(start=self.start, end=bar.date, days=(bar.date - self.start).days,
                                     ret=pnl / self.cap, used=self.spent / self.cap))
            self.cap = acct.cash; self.spent = 0.0
            return
        if self.vt and len(bar.hist) > self.vlb:
            self.scale = _vol_weight(bar.hist, self.vt, self.vlb)  # 변동성이 높으면 1회분 매수액 축소
        super().on_bar(acct, bar)


def _above_ma(hist, n):
    return len(hist) < n or hist[-1] >= hist[-n:].mean()


# ---------------------------------------------------------------- C. 가치평균법
class ValueAvg(Strategy):
    """every 거래일마다 주식 평가액 목표 = unit x k x (1+g)^k (k=회차). 부족분 매수, 초과분은 allow_sell 이면 매도.
    n 회 이후에는 목표를 계속 (1+g) 씩 올리며 현금이 남아 있는 한 같은 규칙 유지."""

    def __init__(self, every=21, n=40, g=0.01, allow_sell=True):
        self.every, self.n, self.g, self.sell = every, n, g, allow_sell
        self.name = f"가치평균({every}일,n{n},g{g:.1%}{',매도' if allow_sell else ''})"
        self.unit = None; self.k = 0

    def on_bar(self, acct, bar):
        if self.unit is None:
            self.unit = acct.cash / self.n
        if bar.i % self.every:
            return
        self.k += 1
        target = self.unit * min(self.k, self.n) * (1 + self.g) ** self.k
        gap = target - acct.shares * bar.c
        if gap > 0:
            acct.buy(gap, bar.c)
        elif self.sell and gap < 0:
            acct.sell(-gap / bar.c, bar.c)


# ---------------------------------------------------------------- D. 하락 가중 분할
class DipWeighted(Strategy):
    """every 거래일마다 기본 1회분(초기현금/n) x 배수 매수. 배수는 전일 종가의 252일 고점 대비 낙폭 구간으로 정한다.
    tiers: ((낙폭하한, 배수), ...) 예) ((0, 1), (0.2, 2), (0.4, 3), (0.6, 4))"""

    def __init__(self, every=5, n=80, tiers=((0, 1), (0.2, 2), (0.4, 3), (0.6, 4)), lookback=252):
        self.every, self.n, self.tiers, self.lb = every, n, tiers, lookback
        self.name = f"하락가중({every}일,n{n},{'/'.join(str(m) for _, m in tiers)})"
        self.unit = None

    def on_bar(self, acct, bar):
        if self.unit is None:
            self.unit = acct.cash / self.n
        if bar.i % self.every or acct.cash <= 1e-9:
            return
        h = bar.hist[-self.lb:]
        dd = 1 - h[-1] / h.max() if len(h) else 0
        mult = max(m for lo, m in self.tiers if dd >= lo)
        acct.buy(self.unit * mult, bar.c)


# ---------------------------------------------------------------- E. 추세 필터
class TrendHold(Strategy):
    """전일 종가가 ma 이평선 x (1+band) 위면 전액 보유, x (1-band) 아래면 전액 현금. 체결은 당일 시가."""

    def __init__(self, ma=200, band=0.0):
        self.ma, self.band = ma, band
        self.name = f"추세보유(MA{ma}{f',±{band:.0%}' if band else ''})"

    def on_bar(self, acct, bar):
        if len(bar.hist) < self.ma:
            return
        m = bar.hist[-self.ma:].mean(); p = bar.hist[-1]
        if acct.shares == 0 and p > m * (1 + self.band):
            acct.buy(acct.cash, bar.o)
        elif acct.shares > 0 and p < m * (1 - self.band):
            acct.sell(acct.shares, bar.o)


# ---------------------------------------------------------------- F. 변동성 조절
class VolTarget(Strategy):
    """목표 비중 = min(max_w, target_vol / 최근 lb일 실현변동성). 비중 차이가 band 넘으면 리밸런싱.
    trend>0 이면 전일 종가가 그 이평선 아래일 때 목표 비중 0. at='close'(당일 종가) / 'open'(당일 시가) 체결.
    판단에는 항상 전일까지 종가만 쓴다."""

    def __init__(self, target_vol=0.4, lb=20, band=0.1, max_w=1.0, trend=0, at="close"):
        self.tv, self.lb, self.band, self.max_w, self.trend, self.at = target_vol, lb, band, max_w, trend, at
        self.name = (f"변동성조절(목표{target_vol:.0%},{lb}일,±{band:.0%}"
                     f"{f',MA{trend}' if trend else ''}{',시가' if at == 'open' else ''})")

    def on_bar(self, acct, bar):
        if len(bar.hist) <= max(self.lb, self.trend):
            return
        w = _vol_weight(bar.hist, self.tv, self.lb, self.max_w)
        if self.trend and not _above_ma(bar.hist, self.trend):
            w = 0.0
        _rebalance(acct, bar.o if self.at == "open" else bar.c, w, self.band)


def _vol_weight(hist, tv, lb, max_w=1.0):
    r = np.diff(np.log(hist[-lb - 1:]))
    vol = r.std() * np.sqrt(252)
    return min(max_w, tv / vol) if vol > 0 else max_w


# ---------------------------------------------------------------- G. 고정비중 리밸런싱
class Rebalance(Strategy):
    """TQQQ 비중 w 유지. 비중이 w±band 를 벗어나면 종가로 되돌린다 (현금은 cash_rate 이자)."""

    def __init__(self, w=0.5, band=0.1, at="close"):
        self.w, self.band, self.at = w, band, at
        self.name = f"리밸런싱(w{w:.0%},±{band:.0%}{',시가' if at == 'open' else ''})"

    def on_bar(self, acct, bar):
        _rebalance(acct, bar.o if self.at == "open" else bar.c, self.w, self.band if bar.i else 0)


def _rebalance(acct, px, w, band):
    val = acct.value(px)
    cur = acct.shares * px / val if val > 0 else 0
    if abs(cur - w) <= band and cur > 0:
        return
    diff = w * val - acct.shares * px
    if diff > 0:
        acct.buy(diff, px)
    elif diff < 0:
        acct.sell(-diff / px, px)


class VolTargetRamp(VolTarget):
    """VolTarget 에 분할 진입을 붙인 것 (signal.py 의 ramp 규칙과 동일).
    변동성 계산이 가능한 첫날 start_w 비중으로 시작하고, weeks 주 동안 목표 비중을 시작값에서 매주 1/weeks 씩만 올린다.
    이 기간에는 min_gap 이상 모자라면 매수, 위험 축소(매도)는 평소 밴드. weeks=0 이면 첫날부터 목표 비중."""

    def __init__(self, target_vol=0.35, lb=20, band=0.1, weeks=5, start_w=0.27, min_gap=0.02):
        super().__init__(target_vol, lb, band)
        self.weeks, self.start_w, self.min_gap = weeks, start_w, min_gap
        self.name = f"변동성조절{target_vol:.0%}(시작{start_w:.0%},{f'{weeks}주 분할' if weeks else '즉시'})"
        self.t0 = None

    def on_bar(self, acct, bar):
        if len(bar.hist) <= self.lb:  # 변동성 계산이 가능해지는 날을 시작일로 본다
            return
        if self.t0 is None:
            acct.buy(acct.cash * self.start_w, bar.c)
            self.t0 = bar.date
            return
        w = _vol_weight(bar.hist, self.tv, self.lb, self.max_w)
        k = (bar.date - self.t0).days // 7 + 1
        if not self.weeks or k > self.weeks or w <= self.start_w:
            _rebalance(acct, bar.c, w, self.band)
            return
        eff = self.start_w + (w - self.start_w) * k / self.weeks
        val = acct.value(bar.c)
        gap = eff - acct.shares * bar.c / val
        if gap > self.min_gap or gap < -self.band:
            _rebalance(acct, bar.c, eff, 0.0)
