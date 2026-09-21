"""Independent AI tenbagger research screener.

This module intentionally does not modify the SEPA, technical, growth/value, or
AI value-chain pipelines.  It publishes a separate public JSON document and a
durable research state.  Missing data stays ``None``; it is never coerced to 0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parent
UNIVERSE_PATH = ROOT / "data" / "tenbagger_universe.csv"
STATE_PATH = ROOT / "research" / "tenbagger_us.json"
PUBLIC_PATH = ROOT / "docs" / "research" / "tenbagger_us.json"


def finite(value: Any) -> float | None:
    """Return a finite float or None without confusing missing data with zero."""
    try:
        if value is None or pd.isna(value):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def percent(value: Any) -> float | None:
    """Yahoo growth fields are ratios; convert them to percentage points."""
    out = finite(value)
    return None if out is None else round(out * 100, 4)


def growth(current: Any, previous: Any) -> float | None:
    current, previous = finite(current), finite(previous)
    if current is None or previous in (None, 0):
        return None
    return round((current / previous - 1) * 100, 4)


def frame_cell(frame: Any, row: str, column: str) -> float | None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    try:
        return finite(frame.loc[column, row])
    except (KeyError, TypeError):
        return None


def estimate_row(frame: Any, period: str) -> dict[str, float | None]:
    keys = {
        "avg": "avg",
        "low": "low",
        "high": "high",
        "yearAgo": "yearAgoEps",
        "analysts": "numberOfAnalysts",
        "growth": "growth",
    }
    return {name: frame_cell(frame, source, period) for name, source in keys.items()}


def provider_value(stock: Any, attribute: str, default: Any) -> Any:
    try:
        value = getattr(stock, attribute)
        return default if value is None else value
    except Exception:
        return default


def statement_value(statement: Any, labels: tuple[str, ...], column_index: int) -> float | None:
    if not isinstance(statement, pd.DataFrame) or statement.empty or len(statement.columns) <= column_index:
        return None
    for label in labels:
        if label in statement.index:
            return finite(statement.loc[label, statement.columns[column_index]])
    return None


def closest_history(history: list[dict], today: dt.date, key: str, days: int) -> float | None:
    """Pick the latest observation at least ``days`` old, with a 21-day tolerance."""
    target = today - dt.timedelta(days=days)
    candidates = []
    for item in history:
        try:
            observed = dt.date.fromisoformat(item["date"])
        except (KeyError, ValueError):
            continue
        value = finite(item.get(key))
        distance = abs((observed - target).days)
        if value is not None and distance <= 21:
            candidates.append((distance, observed, value))
    return sorted(candidates)[0][2] if candidates else None


def cap_zone(market_cap: float | None) -> str:
    if market_cap is None:
        return "확인불가"
    if market_cap < 500_000_000:
        return "Micro Cap / 고위험"
    if market_cap <= 10_000_000_000:
        return "Tenbagger Zone"
    if market_cap <= 30_000_000_000:
        return "Growth Zone"
    return "Compounder"


def gm_trend(current: float | None, previous: float | None) -> str:
    if current is None or previous is None:
        return "확인불가"
    change = current - previous
    if change > 0.5:
        return "GM_UP"
    if change >= -0.5:
        return "GM_STABLE"
    return "GM_DOWN"


def dilution_band(shares_growth: float | None) -> str:
    if shares_growth is None:
        return "확인불가"
    if shares_growth > 20:
        return "강한 희석 경고"
    if shares_growth > 10:
        return "희석 경고"
    return "정상"


def classify_row(row: dict) -> dict:
    """Apply conservative states and warnings without a blended recommendation score."""
    warnings: list[str] = []
    shares_growth = finite(row.get("sharesGrowthYoY"))
    market_cap = finite(row.get("marketCap"))
    eps_revision = finite(row.get("epsRevision3M"))
    revenue_revision = finite(row.get("revenueRevision3M"))
    valuation = finite(row.get("valuationBurden"))
    forward_pe = finite(row.get("forwardPE"))
    price_sales = finite(row.get("priceToSales"))
    visibility = finite(row.get("revenueVisibility"))
    concentration = finite(row.get("customerConcentration"))

    if shares_growth is not None and shares_growth > 20:
        warnings.append("DILUTION")
    if market_cap is not None and market_cap < 500_000_000:
        warnings.append("MICRO_CAP")
    if row.get("gmTrend") == "GM_DOWN":
        warnings.append("MARGIN_DECLINE")
    if (eps_revision is not None and eps_revision < 0) or (revenue_revision is not None and revenue_revision < 0):
        warnings.append("NEGATIVE_REVISION")
    if valuation is not None and valuation >= 4 or forward_pe is not None and forward_pe >= 60 or price_sales is not None and price_sales >= 20:
        warnings.append("HIGH_VALUATION")
    if concentration is not None and concentration >= 4:
        warnings.append("CUSTOMER_CONCENTRATION")
    if visibility is not None and visibility <= 2:
        warnings.append("LOW_REVENUE_VISIBILITY")
    if finite(row.get("epsEstimateCurrent")) is not None and row["epsEstimateCurrent"] < 0:
        warnings.append("LOSS_MAKING")

    required = (
        "marketCap", "revenueGrowthCurrentFY", "epsGrowthCurrentFY",
        "epsGrowthNextFY", "epsRevision3M", "revenueRevision3M",
        "grossMarginCurrent", "sharesGrowthYoY",
    )
    if any(finite(row.get(key)) is None for key in required):
        warnings.append("DATA_INCOMPLETE")

    revenue_growth = finite(row.get("revenueGrowthCurrentFY"))
    eps_growth = finite(row.get("epsGrowthCurrentFY"))
    eps_next = finite(row.get("epsGrowthNextFY"))
    turnaround = row.get("epsGrowthState") == "TURNAROUND"
    high_growth = (
        revenue_growth is not None and revenue_growth >= 30
        and ((eps_growth is not None and eps_growth >= 40) or turnaround)
        and eps_next is not None and eps_next >= 30
    )
    strong_revision = eps_revision is not None and eps_revision >= 10 and revenue_revision is not None and revenue_revision > 0
    quality = row.get("gmTrend") in ("GM_UP", "GM_STABLE") and shares_growth is not None and shares_growth <= 10
    bottleneck = row.get("aiBottleneck") is True
    scale_ok = market_cap is not None and 500_000_000 <= market_cap <= 30_000_000_000

    if "DILUTION" in warnings:
        status = "희석 위험"
    elif market_cap is not None and market_cap > 30_000_000_000 or "HIGH_VALUATION" in warnings and high_growth:
        status = "고성장 Compounder"
    elif high_growth and strong_revision and quality and scale_ok and bottleneck:
        status = "텐배거 후보"
    elif high_growth and eps_revision is not None and eps_revision > 0:
        status = "실적 가속"
    else:
        status = "초기 탐색"

    row["status"] = status
    row["warnings"] = list(dict.fromkeys(warnings))
    row["growthRevisionProduct"] = (
        round(eps_growth * eps_revision, 4)
        if eps_growth is not None and eps_revision is not None else None
    )
    return row


def load_universe(path: Path = UNIVERSE_PATH) -> list[dict]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if frame["Ticker"].duplicated().any():
        raise ValueError("tenbagger universe contains duplicate tickers")
    numeric = [
        "Technology_Moat", "Pricing_Power", "Revenue_Visibility",
        "Customer_Concentration", "Operating_Leverage", "AI_Exposure",
        "Valuation_Burden",
    ]
    for col in numeric:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["AI_Bottleneck"] = frame["AI_Bottleneck"].str.upper().eq("TRUE")
    return frame.to_dict("records")


def collect_one(meta: dict, prior_history: list[dict], observed_at: dt.datetime) -> dict:
    import yfinance as yf

    ticker = meta["Ticker"]
    stock = yf.Ticker(ticker)
    earnings = provider_value(stock, "earnings_estimate", pd.DataFrame())
    revenue = provider_value(stock, "revenue_estimate", pd.DataFrame())
    eps_trend = provider_value(stock, "eps_trend", pd.DataFrame())
    annual = provider_value(stock, "income_stmt", pd.DataFrame())
    info = provider_value(stock, "info", {})

    current_eps = estimate_row(earnings, "0y")
    next_eps = estimate_row(earnings, "+1y")
    current_revenue = estimate_row(revenue, "0y")
    next_revenue = estimate_row(revenue, "+1y")
    eps_current = frame_cell(eps_trend, "current", "0y")
    if eps_current is None:
        eps_current = current_eps["avg"]
    eps_1m = frame_cell(eps_trend, "30daysAgo", "0y")
    eps_3m = frame_cell(eps_trend, "90daysAgo", "0y")
    today = observed_at.date()
    if eps_1m is None:
        eps_1m = closest_history(prior_history, today, "epsEstimateCurrent", 30)
    if eps_3m is None:
        eps_3m = closest_history(prior_history, today, "epsEstimateCurrent", 90)

    revenue_current = current_revenue["avg"]
    revenue_1m = closest_history(prior_history, today, "revenueEstimateCurrent", 30)
    revenue_3m = closest_history(prior_history, today, "revenueEstimateCurrent", 90)

    total_revenue_0 = statement_value(annual, ("Total Revenue", "Operating Revenue"), 0)
    total_revenue_1 = statement_value(annual, ("Total Revenue", "Operating Revenue"), 1)
    gross_profit_0 = statement_value(annual, ("Gross Profit",), 0)
    gross_profit_1 = statement_value(annual, ("Gross Profit",), 1)
    operating_income = statement_value(annual, ("Operating Income",), 0)
    gm_current = round(gross_profit_0 / total_revenue_0 * 100, 4) if gross_profit_0 is not None and total_revenue_0 else finite(info.get("grossMargins"))
    gm_previous = round(gross_profit_1 / total_revenue_1 * 100, 4) if gross_profit_1 is not None and total_revenue_1 else None
    if gm_current is not None and abs(gm_current) <= 1:
        gm_current = round(gm_current * 100, 4)

    end = today + dt.timedelta(days=1)
    start = today - dt.timedelta(days=430)
    try:
        shares = stock.get_shares_full(start=start.isoformat(), end=end.isoformat())
        shares = shares.dropna().sort_index() if shares is not None else pd.Series(dtype=float)
    except Exception:
        shares = pd.Series(dtype=float)
    shares_current = finite(shares.iloc[-1]) if len(shares) else finite(info.get("sharesOutstanding"))
    cutoff = pd.Timestamp(today - dt.timedelta(days=365))
    older = shares[shares.index.tz_localize(None) <= cutoff] if len(shares) and getattr(shares.index, "tz", None) is not None else shares[shares.index <= cutoff] if len(shares) else shares
    shares_year_ago = finite(older.iloc[-1]) if len(older) else None

    eps_growth_current = percent(current_eps["growth"])
    eps_growth_next = percent(next_eps["growth"])
    eps_state = "NORMAL"
    if current_eps["yearAgo"] is not None and current_eps["yearAgo"] <= 0 and eps_current is not None and eps_current > 0:
        eps_state = "TURNAROUND"

    market_cap = finite(info.get("marketCap"))
    if market_cap is None:
        try:
            market_cap = finite(stock.fast_info.market_cap)
        except Exception:
            market_cap = None

    row = {
        "ticker": ticker,
        "company": meta["Company"],
        "marketCap": market_cap,
        "marketCapZone": cap_zone(market_cap),
        "aiValueChain": meta["AI_ValueChain"] or None,
        "aiSubsector": meta["AI_Subsector"] or None,
        "aiBottleneck": bool(meta["AI_Bottleneck"]),
        "bottleneckType": meta["Bottleneck_Type"] or None,
        "technologyMoat": finite(meta["Technology_Moat"]),
        "pricingPower": finite(meta["Pricing_Power"]),
        "revenueVisibility": finite(meta["Revenue_Visibility"]),
        "customerConcentration": finite(meta["Customer_Concentration"]),
        "operatingLeverage": finite(meta["Operating_Leverage"]),
        "aiExposure": finite(meta["AI_Exposure"]),
        "valuationBurden": finite(meta["Valuation_Burden"]),
        "catalyst": meta["Catalyst"] or None,
        "riskSummary": meta["Risk"] or None,
        "competitors": meta.get("Competitors") or None,
        "majorCustomers": meta.get("Major_Customers") or None,
        "revenueGrowthCurrentFY": percent(current_revenue["growth"]),
        "revenueGrowthNextFY": percent(next_revenue["growth"]),
        "epsGrowthCurrentFY": eps_growth_current,
        "epsGrowthNextFY": eps_growth_next,
        "epsGrowthState": eps_state,
        "epsEstimateCurrent": eps_current,
        "epsEstimate1MAgo": eps_1m,
        "epsEstimate3MAgo": eps_3m,
        "epsRevision1M": growth(eps_current, eps_1m),
        "epsRevision3M": growth(eps_current, eps_3m),
        "revenueEstimateCurrent": revenue_current,
        "revenueEstimate1MAgo": revenue_1m,
        "revenueEstimate3MAgo": revenue_3m,
        "revenueRevision1M": growth(revenue_current, revenue_1m),
        "revenueRevision3M": growth(revenue_current, revenue_3m),
        "grossMarginCurrent": gm_current,
        "grossMarginPrevious": gm_previous,
        "grossMarginYoYChange": round(gm_current - gm_previous, 4) if gm_current is not None and gm_previous is not None else None,
        "gmTrend": gm_trend(gm_current, gm_previous),
        "operatingMargin": round(operating_income / total_revenue_0 * 100, 4) if operating_income is not None and total_revenue_0 else percent(info.get("operatingMargins")),
        "sharesOutstandingCurrent": shares_current,
        "sharesOutstanding1YAgo": shares_year_ago,
        "sharesGrowthYoY": growth(shares_current, shares_year_ago),
        "dilutionBand": dilution_band(growth(shares_current, shares_year_ago)),
        "forwardPE": finite(info.get("forwardPE")),
        "priceToSales": finite(info.get("priceToSalesTrailing12Months")),
        "enterpriseToEbitda": finite(info.get("enterpriseToEbitda")),
        "lastUpdated": observed_at.isoformat(),
        "dataStatus": "ok",
        "sources": {
            "estimates": "Yahoo Finance analyst consensus",
            "financials": "Yahoo Finance reported financial statements",
            "shares": "Yahoo Finance shares history",
            "aiMetadata": "Golden Code AI value-chain research",
        },
    }
    return classify_row(row)


def unavailable_row(meta: dict, observed_at: dt.datetime, error: Exception, previous: dict | None = None) -> dict:
    if previous:
        row = dict(previous)
        row["dataStatus"] = "stale"
        row["lastAttemptAt"] = observed_at.isoformat()
        row["failureReason"] = type(error).__name__
        row["warnings"] = list(dict.fromkeys([*row.get("warnings", []), "DATA_INCOMPLETE"]))
        return row
    row = {
        "ticker": meta["Ticker"], "company": meta["Company"],
        "marketCap": None, "marketCapZone": "확인불가",
        "aiValueChain": meta["AI_ValueChain"] or None,
        "aiSubsector": meta["AI_Subsector"] or None,
        "aiBottleneck": bool(meta["AI_Bottleneck"]),
        "bottleneckType": meta["Bottleneck_Type"] or None,
        "technologyMoat": finite(meta["Technology_Moat"]),
        "pricingPower": finite(meta["Pricing_Power"]),
        "revenueVisibility": finite(meta["Revenue_Visibility"]),
        "customerConcentration": finite(meta["Customer_Concentration"]),
        "operatingLeverage": finite(meta["Operating_Leverage"]),
        "aiExposure": finite(meta["AI_Exposure"]),
        "valuationBurden": finite(meta["Valuation_Burden"]),
        "catalyst": meta["Catalyst"] or None, "riskSummary": meta["Risk"] or None,
        "dataStatus": "unavailable", "status": "초기 탐색",
        "warnings": ["DATA_INCOMPLETE"], "lastUpdated": observed_at.isoformat(),
        "failureReason": type(error).__name__,
    }
    return row


def update_history(state: dict, rows: list[dict], observed_at: dt.datetime) -> dict:
    date = observed_at.date().isoformat()
    history = state.setdefault("history", {})
    first = state.setdefault("firstObservedAt", {})
    for row in rows:
        ticker = row["ticker"]
        first.setdefault(ticker, date)
        entry = {
            "date": date,
            "status": row.get("status"),
            "epsEstimateCurrent": row.get("epsEstimateCurrent"),
            "revenueEstimateCurrent": row.get("revenueEstimateCurrent"),
            "epsRevision3M": row.get("epsRevision3M"),
            "revenueRevision3M": row.get("revenueRevision3M"),
            "revenueGrowthCurrentFY": row.get("revenueGrowthCurrentFY"),
            "epsGrowthCurrentFY": row.get("epsGrowthCurrentFY"),
            "grossMarginCurrent": row.get("grossMarginCurrent"),
            "sharesGrowthYoY": row.get("sharesGrowthYoY"),
            "warnings": row.get("warnings", []),
        }
        old = [item for item in history.get(ticker, []) if item.get("date") != date]
        history[ticker] = sorted(old + [entry], key=lambda item: item["date"])
        row["firstObservedAt"] = first[ticker]
        row["observationCount"] = len(history[ticker])
    return state


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    temp.replace(path)


def run(limit: int | None = None, observed_at: dt.datetime | None = None) -> dict:
    observed_at = observed_at or dt.datetime.now(dt.timezone.utc)
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {"schemaVersion": 1, "history": {}, "firstObservedAt": {}}
    universe = load_universe()
    if limit:
        universe = universe[:limit]
    rows = []
    previous_rows = {row.get("ticker"): row for row in state.get("rows", [])}
    for index, meta in enumerate(universe):
        try:
            rows.append(collect_one(meta, state.get("history", {}).get(meta["Ticker"], []), observed_at))
            print(meta["Ticker"], rows[-1]["status"], flush=True)
        except Exception as exc:  # one provider failure must not erase the remaining universe
            rows.append(unavailable_row(meta, observed_at, exc, previous_rows.get(meta["Ticker"])))
            print(meta["Ticker"], "unavailable", type(exc).__name__, flush=True)
        if index + 1 < len(universe):
            time.sleep(0.35)

    if rows and all(row.get("dataStatus") in ("unavailable", "stale") for row in rows):
        raise RuntimeError("all tenbagger providers failed; prior published data preserved")
    update_history(state, rows, observed_at)
    state["updatedAt"] = observed_at.isoformat()
    state["rows"] = rows
    state["thresholds"] = {
        "revenueGrowthCurrentFY": 30, "epsGrowthCurrentFY": 40,
        "epsGrowthNextFY": 30, "epsRevision3M": 10,
        "revenueRevision3M": 0, "sharesGrowthWarning": 10,
        "sharesGrowthStrongWarning": 20, "marketCapMax": 30_000_000_000,
    }
    state["dataBasis"] = {
        "estimateRevision": "EPS는 공급자의 30/90일 추세를 우선 사용하고, 없으면 Golden Code 관찰 스냅샷을 사용합니다. 매출 컨센서스 과거값은 관찰 스냅샷이 쌓인 뒤 계산합니다.",
        "missing": "데이터가 없으면 null로 저장하며 화면에 N/A로 표시합니다. 0으로 대체하지 않습니다.",
        "classification": "상태는 연구용 규칙 판정이며 투자 추천 점수가 아닙니다.",
    }
    public = {key: value for key, value in state.items() if key != "firstObservedAt"}
    write_json(STATE_PATH, state)
    write_json(PUBLIC_PATH, public)
    return public


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    result = run(args.limit)
    failed = sum(row.get("dataStatus") != "ok" for row in result["rows"])
    print(f"tenbagger: {len(result['rows'])} stocks, {failed} unavailable")


if __name__ == "__main__":
    main()
