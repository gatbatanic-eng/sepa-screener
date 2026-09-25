from __future__ import annotations

from statistics import mean, median

from .backtest import (
    assign_deciles,
    block_bootstrap_mean_ci,
    decile_monotonicity,
    spearman_ic,
)
from .storage import write_replaceable_json

HORIZON_BLOCK_MONTHS = {
    65: 3,
    130: 6,
    252: 12,
    504: 24,
}


def _complete_rows(cohort: dict, horizon: int) -> list[dict]:
    out = []
    for row in cohort.get("rows", []):
        outcome = row.get("outcomes", {}).get(str(horizon), {})
        if outcome.get("status") != "complete":
            continue
        signal = row.get("fpdCRZ")
        excess = outcome.get("excessReturnRaw")
        if signal is None or excess is None:
            continue
        item = dict(row)
        item["_excess"] = excess
        out.append(item)
    return out


def _cohort_metrics(cohort: dict, horizon: int) -> dict | None:
    rows = _complete_rows(cohort, horizon)
    if len(rows) < 2:
        return None

    signals = [row["fpdCRZ"] for row in rows]
    excess = [row["_excess"] for row in rows]
    ic = spearman_ic(signals, excess)
    deciles = assign_deciles(signals)
    portfolios: dict[int, list[float]] = {d: [] for d in range(1, 11)}
    for d, value in zip(deciles, excess):
        if d is not None:
            portfolios[d].append(float(value))

    decile_means = {
        d: (mean(portfolios[d]) if portfolios[d] else None)
        for d in range(1, 11)
    }
    d1, d10 = decile_means[1], decile_means[10]
    spread = None if d1 is None or d10 is None else d10 - d1
    return {
        "snapshotDate": cohort.get("snapshotDate"),
        "n": len(rows),
        "ic": ic,
        "decileMeans": decile_means,
        "D10_D1": spread,
        "D10": d10,
    }


def analyze_monthly_research(monthly: dict) -> dict:
    result = {
        "schemaVersion": 1,
        "market": monthly.get("market"),
        "rawSnapshots": monthly.get("rawSnapshots"),
        "confirmedMonthlyCohorts": monthly.get("confirmedMonthlyCohorts"),
        "horizons": {},
    }

    for horizon in (65, 130, 252, 504):
        metrics = [
            metric
            for cohort in monthly.get("cohorts", [])
            if (metric := _cohort_metrics(cohort, horizon)) is not None
        ]
        ics = [m["ic"] for m in metrics if m["ic"] is not None]
        spreads = [m["D10_D1"] for m in metrics if m["D10_D1"] is not None]
        d10s = [m["D10"] for m in metrics if m["D10"] is not None]

        decile_series = {}
        for d in range(1, 11):
            values = [m["decileMeans"][d] for m in metrics if m["decileMeans"][d] is not None]
            decile_series[d] = mean(values) if values else None

        monotonicity_input = {
            d: {"median": value}
            for d, value in decile_series.items()
        }

        block = HORIZON_BLOCK_MONTHS[horizon]
        result["horizons"][str(horizon)] = {
            "completedCohorts": len(metrics),
            "meanIC": mean(ics) if ics else None,
            "medianIC": median(ics) if ics else None,
            "positiveICRate": (
                sum(value > 0 for value in ics) / len(ics)
                if ics else None
            ),
            "meanD10_D1": mean(spreads) if spreads else None,
            "medianD10_D1": median(spreads) if spreads else None,
            "meanD10Excess": mean(d10s) if d10s else None,
            "decileMeanExcessAcrossCohorts": decile_series,
            "decileMonotonicity": decile_monotonicity(monotonicity_input),
            "D10_D1_BlockBootstrap": block_bootstrap_mean_ci(spreads, block) if spreads else None,
            "D10_BlockBootstrap": block_bootstrap_mean_ci(d10s, block) if d10s else None,
            "IC_BlockBootstrap": block_bootstrap_mean_ci(ics, block) if ics else None,
            "cohorts": metrics,
        }
    return result


def publish_backtest_summary(monthly: dict, path) -> dict:
    result = analyze_monthly_research(monthly)
    write_replaceable_json(path, result)
    return result
