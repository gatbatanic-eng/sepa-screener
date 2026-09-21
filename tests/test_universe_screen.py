import unittest

import numpy as np
import pandas as pd

from sepa.config import UniverseConfig
from sepa.universe import select_kr_candidates
from screening import _merge_kr_market_snapshot


class KoreanUniverseFallbackTest(unittest.TestCase):
    def _listing(self, count=120):
        return pd.DataFrame({
            "Code": [f"{i:06d}" for i in range(count)],
            "Name": [f"종목{i}" for i in range(count)],
            "Market": ["KOSPI" if i % 2 == 0 else "KOSDAQ" for i in range(count)],
            "Amount": [np.nan] * count,
            "Marcap": [float(count - i) * 1_000_000_000 for i in range(count)],
        })

    def test_empty_amount_falls_back_to_market_cap(self):
        cfg = UniverseConfig(kr_liquidity_candidate_n=100)
        result = select_kr_candidates(self._listing(), cfg)

        self.assertEqual(len(result), 100)
        self.assertEqual(result.iloc[0]["Code"], "000000")
        self.assertEqual(result.iloc[-1]["Code"], "000099")

    def test_pykrx_snapshot_repairs_empty_fdr_values(self):
        listing = self._listing(3)
        listing["Marcap"] = np.nan
        snapshot = pd.DataFrame(
            {"시가총액": [30.0, 20.0, 10.0], "거래대금": [3.0, 2.0, 1.0]},
            index=["000000", "000001", "000002"],
        )
        repaired = _merge_kr_market_snapshot(listing, snapshot)

        self.assertEqual(repaired["Marcap"].tolist(), [30.0, 20.0, 10.0])
        self.assertEqual(repaired["Amount"].tolist(), [3.0, 2.0, 1.0])

    def test_valid_amount_keeps_liquidity_ranking(self):
        listing = self._listing(10)
        listing["Amount"] = list(range(10))
        cfg = UniverseConfig(kr_liquidity_candidate_n=5)
        result = select_kr_candidates(listing, cfg)

        self.assertEqual(result["Code"].tolist(), ["000009", "000008", "000007", "000006", "000005"])


if __name__ == "__main__":
    unittest.main()
