from __future__ import annotations

from datetime import date
from pathlib import Path

from .features import percentile_rank, robust_z
from .storage import write_replaceable_json

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = ROOT / "docs" / "data" / "fpd"


def select_forward_rows(derived: dict) -> dict[str, list[dict]]:
    """Annotate future annual rows F1/F2/... without altering fiscal-period identity."""
    snapshot_date = derived["snapshotDate"]
    out: dict[str, list[dict]] = {}
    for ticker, rows in derived.get("symbols", {}).items():
        future = []
        for row in rows:
            try:
                period = date.fromisoformat(str(row.get("periodEnd")))
                snap = date.fromisoformat(snapshot_date)
            except ValueError:
                continue
            if period > snap:
                future.append((period, row))
        future.sort(key=lambda x: x[0])
        labelled = []
        for i, (_, row) in enumerate(future, start=1):
            item = dict(row)
            item["forwardOrdinal"] = f"F{i}"
            labelled.append(item)
        out[ticker] = labelled
    return out


def score_primary_f1(derived: dict) -> dict:
    """Compute FPD-v0.2.1 primary F1 cross-section.

    Revision, price and RS raw values are read from the same 30D matched window.
    Missing EPS or revenue revision keeps RV composite and FPD missing.
    """
    labelled = select_forward_rows(derived)
    rows = []
    for ticker, items in labelled.items():
        row = next((x for x in items if x.get("forwardOrdinal") == "F1"), None)
        if row is None:
            continue
        l30 = row.get("lookbacks", {}).get("30D", {})
        rows.append({
            "ticker": ticker,
            "periodEnd": row.get("periodEnd"),
            "forwardOrdinal": "F1",
            "epsR30": l30.get("epsRevisionRaw"),
            "revenueR30": l30.get("revenueRevisionRaw"),
            "pv30": l30.get("priceReturnRaw"),
            "rs30": l30.get("relativeStrengthRaw"),
            "actualLagDays": l30.get("actualLagDays"),
            "epsAnalysts": row.get("epsAnalysts"),
            "revenueAnalysts": row.get("revenueAnalysts"),
            "epsDispersion": row.get("epsDispersion"),
            "revenueDispersion": row.get("revenueDispersion"),
            "epsRA_A": row.get("acceleration", {}).get("epsRA_A"),
            "revenueRA_A": row.get("acceleration", {}).get("revenueRA_A"),
            "epsRA_B": row.get("acceleration", {}).get("epsRA_B"),
            "revenueRA_B": row.get("acceleration", {}).get("revenueRA_B"),
        })

    eps_z = robust_z([r["epsR30"] for r in rows])
    rev_z = robust_z([r["revenueR30"] for r in rows])
    pv_z = robust_z([r["pv30"] for r in rows])
    rs_z = robust_z([r["rs30"] for r in rows])

    for i, row in enumerate(rows):
        row["epsR30RZ"] = eps_z[i]
        row["revenueR30RZ"] = rev_z[i]
        row["pv30RZ"] = pv_z[i]
        row["rs30RZ"] = rs_z[i]
        if eps_z[i] is None or rev_z[i] is None:
            row["rvCompositeRZ"] = None
            row["fpdCRZ"] = None
            row["fpdCRSZ"] = None
        else:
            rv = (eps_z[i] + rev_z[i]) / 2.0
            row["rvCompositeRZ"] = rv
            row["fpdCRZ"] = None if pv_z[i] is None else rv - pv_z[i]
            row["fpdCRSZ"] = None if rs_z[i] is None else rv - rs_z[i]

    fpd_rank = percentile_rank([r.get("fpdCRZ") for r in rows])
    for i, row in enumerate(rows):
        row["fpdCRZRank"] = fpd_rank[i]

    available = sum(row.get("fpdCRZ") is not None for row in rows)
    return {
        "schemaVersion": 1,
        "researchId": derived.get("researchId"),
        "datasetId": derived.get("datasetId"),
        "researchCohort": derived.get("researchCohort"),
        "researchDefinitionHash": derived.get("researchDefinitionHash"),
        "snapshotDate": derived.get("snapshotDate"),
        "market": derived.get("market"),
        "signalFiscalSelector": "F1",
        "status": "READY" if available else "DATA_ACCUMULATING",
        "manifest": {
            "eligibleF1": len(rows),
            "fpdAvailable": available,
        },
        "rows": rows,
    }


def publish_primary_f1(derived: dict) -> dict:
    result = score_primary_f1(derived)
    write_replaceable_json(PUBLIC_ROOT / "signal_latest_us.json", result)
    return result
