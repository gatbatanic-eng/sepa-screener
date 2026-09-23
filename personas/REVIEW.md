# Six-lens recovery review

Open `docs/recovery/personas/` from the recovery dashboard. The daily recovery
build prepares the public evidence automatically after screening completes.

## Scope and cost

- Five automatic choices: accepted recovery entries first, then strongest rows,
  deduplicated across market and code. Missing source rows are reported, not fabricated.
- Up to three user-marked holdings and two manual choices, saved only in the
  browser. A holding marker is not a connected portfolio and cannot produce an
  account exposure, loss estimate or risk-ranked holding list.
- The searchable universe consists of current `status=OK` screener rows, including
  non-trend stocks. Missing symbols are unavailable; there is no live ticker lookup.
- Ten is the active review limit, not the count of cheap offline rule evaluations.
  Rule evidence is prepared for the universe so a static site can support immediate
  local selection without a backend or sharing holdings. The page loads only the
  catalog and selected stock files, and reuses loaded reviews during the session.
- This first release makes **zero LLM or market-data API calls** for reviews.
  The older daily persona generation step explicitly uses `--no-llm` as well, even
  if a paid-provider secret exists. Optional prose generation is not enabled.
- In a local run over the available repository snapshot, 1,172 reviews took about
  seven seconds and 10 MB total. This is an observation, not a production SLA.

## Interpretation

The seven existing evidence lenses are grouped into six by combining trend and
technical analysis. Each card carries its horizon, supporting evidence, concerns,
next checks and missing data. Cards never vote or change recovery decisions.
The SEPA structural stop and recovery execution stop remain explicitly distinct.
Assumed account-size calculations are removed from this view. Value comparisons
without peers/history are flagged as incomplete. Statistical sample limitations
from the existing tracker are retained.

The price snapshot date comes from the market history, not the generation time.
Financial period, external valuation timestamp and stale flags are shown separately.
No account balances, positions, browser selections or API secrets enter published files.

## Build and verification

`python recovery_mode.py` publishes the recovery snapshot and review page. Tests:

```sh
python -m unittest tests.test_personas tests.test_personas_data tests.test_personas_review tests.test_recovery_mode tests.test_aggressive_screen -v
node --test tests/test_personas_review.cjs
```

Identical evidence is not rewritten; changed evidence gets a new catalog revision
for cache invalidation. A failed stock fetch has a retry button; rapid selection
cannot replace the current stock with a late response for a previous selection.
Invalid saved selections are filtered, and unavailable browser storage falls back
to session-only selection. Source data errors fail the build rather than publish
an invented opinion.

Before enabling optional AI prose or using comments as trading filters, measure
unsupported statements, duplicated warnings, missed opportunities and cost over
distinct signal episodes. This release does not claim a performance improvement.
