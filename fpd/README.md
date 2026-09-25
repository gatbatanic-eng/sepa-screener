# FPD PIT Revision Research

This package implements the point-in-time estimate-revision research pipeline for Golden Code. It is intentionally separate from production SEPA screening.

## Active frozen definition

- Research model: `FPD-v0.2.2-FREE`
- Dataset: `FPD-PIT-US-FREE-2026`
- Frozen panel: `FPD-FREE-US-220-20260925`
- Collector: `PIT-Collector-v0.2-free`
- Schema: `1`
- Primary fiscal selector: **F1**, the nearest annual fiscal period whose `periodEnd` is strictly after the snapshot date
- Revision history always compares the exact same `periodEnd`

The full-S&P500 `FPD-v0.2.1` protocol is retained unchanged. The free variant was created before the first successful PIT estimate snapshot because FMP's free plan allows 250 calls/day and paid corporate calendars are unavailable.

## Free-tier design

- 220 symbols are frozen from the 2026-09-25 Golden Code US universe.
- Selection uses only a deterministic ticker-string hash; no price, momentum, valuation, sector, or performance variable is used.
- The panel is **not** re-optimized or performance-replaced.
- FMP usage: 1 AAPL live contract check + at most 220 estimate calls = 221 calls/day.
- Estimate requests use `retries=1` in collection so retries cannot silently consume the reserved free quota.
- 29 calls/day are deliberately left unused.
- FMP paid split/earnings calendars are not used.
- Split events are collected with a Yahoo Finance multi-ticker actions download.
- If Yahoo split coverage is unknown for a symbol, EPS revision/PV/RS are conservatively excluded while revenue revision remains available.
- Earnings-window diagnostics are unavailable in the free variant.

## Pipeline

1. `provider_fmp.py` fetches annual analyst estimates.
2. `provider_yahoo.py` fetches split actions without consuming FMP quota.
3. `normalize.py` maps provider fields to the internal schema.
4. `quality.py` rejects malformed provider rows.
5. `collector.py` records one immutable snapshot for the latest closed SEPA US session.
6. `derive.py` creates R7/R30/R60/R90, coverage, dispersion, price/benchmark returns, RS, and RA.
7. `cross_section.py` computes robust-Z F1 RV and FPD within the same-date free panel.
8. `research.py` confirms monthly cohorts only on the actual SEPA US month-end session.
9. `outcomes.py` tracks 65/130/252/504 actual-market-session forward outcomes.
10. `backtest.py` provides IC, rank deciles, monotonicity, and block-bootstrap primitives.
11. `analysis.py` aggregates completed cohorts into validation summaries.
12. `governance.py` protects frozen experiment definitions.

## Integrity rules

- raw PIT snapshots are append-only
- no forward-fill or zero-fill
- no epsilon adjustment for EPS
- no fiscal-period splicing
- no paid-FMP calendar dependency in free mode
- unknown split coverage excludes EPS/PV/RS rather than guessing
- price and benchmark returns use the same prior snapshot selected for the revision window
- raw variables are not winsorized
- primary cross-sectional normalization uses median/MAD robust Z
- FPD composite requires both EPS and revenue revisions
- missing/delisted prices remain explicit unavailable outcomes
- research signals do not change production buy/sell classifications

## Required secret

GitHub Actions expects:

```
FMP_API_KEY
```

The free key is sufficient for the active 220-stock panel design. The workflow performs a single AAPL analyst-estimates contract check before collection.

## Tests

```bash
python -m unittest discover -s tests -p "test_fpd_*.py" -v
```

## Generated data

Immutable raw snapshots:

```
research/fpd/raw/us/YYYY/MM/YYYY-MM-DD.json.gz
```

Public rebuildable views:

```
docs/data/fpd/latest_us.json
docs/data/fpd/derived_latest_us.json
docs/data/fpd/signal_latest_us.json
docs/data/fpd/research_status_us.json
docs/data/fpd/monthly_research_us.json
docs/data/fpd/backtest_summary_us.json
```

The absence of R30/RA/FPD during the initial accumulation period is expected and must not be filled with proxy history.

## Research dashboard

A standalone research-only page is published at:

```
docs/fpd/index.html
```

It deliberately shows data-accumulation / empty validation states until the required PIT history and forward outcomes exist. It is not a production buy/sell surface.
