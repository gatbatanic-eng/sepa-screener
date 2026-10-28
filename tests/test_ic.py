import unittest

from agents import ic


def s(i, date, x, ret, status="complete"):
    return {"id": f"s{i}", "date": date, "attributes": {"a": x, "close": 100.0, "flag": True},
            "outcomes": {"5": {"status": status, "returnPct": ret}}}


def day(date, pairs, start=0):
    return [s(start + i, date, x, y) for i, (x, y) in enumerate(pairs)]


class IcTest(unittest.TestCase):
    def test_perfect_rank_gives_one_and_inverse_gives_minus_one(self):
        up = day("2026-10-01", [(i, i * 2.0) for i in range(10)])
        down = day("2026-10-02", [(i, -i * 2.0) for i in range(10)], 100)
        got = ic.daily_ics(up + down, "a", "5")
        self.assertAlmostEqual(got["2026-10-01"], 1.0)
        self.assertAlmostEqual(got["2026-10-02"], -1.0)

    def test_small_cross_section_pending_and_constant_days_are_skipped(self):
        few = day("2026-10-01", [(i, i) for i in range(ic.MIN_CROSS - 1)])
        flat = day("2026-10-02", [(1.0, i) for i in range(10)], 100)
        pending = [s(200 + i, "2026-10-03", i, i, status="pending") for i in range(10)]
        self.assertEqual(ic.daily_ics(few + flat + pending, "a", "5"), {})

    def test_numeric_keys_drop_levels_and_bools(self):
        self.assertEqual(ic.numeric_keys(day("2026-10-01", [(1, 1)])), ["a"])

    def test_short_history_gives_no_interval(self):
        r = ic.summarize_ics({f"d{i}": 0.1 for i in range(5)})
        self.assertEqual((r["status"], r["lo"], r["hi"]), ("INSUFFICIENT", None, None))
        self.assertEqual(r["meanIc"], 0.1)

    def test_enough_dates_gives_interval_and_is_deterministic(self):
        ics = {f"d{i}": (0.3 if i % 2 else -0.1) for i in range(ic.MIN_DATES)}
        a, b = ic.summarize_ics(ics), ic.summarize_ics(ics)
        self.assertEqual(a, b)
        self.assertEqual(a["status"], "OK")
        self.assertLess(a["lo"], a["meanIc"])
        self.assertGreater(a["hi"], a["meanIc"])


if __name__ == "__main__":
    unittest.main()
