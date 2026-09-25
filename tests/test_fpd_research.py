import unittest
from unittest.mock import patch

from fpd.research import load_all_raw


class FPDResearchDatasetTests(unittest.TestCase):
    def test_load_all_raw_filters_active_dataset(self):
        rows = [
            {"snapshotDate": "2026-09-24", "datasetId": "PILOT"},
            {"snapshotDate": "2026-09-25", "datasetId": "ACTIVE"},
        ]
        with patch("fpd.research.raw_snapshot_paths", return_value=["a", "b"]), \
             patch("fpd.research.read_gzip_json", side_effect=rows):
            result = load_all_raw("us", dataset_id="ACTIVE")
        self.assertEqual([x["snapshotDate"] for x in result], ["2026-09-25"])


if __name__ == "__main__":
    unittest.main()
