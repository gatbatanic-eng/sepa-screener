from __future__ import annotations

from datetime import date

from .features import percentile_rank, robust_z


def _future_ordinal(period_end: str | None, snapshot_date: str) -> int | None:
    if not period_end:
        return None
    try:
        p = date.fromisoformat(period_end)
        s = date.fromisoformat(snapshot_date)
    except ValueError:
        return None
    return 1 if p > s else None


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
    tickers = []
    rows = []
    for ticker, items in labelled.items():
        row = next((x for x in items if x.get("forwardOrdinal") == "F1"), None)
        if row is None:
            continue
        l30 = row.get("lookbacks", {}).get("30D", {})
        tickers.append(ticker)
        rows.append({
            "ticker": ticker,
            "periodEnd": row.get("periodEnd"),
            "forwardOrdinal": "F1",
            "epsR30": l30.get("epsRevisionRaw"),
            "revenueR30": l30.get("revenueRevisionRaw"),
            "pv30": l30.get("priceReturnRaw"),
            "rs30": l30.get("relativeStrengthRaw"),
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

    return {
        "schemaVersion": 1,
        "researchId": derived.get("researchId"),
        "researchDefinitionHash": derived.get("researchDefinitionHash"),
        "snapshotDate": derived.get("snapshotDate"),
        "market": derived.get("market"),
        "signalFiscalSelector": "F1",
        "rows": rows,
    }
