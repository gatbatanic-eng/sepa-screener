"""momentum_signals/portfolio.py — 포지션 사이징 + 일일 자동 페이퍼 포지션 관리.

이 서브시스템은 다른 4개(sepa/screener/range_vrebound/technical_signals)와
달리 상태가 없는 일일 재계산 스크리너가 아니다 — "신규진입 대상"은 익일
시가에 실제로 매수했다고 자동 가정하고(사용자 확인 완료 사항), 이후
날짜를 넘어 포지션을 추적한다. 실제 주문은 절대 실행하지 않는다 —
전부 페이퍼(가상) 트레이딩 시뮬레이션이며 산출물은 참고용이다.

하루 처리 순서(포지션당, 그날의 OHLC만 사용해 causal하게):
1. 갭 손절: 시가 <= 현재 손절가 → 시가로 전량 청산(그 외 로직 스킵).
2. 저가 <= 현재 손절가 → 손절가로 전량 청산.
3. (청산 안 됐으면) +1R 도달(당일 고가 기준) → 손절가를 진입가(본전)로 상향.
4. +2R 도달(당일 고가 기준) → 잔여수량의 1/3을 +2R 가격에 익절, 이후
   트레일링 스탑 시작.
5. 트레일링 활성 상태면 손절가 = max(기존 손절가, 종가 - ATR*1.5).
6. 보유 거래일수(시장별 개별 카운트)가 정확히 5일째이고 진입 대비 수익률이
   +3% 미만이면 잔여수량 종가 청산(시간손절). 손절가와 동시충족 시 손절가가
   이미 위 1~2단계에서 우선 처리된다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import config as cfg


@dataclass
class PartialExit:
    date: str
    shares: int
    price: float
    reason: str


@dataclass
class Position:
    id: str
    market: str
    code: str
    name: str
    entry_date: str
    entry_price: float
    initial_stop: float
    entry_score: float
    status: str = "OPEN"          # OPEN | CLOSED
    current_stop: float = 0.0
    r_per_share: float = 0.0
    shares_original: int = 0
    shares_remaining: int = 0
    invested_amount: float = 0.0
    account_risk_pct: float = 0.0
    r1_hit: bool = False
    r2_hit: bool = False
    trailing_active: bool = False
    trading_days_held: int = 0
    time_stop_checked: bool = False
    exits: list[PartialExit] = field(default_factory=list)
    exit_date: str | None = None
    last_close: float | None = None
    last_processed_date: str | None = None

    def __post_init__(self):
        if self.r_per_share <= 0:
            self.r_per_share = self.entry_price - self.initial_stop
        if self.current_stop == 0.0:
            self.current_stop = self.initial_stop

    @property
    def realized_shares(self) -> int:
        return sum(e.shares for e in self.exits)

    @property
    def realized_pnl(self) -> float:
        return sum((e.price - self.entry_price) * e.shares for e in self.exits)

    @property
    def r_multiple(self) -> float | None:
        """CLOSED면 실현 R배수, OPEN이면 마지막 종가 기준 미실현 R배수(전량 기준)."""
        if self.r_per_share <= 0:
            return None
        if self.status == "CLOSED":
            total_shares = self.realized_shares
            if total_shares <= 0:
                return None
            return round(self.realized_pnl / total_shares / self.r_per_share, 3)
        if self.last_close is None:
            return None
        return round((self.last_close - self.entry_price) / self.r_per_share, 3)


def size_position(entry_price: float, stop_price: float, score_total: float,
                   market: str, total_equity: float) -> tuple[int, float, float]:
    """(수량, 실투입액, 계좌리스크%) — 상한액 tier와 리스크캡(총자산 1%) 중
    더 적은 수량을 채택한다."""
    risk_per_share = entry_price - stop_price
    if entry_price <= 0 or risk_per_share <= 0:
        return 0, 0.0, 0.0

    tier_cap_krw = 0.0
    for lo, hi, cap in cfg.SIZE_TIERS_KRW:
        if lo <= score_total < hi:
            tier_cap_krw = cap
            break
    else:
        if score_total >= cfg.SIZE_TIERS_KRW[-1][1]:
            tier_cap_krw = cfg.SIZE_TIERS_KRW[-1][2]
    tier_cap = tier_cap_krw if market == "KR" else tier_cap_krw / cfg.USD_KRW_RATE

    risk_budget = total_equity * cfg.ACCOUNT_RISK_PCT / 100.0
    shares_from_risk = math.floor(risk_budget / risk_per_share) if risk_per_share > 0 else 0
    shares_from_tier = math.floor(tier_cap / entry_price)
    shares = max(0, min(shares_from_risk, shares_from_tier))

    invested = shares * entry_price
    account_risk_pct = (shares * risk_per_share / total_equity * 100.0) if total_equity > 0 else 0.0
    return shares, round(invested, 2), round(account_risk_pct, 4)


def open_position(pos_id: str, market: str, code: str, name: str, entry_date: str,
                   entry_price: float, stop_price: float, score_total: float,
                   total_equity: float) -> Position | None:
    shares, invested, risk_pct = size_position(entry_price, stop_price, score_total,
                                                market, total_equity)
    if shares <= 0:
        return None
    pos = Position(id=pos_id, market=market, code=code, name=name, entry_date=entry_date,
                    entry_price=entry_price, initial_stop=stop_price, entry_score=score_total,
                    shares_original=shares, shares_remaining=shares,
                    invested_amount=invested, account_risk_pct=risk_pct)
    return pos


def process_day(pos: Position, date: str, open_: float, high: float, low: float,
                 close: float, atr_value: float | None) -> None:
    """pos를 제자리에서 갱신한다. status가 이미 CLOSED면 아무 것도 하지 않는다.
    같은 날짜를 두 번 넘기거나(재실행) 그 시장이 휴장이라 새 봉이 없는 날은
    (date == 직전 처리일) 거래일 카운트를 올리지 않고 건너뛴다 — "시장별
    개별 카운트"가 실제 그 시장의 거래일과 어긋나지 않도록 한다."""
    if pos.status != "OPEN":
        return
    if date == pos.last_processed_date:
        return
    pos.last_processed_date = date

    pos.trading_days_held += 1
    pos.last_close = close

    if open_ <= pos.current_stop:
        _exit_all(pos, date, open_, "갭 손절(시가가 손절가 이하)")
        return
    if low <= pos.current_stop:
        _exit_all(pos, date, pos.current_stop, "손절")
        return

    if not pos.r1_hit and high >= pos.entry_price + cfg.R1_MULTIPLE * pos.r_per_share:
        pos.r1_hit = True
        pos.current_stop = max(pos.current_stop, pos.entry_price)

    if not pos.r2_hit and high >= pos.entry_price + cfg.R2_MULTIPLE * pos.r_per_share:
        pos.r2_hit = True
        target_price = pos.entry_price + cfg.R2_MULTIPLE * pos.r_per_share
        sell_shares = math.floor(pos.shares_original * cfg.R2_PARTIAL_SELL_FRACTION)
        sell_shares = min(sell_shares, pos.shares_remaining)
        if sell_shares > 0:
            pos.exits.append(PartialExit(date=date, shares=sell_shares, price=target_price,
                                          reason="+2R 부분익절(1/3)"))
            pos.shares_remaining -= sell_shares
        pos.trailing_active = True

    if pos.trailing_active and atr_value:
        pos.current_stop = max(pos.current_stop, close - cfg.TRAILING_ATR_MULT * atr_value)

    if (not pos.time_stop_checked and pos.trading_days_held >= cfg.TIME_STOP_TRADING_DAYS
            and pos.shares_remaining > 0):
        pos.time_stop_checked = True
        ret_pct = (close - pos.entry_price) / pos.entry_price * 100.0
        if ret_pct < cfg.TIME_STOP_MIN_RETURN_PCT:
            _exit_all(pos, date, close, "시간손절(5거래일 내 +3% 미달)")


def _exit_all(pos: Position, date: str, price: float, reason: str) -> None:
    if pos.shares_remaining > 0:
        pos.exits.append(PartialExit(date=date, shares=pos.shares_remaining, price=price,
                                      reason=reason))
        pos.shares_remaining = 0
    pos.status = "CLOSED"
    pos.exit_date = date


def position_to_dict(pos: Position) -> dict:
    d = {k: v for k, v in pos.__dict__.items() if k != "exits"}
    d["exits"] = [e.__dict__ for e in pos.exits]
    return d


def position_from_dict(d: dict) -> Position:
    d = dict(d)
    exits = [PartialExit(**e) for e in d.pop("exits", [])]
    pos = Position(**d)
    pos.exits = exits
    return pos


def open_position_count(positions: list[Position]) -> int:
    return sum(1 for p in positions if p.status == "OPEN")

