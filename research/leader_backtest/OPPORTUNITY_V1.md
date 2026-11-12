# Opportunity Layer V1 (research-only)

Frozen on 2026-11-08. The original V1 and live recommendations remain unchanged.

- **Discovery:** broad OPPORTUNITY signal from TREND+RS, RS>=90, or RS>=70 with a volume-backed pivot break.
- **Decision:** ENTRY if the pre-existing V1 risk/chase limits are satisfied; otherwise WAIT. REJECT means no opportunity or insufficient data.
- **Evaluation:** next-day close entry; 20-trading-day close-to-close return >=15%, with no intermediate close below -8%; excludes immature samples.
- **No lookahead:** future prices only enter `evaluate`, never `classify`.
- **Caveats:** These thresholds are exploratory, not validated. No portfolio backtest or live recommendation. Repeated daily observations overlap and are not independent. Precision/recall do not equal trading profitability. Survival bias persists unless historical universes are used.
- **Next test:** compare discovery recall and precision to the unchanged V1 on the same frozen universe and dates; then test portfolio outcomes with explicit costs and controls.

Run tests: `python -m unittest discover -s tests -p 'test_opportunity.py'`
