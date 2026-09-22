"""momentum_signals/pipeline.py — 하루치 전체 파이프라인(스펙 1~9번 순서 그대로).

스펙 1번("미국장 마감 이후 1회, 국내는 전일 종가·미국은 당일 종가 기준
통합")대로 국내/미국을 한 번에 같이 돌린다 — 통합 상위 5종목(4번)과 총
보유종목 6개 제한(9번)이 두 시장을 같이 봐야 하는 규칙이라 따로 실행하면
맞출 수 없다."""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

import config as cfg
import data
import portfolio as pf
import regime as regime_mod
import rs as rs_mod
from scoring import GateResult, ScoreBreakdown, hard_gate, score, select_top5_balanced
from signals import StockMetrics, evaluate

logger = logging.getLogger(__name__)


def _fetch_metrics(codes: list[tuple[str, str, str]], start: dt.date,
                    limit: int | None) -> dict[str, StockMetrics]:
    """codes: [(code, name, market), ...]"""
    if limit:
        codes = codes[:limit]
    out: dict[str, StockMetrics] = {}
    for code, name, market in codes:
        try:
            ohlcv = data.fetch_ohlcv(code, start)
        except Exception as exc:  # noqa: BLE001 — 개별 종목 조회 실패는 스킵하고 계속
            logger.warning("OHLCV 조회 실패 %s(%s): %s", code, name, exc)
            m = StockMetrics(code=code, name=name, market=market, status="확인불가",
                              reason=f"OHLCV 조회 실패: {exc}")
            out[code] = m
            continue
        out[code] = evaluate(code, name, market, ohlcv)
    return out


def _liquidity_pool(metrics: dict[str, StockMetrics], min_value: float,
                     top_n: int | None) -> list[str]:
    ok = [m for m in metrics.values() if m.status == "OK" and m.avg_trading_value20 is not None
          and m.avg_trading_value20 >= min_value]
    ok.sort(key=lambda m: m.avg_trading_value20, reverse=True)
    if top_n:
        ok = ok[:top_n]
    return [m.code for m in ok]


def _attach_rs(metrics: dict[str, StockMetrics], codes: list[str]) -> None:
    returns = {c: metrics[c].return_by_period for c in codes}
    scores = rs_mod.rs_scores_for_market(returns, cfg.RS_TREND_LOOKBACK, cfg.RS_TREND_WEIGHTS)
    for c, s in scores.items():
        metrics[c].rs_score = s


