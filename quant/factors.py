"""적격 판정과 팩터 점수(QUANT.md §3·§4). 시장별로 따로 계산한다. 순수 함수."""
from __future__ import annotations

import datetime as dt
import hashlib

import pandas as pd

from . import config


def industry2(code) -> str | None:
    s = str(code or "").strip()
    return s[:2] if len(s) >= 2 and s[:2].isdigit() else None


def gate(row: dict, market: str, signal: str) -> str | None:
    """적격이면 None, 아니면 탈락 사유."""
    ind = industry2(row.get("industry"))
    if ind and ind in config.FINANCIAL_PREFIX[market]:
        return "financial"
    if row.get("mcap") is None:
        return "no_shares"
    if row["mcap"] < config.MIN_MARCAP[market]:
        return "small_cap"
    if row.get("fin") is None:
        return row.get("finReason") or "no_fin"
    f = row["fin"]
    if f["ni"] <= 0:
        return "loss"
    if f["ocf"] <= 0:
        return "ocf_nonpositive"
    if f["equity"] <= 0:
        return "equity_nonpositive"
    if f["liabilities"] / f["equity"] * 100 > config.MAX_DEBT_TO_EQUITY:
        return "leverage"
    px = row.get("px")
    if px is None:
        return "no_price"
    if (dt.date.fromisoformat(signal) - dt.date.fromisoformat(px["lastBar"])).days > config.STALE_BAR_DAYS:
        return "stale_price"
    if px["price"] <= px["sma200"]:
        return "below_sma200"
    return None


def indicators(row: dict) -> dict:
    f, px, mcap = row["fin"], row["px"], row["mcap"]
    cf = f["ocf"] - (f["capex"] if f.get("capex") is not None else 0.0)   # 한국은 capex 없음 → 영업현금흐름
    return {
        "earn_yield": f["ni"] / mcap, "cf_yield": cf / mcap, "book_to_price": f["equity"] / mcap,
        "ret_12_1": px["ret_12_1"], "ret_6m": px["ret_6m"], "sma_gap": px["price"] / px["sma200"] - 1.0,
        "profitability": f["opinc"] / f["assets"], "roe": f["ni"] / f["equity"],
        "cash_quality": min(max(f["ocf"] / f["ni"], 0.0), 3.0), "leverage": f["liabilities"] / f["equity"] * 100,
    }


def _rk(s: pd.Series, high_is_good: bool = True) -> pd.Series:
    return s.rank(pct=True, ascending=high_is_good) * 100.0


def score(rows: list[dict]) -> pd.DataFrame:
    """적격 종목 목록 → 지표·팩터 점수 표(symbol 오름차순). 순위는 같은 시장 적격 종목끼리의 백분위."""
    df = pd.DataFrame([{"symbol": r["symbol"], "name": r.get("name", ""), "industry": industry2(r.get("industry")),
                        "mcap": r["mcap"], **indicators(r)} for r in rows]).sort_values("symbol").reset_index(drop=True)
    if df.empty:
        return df
    df["value"] = pd.concat([_rk(df["earn_yield"]), _rk(df["cf_yield"]), _rk(df["book_to_price"])], axis=1).mean(axis=1)
    df["momentum"] = pd.concat([_rk(df["ret_12_1"]), _rk(df["ret_6m"]), _rk(df["sma_gap"])], axis=1).mean(axis=1)
    df["quality"] = pd.concat([_rk(df["profitability"]), _rk(df["roe"]), _rk(df["cash_quality"]), _rk(df["leverage"], False)],
                              axis=1).mean(axis=1)
    return df


def order(df: pd.DataFrame, variant: str, signal: str, market: str) -> list[str]:
    """변형별 순위(좋은 순). 동점은 종목코드 오름차순. control 은 (시드, 신호일, 시장, 종목) 해시 순."""
    if df.empty:
        return []
    if variant == "control":
        key = {s: hashlib.sha256(f"{config.CONTROL_SEED}|{signal}|{market}|{s}".encode()).hexdigest() for s in df["symbol"]}
        return sorted(df["symbol"], key=lambda s: key[s])
    wv, wm, wq = config.WEIGHTS[variant]
    comp = wv * df["value"] + wm * df["momentum"] + wq * df["quality"]
    tmp = df.assign(composite=comp).sort_values(["composite", "symbol"], ascending=[False, True])
    return list(tmp["symbol"])


def composite(df: pd.DataFrame, variant: str) -> pd.Series:
    wv, wm, wq = config.WEIGHTS[variant]
    return wv * df["value"] + wm * df["momentum"] + wq * df["quality"]
