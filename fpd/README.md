# FPD PIT Revision Research

This package implements the point-in-time estimate-revision research pipeline for Golden Code. It is intentionally separate from production SEPA screening.

## Active frozen definition

- Research model: `FPD-v0.2.1`
- Collector: `PIT-Collector-v0.1`
- Schema: `1`
- Primary fiscal selector: **F1**, the nearest annual fiscal period whose `periodEnd` is strictly after the snapshot date
- F2 is retained as a secondary research period
- Revision history always compares the exact same `periodEnd`

`FPD-v0.2` is retained unchanged. `v0.2.1` only clarifies the F1/F2 selector before the first PIT estimate snapshot.

## Pipeline

1. `provider_fmp.py` fetches annual analyst estimates.
2. `normalize.py` maps provider fields to the internal schema.
3. `quality.py` rejects malformed provider rows.
4. `collector.py` records one immutable snapshot for the latest closed SEPA US session.
5. `derive.py` creates R7/R30/R60/R90, coverage, dispersion, price/benchmark returns, RS, and RA.
6. `cross_section.py` computes robust-Z F1 RV and FPD.
7. `research.py` confirms monthly cohorts only after the next month begins.
8. `outcomes.py` tracks 65/130/252/504-session forward outcomes.
9. `backtest.py` provides IC, rank deciles, monotonicity, and block-bootstrap primitives.
10. `analysis.py` automatically aggregates completed monthly cohorts into horizon-level IC, D10-D1, monotonicity, and block-bootstrap validation summaries.
11. `governance.py` protects frozen experiment definitions.

## Integrity rules

- raw PIT snapshots are append-only
- no forward-fill or zero-fill
- no epsilon adjustment for EPS
- no fiscal-period splicing
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

If the secret is absent, scheduled collection is skipped. Once present, the workflow first performs a live AAPL provider-contract check. Collection does not run when that contract check fails.

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