def run(positions: list[pf.Position] | None = None, limit: int | None = None,
        today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    positions = list(positions) if positions is not None else []
    start = data.history_start_date(today)

    logger.info("KR 후보 유니버스 조회")
    kr_listing = data.fetch_kr_candidate_universe()
    kr_codes = list(kr_listing[["Code", "Name", "Market"]].itertuples(index=False, name=None))
    kr_metrics = _fetch_metrics([(c, n, "KR") for c, n, _ in kr_codes], start, limit)

    logger.info("US 후보 유니버스 조회")
    us_listing = data.fetch_us_universe()
    us_codes = list(us_listing[["Code", "Name", "Market"]].itertuples(index=False, name=None))
    us_metrics = _fetch_metrics([(c, n, "US") for c, n, _ in us_codes], start, limit)

    kr_pool = _liquidity_pool(kr_metrics, cfg.MIN_AVG_TRADING_VALUE_KRW, cfg.KR_UNIVERSE_TOP_N)
    us_pool = _liquidity_pool(us_metrics, cfg.MIN_AVG_TRADING_VALUE_USD, None)

    all_metrics = {**kr_metrics, **us_metrics}
    _attach_rs(all_metrics, kr_pool)
    _attach_rs(all_metrics, us_pool)

    top5_codes = select_top5_balanced(
        {c: (all_metrics[c], all_metrics[c].rs_score) for c in kr_pool + us_pool},
        kr_pool, us_pool)

    top5: list[dict] = []
    for code in top5_codes:
        m = all_metrics[code]
        g = hard_gate(m)
        s = score(m)
        top5.append({"metrics": m, "gate": g, "score": s})

    passed = [row for row in top5 if row["gate"].passed and row["score"].total >= cfg.SCORE_MIN]
    passed.sort(key=lambda row: row["score"].total, reverse=True)
    finalists = passed[: cfg.FINALIST_N]
    entry_candidates = finalists[: cfg.ENTRY_N]

    logger.info("시장 레짐 평가")
    kr_index = data.fetch_index_ohlcv(cfg.KR_INDEX_CODE, start)
    us_index = data.fetch_index_ohlcv(cfg.US_INDEX_CODE, start)
    regime = {"KR": regime_mod.evaluate_regime(kr_index), "US": regime_mod.evaluate_regime(us_index)}

    _update_open_positions(positions, all_metrics, start)

    opened_today: list[pf.Position] = []
    today_str = today.isoformat()
    for row in entry_candidates:
        m: StockMetrics = row["metrics"]
        gate: GateResult = row["gate"]
        sc: ScoreBreakdown = row["score"]
        if regime[m.market].allow_new_entry is not True:
            continue  # 레짐 판정불가(None)도 보수적으로 신규진입 보류 취급
        if pf.open_position_count(positions) >= cfg.MAX_CONCURRENT_POSITIONS:
            continue
        already_open = any(p.status == "OPEN" and p.code == m.code and p.market == m.market
                            for p in positions)
        if already_open:
            continue
        total_equity = cfg.TOTAL_EQUITY_KRW if m.market == "KR" else cfg.TOTAL_EQUITY_USD
        pos_id = f"{m.market}-{m.code}-{today_str}"
        pos = pf.open_position(pos_id, m.market, m.code, m.name, today_str,
                                m.close, m.stop_price, sc.total, total_equity)
        if pos is not None:
            positions.append(pos)
            opened_today.append(pos)

    history_entry = _build_history_entry(today_str, top5, passed, opened_today)

    return {
        "date": today_str,
        "top5": top5,
        "finalists": finalists,
        "entries": entry_candidates,
        "opened_today": opened_today,
        "regime": regime,
        "positions": positions,
        "history_entry": history_entry,
    }


def _update_open_positions(positions: list[pf.Position], all_metrics: dict[str, StockMetrics],
                            start: dt.date) -> None:
    open_positions = [p for p in positions if p.status == "OPEN"]
    for pos in open_positions:
        m = all_metrics.get(pos.code)
        try:
            ohlcv = data.fetch_ohlcv(pos.code, start)
        except Exception as exc:  # noqa: BLE001
            logger.warning("보유 종목 시세 조회 실패 %s(%s): %s", pos.code, pos.name, exc)
            continue
        if ohlcv is None or ohlcv.empty:
            continue
        bar_date = ohlcv.index[-1]
        bar_date_str = bar_date.date().isoformat() if hasattr(bar_date, "date") else str(bar_date)
        row = ohlcv.iloc[-1]
        atr_value = m.atr_value if m and m.atr_value is not None else None
        if atr_value is None:
            from indicators import atr as atr_fn
            atr_series = atr_fn(ohlcv["High"].astype(float), ohlcv["Low"].astype(float),
                                 ohlcv["Close"].astype(float), cfg.ATR_PERIOD)
            atr_value = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else None
        pf.process_day(pos, bar_date_str, float(row["Open"]), float(row["High"]),
                        float(row["Low"]), float(row["Close"]), atr_value)


def _build_history_entry(date_str: str, top5: list[dict], passed: list[dict],
                          opened_today: list[pf.Position]) -> list[dict]:
    opened_by_code = {(p.market, p.code): p for p in opened_today}
    entries = []
    for row in top5:
        m: StockMetrics = row["metrics"]
        gate: GateResult = row["gate"]
        sc: ScoreBreakdown = row["score"]
        pos = opened_by_code.get((m.market, m.code))
        entries.append({
            "date": date_str, "market": m.market, "code": m.code, "name": m.name,
            "rsScore": m.rs_score, "scoreTotal": sc.total, "gatePass": gate.passed,
            "entered": pos is not None, "positionId": pos.id if pos else None,
            "sizingAmount": pos.invested_amount if pos else None,
            "stopPrice": m.stop_price,
        })
    return entries


def _metrics_to_dict(m: StockMetrics) -> dict:
    d = dict(m.__dict__)
    d.pop("return_by_period", None)
    return d


def _row_to_dict(row: dict) -> dict:
    m: StockMetrics = row["metrics"]
    g: GateResult = row["gate"]
    s: ScoreBreakdown = row["score"]
    out = _metrics_to_dict(m)
    out["gate"] = {"volumeOk": g.volume_ok, "riskOk": g.risk_ok, "passed": g.passed}
    out["scoreBreakdown"] = {"rs": s.rs, "volume": s.volume, "pivot": s.pivot, "clv": s.clv,
                              "rsi": s.rsi, "disparity": s.disparity,
                              "riskEfficiency": s.risk_efficiency, "total": s.total}
    return out


def _regime_to_dict(r: regime_mod.RegimeResult) -> dict:
    return {"indexClose": r.index_close, "indexSma": r.index_sma, "allowNewEntry": r.allow_new_entry}


def serialize_result(result: dict) -> dict:
    """run()의 반환값을 JSON 저장 가능한 dict로 바꾼다(대시보드/기록용)."""
    return {
        "date": result["date"],
        "top5": [_row_to_dict(r) for r in result["top5"]],
        "finalists": [_row_to_dict(r) for r in result["finalists"]],
        "entries": [_row_to_dict(r) for r in result["entries"]],
        "openedToday": [p.id for p in result["opened_today"]],
        "regime": {mk: _regime_to_dict(r) for mk, r in result["regime"].items()},
        "positions": [pf.position_to_dict(p) for p in result["positions"]],
        "historyEntry": result["history_entry"],
        "totalEquity": {"KR": cfg.TOTAL_EQUITY_KRW, "US": cfg.TOTAL_EQUITY_USD},
    }
