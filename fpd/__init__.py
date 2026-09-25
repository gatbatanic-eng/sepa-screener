"""Point-in-time forward-estimate research infrastructure.

This package is intentionally independent of SEPA production screening.
Raw PIT observations are append-only research data; derived signals are
rebuildable from those observations and a frozen research definition.
"""

RESEARCH_ID = "FPD-v0.2.1"
COLLECTOR_VERSION = "PIT-Collector-v0.1"
SCHEMA_VERSION = 1

DATASET_ID = "FPD-PIT-US-2026"
RESEARCH_COHORT = "OOS_PROSPECTIVE"
