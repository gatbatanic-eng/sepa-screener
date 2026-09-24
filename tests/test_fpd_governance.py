import unittest

from fpd.governance import experiment_hash, freeze_experiment, register_experiment, validate_registry


BASE = {
    "researchId": "X-001",
    "family": "FPD",
    "parentResearchId": None,
    "hypothesis": "test",
    "variables": ["x"],
    "formula": "x",
    "universe": "PIT",
    "testPeriod": "OOS",
    "primaryHorizons": [65],
    "primaryMetrics": ["IC"],
    "classification": "PRIMARY",
    "status": "PROPOSED",
}


class FPDGovernanceTests(unittest.TestCase):
    def test_freeze_hashes_definition(self):
        frozen = freeze_experiment(BASE)
        self.assertEqual(frozen["status"], "FROZEN")
        self.assertEqual(frozen["definitionHash"], experiment_hash(frozen))
        self.assertEqual(validate_registry({"experiments": [frozen]}), [])

    def test_frozen_definition_change_detected(self):
        frozen = freeze_experiment(BASE)
        frozen["formula"] = "changed"
        self.assertIn("FROZEN_DEFINITION_CHANGED:X-001", validate_registry({"experiments": [frozen]}))

    def test_duplicate_research_id_rejected(self):
        registry = {"experiments": [BASE]}
        with self.assertRaises(RuntimeError):
            register_experiment(registry, BASE)

    def test_invalid_status_detected(self):
        row = dict(BASE, status="BEST")
        self.assertIn("INVALID_STATUS:X-001", validate_registry({"experiments": [row]}))


if __name__ == "__main__":
    unittest.main()
