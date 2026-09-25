from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_US_SOURCE = ROOT / "docs" / "data" / "latest_us.json"
FREE_PANEL_PATH = ROOT / "research" / "fpd" / "registry" / "free_sandbox_panel_us_20260925.json"


def load_us_pit_universe(path: Path = DEFAULT_US_SOURCE) -> list[dict]:
    """Use the current SEPA US universe snapshot without filtering on signal quality."""
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


def load_frozen_free_panel(path: Path = FREE_PANEL_PATH) -> dict:
    panel = json.loads(path.read_text(encoding="utf-8"))
    symbols = panel.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise RuntimeError("FPD free panel registry is empty")
    if len(symbols) > 28:
        raise RuntimeError("FPD free panel exceeds frozen 28-symbol sandbox budget")
    return panel


def select_frozen_free_panel(universe: list[dict], panel_path: Path = FREE_PANEL_PATH) -> tuple[list[dict], dict]:
    """Intersect today's observable SEPA universe with the frozen free-tier panel.

    Missing/removed names are never performance-replaced by other stocks.
    """
    panel = load_frozen_free_panel(panel_path)
    wanted = set(map(str, panel["symbols"]))
    by_ticker = {str(item["ticker"]): item for item in universe}
    active = [by_ticker[symbol] for symbol in panel["symbols"] if symbol in by_ticker]
    missing = [symbol for symbol in panel["symbols"] if symbol not in by_ticker]
    meta = {
        "panelId": panel.get("panelId"),
        "frozenSymbols": len(panel["symbols"]),
        "activeSymbols": len(active),
        "missingFrozenSymbols": missing,
        "selection": panel.get("selection"),
        "replacementPolicy": panel.get("replacementPolicy"),
    }
    if not active:
        raise RuntimeError("No frozen FPD free-panel symbols are present in current US universe")
    return active, meta
