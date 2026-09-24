from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_US_SOURCE = ROOT / "docs" / "data" / "latest_us.json"


def load_us_pit_universe(path: Path = DEFAULT_US_SOURCE) -> list[dict]:
    """Use the current SEPA S&P500 universe snapshot without filtering on signal quality."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    selected = []
    seen: set[str] = set()
    for row in rows:
        if row.get("inUniverse") is not True:
            continue
        code = str(row.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        selected.append({
            "ticker": code,
            "name": row.get("name"),
            "close": row.get("close"),
            "market": "US",
        })
    if not selected:
        raise RuntimeError("US PIT universe is empty")
    return selected
