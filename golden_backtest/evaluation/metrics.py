"""거래 단위 R-multiple 지표. CLAUDE.md 9절: PF 단독으로 보고하지 않고 거래 수, 승률, 평균 R, 비용 후 PF, 최대 연속 손실을 함께 낸다.

정의
- r_multiple은 이미 비용 반영 후 값이다. 트랜치 행은 (ticker, trade_id)로 합산해 거래 하나로 센다.
- **거래 순서는 청산일 순이다**(같은 날이면 ticker, trade_id 순). 최대 연속 손실은 이 순서로 계산한다.
  종목별로 이어 붙이지 않는다 — 여러 종목을 합산할 때 진입순·종목순으로 세면 실제 시간 순서의 연속 손실과 달라진다.
  미청산(open_mtm) 거래의 청산일은 마지막 봉 날짜다.
- 승률 = r > 0, 패 = r < 0, r = 0은 둘 다 아니고 연속 손실을 끊는다. PF = 이익 합 / 손실 합(손실이 없으면 정의 불가).
- 평균 이익 R / 평균 손실 R / 중앙값 R을 함께 낸다. '기대값'(= 승률 × 평균 이익 + 패율 × 평균 손실)은 평균 R과 같은 값이라 표에서 뺀다.
- 거래 수 30 미만은 '판정 불가'로 표시한다.
"""
from __future__ import annotations

import statistics

from golden_backtest.records.trade import Trade

MIN_TRADES = 30


def trade_r_list(trades: list[Trade], include_open: bool) -> list[float]:
    """거래 단위 r 목록(청산일 순). include_open=False면 미청산(open_mtm)이 하나라도 있는 거래를 뺀다."""
    groups: dict[tuple[str, int], dict] = {}
    for t in trades:
        g = groups.setdefault((t.ticker, t.trade_id), {"r": 0.0, "exit": t.exit_date, "open": False})
        g["r"] += t.r_multiple
        g["exit"] = max(g["exit"], t.exit_date)
        g["open"] = g["open"] or t.exit_reason == "open_mtm"
    ordered = sorted(groups.items(), key=lambda kv: (kv[1]["exit"], kv[0][0], kv[0][1]))
    return [g["r"] for _, g in ordered if include_open or not g["open"]]


def r_stats(rs: list[float]) -> dict:
    """rs는 청산일 순으로 정렬된 거래 r 목록이어야 한다(최대 연속 손실이 순서에 의존)."""
    n = len(rs)
    if n == 0:
        return {"n": 0, "insufficient": True}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    streak = best = 0
    for r in rs:
        streak = streak + 1 if r < 0 else 0
        best = max(best, streak)
    gross_loss = -sum(losses)
    return {
        "n": n, "wins": len(wins), "losses": len(losses), "win_rate": win_rate,
        "avg_r": sum(rs) / n,
        "median_r": statistics.median(rs),
        "avg_win_r": avg_win, "avg_loss_r": avg_loss,
        "expectancy": win_rate * avg_win + (1 - win_rate) * avg_loss,   # 평균 R과 같은 값(API용). 보고 표에는 싣지 않는다
        "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0 else None,  # 손실이 없으면 정의 불가(None)
        "max_consecutive_losses": best, "total_r": sum(rs),
        "insufficient": n < MIN_TRADES,                                  # 30거래 미만 → 판정 불가
    }


def trade_stats(trades: list[Trade], include_open: bool) -> dict:
    return r_stats(trade_r_list(trades, include_open))
