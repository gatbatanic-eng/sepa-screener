"""CLI 진입점 — 유니버스 수집 → 팩터 스코어 → 신호 판정 → CSV 저장 + 콘솔 요약.

예:
  python -m screener.main --market kr --kr-max-tickers 20
  python -m screener.main --market all
  python -m screener.main --market us --holdings my_holdings.csv --output out.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from . import factors, signals
from .schema import empty_frame

OUTPUT_COLUMNS = [
    "symbol", "name", "market", "signal", "composite_score",
    "value_score", "momentum_score", "quality_score",
    "price", "sma200", "price_to_sma200", "ret_6m", "ret_12m",
    "per", "pbr", "ev_ebitda", "shareholder_yield", "roe",
    "debt_to_equity", "net_income_positive", "reason",
]


def _load_market(market: str, args, verbose: bool) -> pd.DataFrame:
    if market == "kr":
        from . import data_kr
        raw = data_kr.load(
            max_tickers=args.kr_max_tickers,
            min_marcap=args.kr_min_marcap,
            verbose=verbose,
        )
    elif market == "us":
        from . import data_us
        raw = data_us.load(
            max_tickers=args.us_max_tickers,
            min_marcap=args.us_min_marcap,
            verbose=verbose,
        )
    else:
        raise ValueError(market)
    if raw.empty:
        return raw
    # 팩터 순위는 '유니버스 내' 이므로 시장별로 계산
    return factors.compute_factor_scores(raw)


def run(args) -> pd.DataFrame:
    verbose = not args.quiet
    markets = ["kr", "us"] if args.market == "all" else [args.market]

    parts = []
    for m in markets:
        try:
            df = _load_market(m, args, verbose)
            if not df.empty:
                parts.append(df)
        except Exception as e:  # noqa: BLE001
            print(f"[{m}] 수집 실패: {type(e).__name__}: {e}", file=sys.stderr)

    if not parts:
        print("수집된 종목이 없습니다.", file=sys.stderr)
        return empty_frame()

    scored = pd.concat(parts, ignore_index=True)

    holdings = None
    if args.holdings:
        holdings = pd.read_csv(args.holdings, dtype={"symbol": str})
        print(f"보유종목 {len(holdings)}건 로드 (트레일링 스탑 판정 포함)")

    result = signals.classify(scored, holdings=holdings)

    for c in OUTPUT_COLUMNS:
        if c not in result.columns:
            result[c] = pd.NA
    result = result[OUTPUT_COLUMNS].sort_values(
        ["signal", "composite_score"], ascending=[True, False]
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n결과 저장: {out_path}  ({len(result)}종목)")

    _print_summary(result, top=args.top)
    return result


def _print_summary(df: pd.DataFrame, top: int) -> None:
    cols = ["symbol", "name", "market", "composite_score",
            "value_score", "momentum_score", "quality_score", "reason"]
    counts = df["signal"].value_counts().to_dict()
    print("\n" + "=" * 70)
    print("신호 요약: " + ", ".join(f"{k} {counts.get(k, 0)}"
                                   for k in ("BUY", "WATCH", "SELL", "NEUTRAL")))
    print("=" * 70)
    for sig in ("BUY", "WATCH", "SELL"):
        sub = df[df["signal"] == sig].head(top)
        print(f"\n── {sig} (상위 {min(top, len(sub))}/{(df['signal'] == sig).sum()}) ──")
        if sub.empty:
            print("  (없음)")
            continue
        with pd.option_context("display.max_colwidth", 48, "display.width", 200):
            print(sub[cols].to_string(index=False))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Trending Value-Quality 복합 팩터 스크리너")
    p.add_argument("--market", choices=["kr", "us", "all"], default="all")
    p.add_argument("--kr-max-tickers", type=int, default=300,
                   help="한국: 시총 상위 N종목만 처리 (0=제한없음)")
    p.add_argument("--us-max-tickers", type=int, default=120,
                   help="미국: S&P500 상위 N종목만 처리 (yfinance 속도 제약)")
    p.add_argument("--kr-min-marcap", type=float, default=1_000 * 1e8,
                   help="한국 최소 시가총액(원). 기본 1,000억")
    p.add_argument("--us-min-marcap", type=float, default=2e9,
                   help="미국 최소 시가총액(달러). 기본 20억")
    p.add_argument("--holdings", type=str, default=None,
                   help="보유종목 CSV (symbol,buy_price,peak_price)")
    p.add_argument("--output", type=str, default="output/screening_result.csv")
    p.add_argument("--top", type=int, default=15, help="콘솔 출력 시 신호별 상위 N")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.kr_max_tickers == 0:
        args.kr_max_tickers = None
    if args.us_max_tickers == 0:
        args.us_max_tickers = None
    df = run(args)
    return 0 if not df.empty else 1


if __name__ == "__main__":
    raise SystemExit(main())
