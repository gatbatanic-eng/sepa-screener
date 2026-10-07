"""단일 종목 일봉 시뮬레이터. 체결·비용·갭·동시발생 처리는 여기(와 fills.py, costs.py)에서만 한다.

봉 i 하나를 처리하는 순서
  1. 진입: 직전 봉(i-1)에서 낸 진입 의도를 체결한다(M1 시가 / M2 돌파가). 체결되면 전략에서 초기 손절(StopSpec)을 받는다.
     진입 당일 intraday 손절은 저가 ≤ 손절가면 손절가 체결로 고정한다(fills.entry_bar_intraday_stop_fill).
  2. 기존 포지션: (a) 시가 체결 청산 → (b) intraday 손절(시가 갭이면 시가, 아니면 손절가) → (c) 종가 청산.
     일봉으로 순서를 알 수 없는 경우는 손절을 먼저 처리한다(보수적).
  3. 포지션이 남아 있으면 MFE/MAE를 갱신하고, 마감 후 전략 manage()를 불러 손절선 갱신·규칙 청산 의도를 받는다.
     close 종류 손절은 이 봉의 종가로 판정해 다음 봉 시가 청산을 예약한다(진입 당일 종가부터 판정).
  4. 마감 시 포지션이 없으면 전략 entry_intent()를 불러 다음 봉 진입 의도를 받는다. 보유 중에는 부르지 않는다(종목당 1포지션).
     데이터 시작 후 warmup_bars(기본 252)봉 안의 신호는 부르지 않는다(워밍업 게이트, 모든 전략 공통).
  데이터 끝에서 남은 포지션은 마지막 종가로 평가해 exit_reason="open_mtm"으로 기록한다(청산 비용 포함).

R 정의: 1R = 진입가 − 초기 손절가(비용 제외 체결가). r = weight × (청산가 − 진입가 − 진입비용 − 청산비용) / 1R.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from golden_backtest import config
from golden_backtest.engine import fills
from golden_backtest.engine.costs import Costs
from golden_backtest.engine.intents import EntryIntent, ExitIntent, StopSpec
from golden_backtest.engine.position import Position, TrancheState
from golden_backtest.records.trade import Trade

log = logging.getLogger("golden_backtest.engine")

REQUIRED_COLUMNS = ("open", "high", "low", "close")


@dataclass
class SimResult:
    ticker: str
    trades: list[Trade] = field(default_factory=list)            # 확정 거래 + open_mtm 행
    rejected_entries: list[dict] = field(default_factory=list)   # R ≤ 0 등으로 거부된 진입

    def confirmed(self) -> list[Trade]:
        """확정 거래만(미청산 제외)."""
        return [t for t in self.trades if t.exit_reason != "open_mtm"]

    def with_open_mtm(self) -> list[Trade]:
        """확정 거래 + 미청산을 마지막 종가로 평가한 행."""
        return list(self.trades)


@dataclass
class _Pending:
    tranche: str
    reason: str
    at: str  # "next_open" | "next_close"


def simulate(df: pd.DataFrame, strategy, ticker: str, costs: Costs, spec_version: str, cost_group: str = "default",
             warmup_bars: int | None = None) -> SimResult:
    """df: 완전 수정 OHLC(소문자 열 open/high/low/close) + 전략이 쓰는 열, DatetimeIndex 오름차순.

    warmup_bars: 데이터(df) 시작 후 이 봉 수 안(봉 번호 < warmup_bars)의 신호는 쓰지 않는다 [임의, 모든 전략 공통].
    None이면 config/engine.yaml 값(252)을 쓴다. 이미 진입한 포지션의 관리에는 영향이 없고, 신규 진입 신호만 막는다.
    """
    if warmup_bars is None:
        warmup_bars = int(config.load("engine")["warmup_bars"])
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"df에 {col} 열이 없다")
    prep = strategy.prepare(df)
    if not prep.index.equals(df.index):
        raise ValueError("strategy.prepare는 입력과 같은 인덱스를 돌려줘야 한다")
    n = len(prep)
    o, h, l, c = (prep[k].to_numpy(dtype=float) for k in REQUIRED_COLUMNS)
    dates = prep.index
    rate = costs.one_way_rate(cost_group)

    result = SimResult(ticker=ticker)
    pos: Position | None = None
    pending_entry: tuple[EntryIntent, int] | None = None
    pending_exits: list[_Pending] = []
    trade_seq = 0

    def record(p: Position, tr: TrancheState, i: int, price: float, reason: str) -> None:
        entry_cost = p.entry_price * rate
        exit_cost = price * rate
        w = tr.weight
        result.trades.append(Trade(
            ticker=ticker, strategy=strategy.code, version=spec_version,
            signal_date=p.signal_date, entry_date=p.entry_date, entry_price=p.entry_price, initial_stop=p.initial_stop,
            exit_date=dates[i], exit_price=price, exit_reason=reason, tranche=tr.name, hold_days=i - p.entry_index,
            costs=w * (entry_cost + exit_cost), r_multiple=w * ((price - p.entry_price) - entry_cost - exit_cost) / p.risk,
            mfe_r=p.mfe_r, mae_r=p.mae_r, regime_tag=None, event_flag=None,
            strategy_version=strategy.version, entry_model=strategy.entry_model, risk_basis=p.risk_basis,
            stop_kind=p.stop_kind, trade_id=p.trade_id, weight=w))
        tr.open = False

    for i in range(n):
        entered_now = False

        # 1. 진입 -----------------------------------------------------------------------------------------
        if pending_entry is not None:
            intent, sig_i = pending_entry
            pending_entry = None
            fill = fills.entry_next_open(o[i]) if intent.kind == "next_open" else fills.entry_buy_stop(intent.price, o[i], h[i])
            if fill is not None and pos is None:
                spec: StopSpec = strategy.initial_stop(prep.iloc[sig_i], fill)
                risk = (fill - spec.price) if spec.price is not None else spec.nominal_risk
                if risk <= 0:
                    result.rejected_entries.append({"date": dates[i], "signal_date": dates[sig_i], "fill": fill,
                                                    "stop": spec.price, "reason": "손절가 >= 체결가 (R <= 0)"})
                    log.warning("[%s] %s 진입 거부: 손절가 %.4f >= 체결가 %.4f", ticker, dates[i].date(), spec.price, fill)
                else:
                    trade_seq += 1
                    pos = Position(ticker=ticker, strategy=strategy.code, signal_date=dates[sig_i], entry_index=i,
                                   entry_date=dates[i], entry_price=fill, initial_stop=spec.price, stop_kind=spec.kind,
                                   risk=risk, risk_basis=spec.risk_basis, trade_id=trade_seq,
                                   tranches={k: TrancheState(k, w) for k, w in intent.tranches.items()})
                    if spec.price is not None:
                        if spec.kind == "intraday":
                            pos.raise_intraday_stop(spec.price, spec.reason)
                        else:
                            pos.raise_close_stop(spec.price, spec.reason)
                    entered_now = True
                    pos.update_excursions(h[i], l[i])
                    if spec.kind == "intraday":
                        hit = fills.entry_bar_intraday_stop_fill(spec.price, l[i])
                        if hit is not None:
                            for tr in pos.open_tranches():
                                record(pos, tr, i, hit, spec.reason)

        # 2. 기존 포지션 ----------------------------------------------------------------------------------
        if pos is not None and not entered_now:
            pos.update_excursions(h[i], l[i])
            # (a) 직전 종가에서 정해진 시가 체결 청산
            for pe in [p for p in pending_exits if p.at == "next_open"]:
                tr = pos.tranches[pe.tranche]
                if tr.open:
                    record(pos, tr, i, o[i], pe.reason)
            # (b) intraday 손절: 시가 갭이면 시가, 아니면 손절가
            for tr in pos.open_tranches():
                if tr.stop_intraday is not None:
                    px = fills.intraday_stop_fill(tr.stop_intraday, o[i], l[i])
                    if px is not None:
                        record(pos, tr, i, px, tr.stop_intraday_reason)
            # (c) 날짜가 정해진 종가 청산 (손절이 먼저 처리된 뒤 남은 트랜치만)
            for pe in [p for p in pending_exits if p.at == "next_close"]:
                tr = pos.tranches[pe.tranche]
                if tr.open:
                    record(pos, tr, i, c[i], pe.reason)
            pending_exits = []

        # 3. 마감 후 관리 ---------------------------------------------------------------------------------
        if pos is not None and pos.open_tranches():
            pos.bars_since_entry = i - pos.entry_index
            pending_exits = []
            if i < n - 1:
                for ex in strategy.manage(pos, prep.iloc[i]) or []:
                    targets = [t for t in pos.open_tranches() if ex.tranche in (None, t.name)]
                    if ex.price is not None:
                        if ex.kind == "intraday":
                            pos.raise_intraday_stop(ex.price, ex.reason, ex.tranche)
                        else:
                            pos.raise_close_stop(ex.price, ex.reason, ex.tranche)
                    else:
                        for t in targets:
                            pending_exits.append(_Pending(t.name, ex.reason, ex.at))
                # close 종류 손절: 이 봉 종가로 판정 → 다음 봉 시가
                scheduled = {p.tranche for p in pending_exits}
                for t in pos.open_tranches():
                    if t.stop_close is not None and t.name not in scheduled and fills.close_stop_triggered(t.stop_close, c[i]):
                        pending_exits.append(_Pending(t.name, t.stop_close_reason, "next_open"))
        if pos is not None and not pos.open_tranches():
            pos, pending_exits = None, []

        # 4. 다음 봉 진입 의도 ----------------------------------------------------------------------------
        if pos is None and i < n - 1 and i >= warmup_bars:   # 워밍업 게이트: 전략 코드는 이 규칙을 모른다
            intent = strategy.entry_intent(prep.iloc[i])
            if intent is not None:
                pending_entry = (intent, i)

    # 데이터 끝 미청산: 마지막 종가로 평가 --------------------------------------------------------------------
    if pos is not None:
        for tr in pos.open_tranches():
            record(pos, tr, n - 1, c[n - 1], "open_mtm")
    return result
