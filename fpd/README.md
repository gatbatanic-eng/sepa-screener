# FPD PIT Revision Research

This package implements the point-in-time estimate-revision research pipeline for Golden Code. It is intentionally separate from production SEPA screening.

## Active free sandbox definition

- Research model: `FPD-v0.2.3-FREE-SANDBOX`
- Dataset: `FPD-PIT-US-SANDBOX-2026`
- Frozen panel: `FPD-FREE-SANDBOX-28-20260925`
- Collector: `PIT-Collector-v0.3-free-sandbox`
- Schema: `1`
- Primary fiscal selector: **F1**
- Revision history always compares the exact same `periodEnd`
- Revision/cohort/outcome history is restricted to the exact same `datasetId`

## Why the panel is 28 symbols

The first live free-tier scan on the 2026-09-24 US session tested 220 frozen symbols. FMP returned analyst estimates for 27 and HTTP 402 for the other 193. AAPL was separately verified by the live contract check.

That 220-symbol run is retained as an **entitlement-discovery pilot** and excluded from primary sandbox validation. The active 28-symbol panel contains only symbols whose analyst-estimates access was empirically verified. Membership was selected by provider entitlement only; no return, momentum, valuation, sector, or future-outcome information was used.

The full-S&P500 `FPD-v0.2.1` protocol and the earlier 220-symbol free experiment remain preserved separately.

## Scope

The active free dataset is a **sandbox proof of concept**, not full-universe validation. It is suitable for:

- validating PIT estimate collection
- validating revision/price divergence calculations
- building prospective history
- testing directional IC and operational stability

It is not sufficient by itself to claim that FPD generalizes across the full S&P500.

## Free-tier operation

- FMP: 1 AAPL contract check + at most 28 panel estimate calls/day
- FMP estimate collection uses `retries=1`
- paid FMP corporate calendars are not used
- split events come from Yahoo Finance multi-ticker actions
- unknown split coverage conservatively excludes EPS/PV/RS
- earnings-window diagnostics are unavailable in this free variant
- no performance-based panel replacement is allowed

## Pipeline

1. `provider_fmp.py` fetches annual analyst estimates.
2. `provider_yahoo.py` fetches split actions without FMP quota.
3. `normalize.py` normalizes estimate fields.
4. `quality.py` validates provider rows.
5. `collector.py` writes one immutable PIT snapshot for the latest closed SEPA US session.
6. `derive.py` computes R7/R30/R60/R90, coverage, dispersion, price/benchmark returns, RS, and RA using same-dataset history only.
7. `cross_section.py` computes robust-Z F1 RV and FPD.
8. `research.py` builds same-dataset month-end cohorts.
9. `outcomes.py` tracks 65/130/252/504 actual-market-session outcomes.
10. `backtest.py` and `analysis.py` provide IC and diagnostic portfolio summaries.
11. `governance.py` protects frozen definitions.

## Integrity rules

- raw PIT snapshots are append-only
- no forward-fill or zero-fill
- no epsilon adjustment for EPS
- no fiscal-period splicing
- no cross-dataset revision history
- no paid-FMP calendar dependency
- unknown split coverage excludes EPS/PV/RS rather than guessing
- raw variables are not winsorized
- FPD composite requires both EPS and revenue revisions
- research signals do not change production buy/sell classifications

## Required secret

```
FMP_API_KEY
```

The current free key has been verified successfully for the AAPL analyst-estimates contract.

## Schedule

The workflow runs after the US close on weekdays and can also be launched manually from GitHub Actions. Code merges do not automatically consume another daily FMP collection.

## Tests

```bash
python -m unittest discover -s tests -p "test_fpd_*.py" -v
```

## Data

Immutable raw snapshots:

```
research/fpd/raw/us/YYYY/MM/YYYY-MM-DD.json.gz
```

Public views:

```
docs/data/fpd/latest_us.json
docs/data/fpd/derived_latest_us.json
docs/data/fpd/signal_latest_us.json
docs/data/fpd/research_status_us.json
docs/data/fpd/monthly_research_us.json
docs/data/fpd/backtest_summary_us.json
```

The absence of R30/RA/FPD during the initial accumulation period is expected. Proxy history must not be inserted.

## Research dashboard

```
docs/fpd/index.html
```

This page is research-only and not a production buy/sell surface.
