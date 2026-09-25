import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from fpd.provider_yahoo import parse_split_download
from fpd.universe import load_frozen_free_panel, select_frozen_free_panel


class FPDFreeModeTests(unittest.TestCase):
    def test_frozen_panel_is_exactly_220(self):
        panel = load_frozen_free_panel()
        self.assertEqual(len(panel["symbols"]), 220)
        self.assertEqual(panel["panelId"], "FPD-FREE-US-220-20260925")

    def test_panel_intersection_does_not_replace_missing_names(self):
        panel = {
            "panelId": "X",
            "selection": {},
            "replacementPolicy": "NO_PERFORMANCE_BASED_REPLACEMENT",
            "symbols": ["AAA", "BBB", "CCC"],
        }
        universe = [
            {"ticker": "AAA", "name": "A", "close": 1, "market": "US"},
            {"ticker": "CCC", "name": "C", "close": 3, "market": "US"},
            {"ticker": "ZZZ", "name": "Z", "close": 9, "market": "US"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "panel.json"
            path.write_text(json.dumps(panel), encoding="utf-8")
            active, meta = select_frozen_free_panel(universe, path)
        self.assertEqual([x["ticker"] for x in active], ["AAA", "CCC"])
        self.assertEqual(meta["missingFrozenSymbols"], ["BBB"])
        self.assertNotIn("ZZZ", [x["ticker"] for x in active])

    def test_yahoo_split_parser_multi_ticker(self):
        idx = pd.to_datetime(["2026-09-01", "2026-09-02"])
        cols = pd.MultiIndex.from_tuples([
            ("AAA", "Close"),
            ("AAA", "Stock Splits"),
            ("BBB", "Close"),
            ("BBB", "Stock Splits"),
        ])
        frame = pd.DataFrame(
            [
                [100.0, 0.0, 50.0, 2.0],
                [101.0, 10.0, 25.0, 0.0],
            ],
            index=idx,
            columns=cols,
        )
        parsed = parse_split_download(frame, ["AAA", "BBB"])
        self.assertTrue(parsed["covered"]["AAA"])
        self.assertTrue(parsed["covered"]["BBB"])
        self.assertEqual(parsed["events"]["AAA"][0]["date"], "2026-09-02")
        self.assertEqual(parsed["events"]["AAA"][0]["ratio"], 10.0)
        self.assertEqual(parsed["events"]["BBB"][0]["date"], "2026-09-01")
        self.assertEqual(parsed["events"]["BBB"][0]["ratio"], 2.0)


if __name__ == "__main__":
    unittest.main()
