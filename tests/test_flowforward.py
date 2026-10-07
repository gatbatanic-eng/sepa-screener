import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from accumulation import forward as fw
from accumulation.flowrule import FLOW_CFG, V2_CFG, flow_condition, groups, v2_condition
from nhplug.flowstore import FlowStore
from tests.test_accumulation import make


def row(d, frgn=1, inst=1, indiv=-2, close=100, vol=1000, program=None):
    x = {"bsop_date1": d, "invest": frgn, "gigwan": inst, "person": indiv, "stck_prpr": close, "acml_vol": vol}
    if program is not None:
        x["program"] = program
    return x


class StoreTest(unittest.TestCase):
    def test_merge_keeps_first_value_skips_today_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as t:
            s = FlowStore(Path(t))
            self.assertEqual(s.merge("005930", [row("20261001", 5), row("20261002", 6), row("20261006", 9)], exclude_date="20261006"), 2)
            self.assertEqual(s.merge("005930", [row("20261001", 999)]), 0)           # 이미 있는 날은 덮어쓰지 않는다
            self.assertNotIn("20261006", s.days)
            s.save()
            again = FlowStore(Path(t))
            self.assertEqual(again.days["20261001"]["005930"], [5, 1, -2, 100, 1000])
            self.assertTrue((Path(t) / "2026-10.json").exists())

    def test_program_is_stored_and_old_rows_get_only_the_program_filled(self):
        with tempfile.TemporaryDirectory() as t:
            s = FlowStore(Path(t))
            s.merge("A", [row("20261001", 5)])                                  # 예전 형식(프로그램 없음)
            self.assertEqual(len(s.days["20261001"]["A"]), 5)
            self.assertEqual(s.merge("A", [row("20261001", 999, program=7)]), 1)
            self.assertEqual(s.days["20261001"]["A"], [5, 1, -2, 100, 1000, 7])   # 기존 값은 그대로, 프로그램만 채움
            self.assertEqual(s.merge("A", [row("20261001", 999, program=8)]), 0)   # 이미 채운 값은 덮어쓰지 않음
            s.merge("B", [row("20261002", program=3)])
            self.assertEqual(s.days["20261002"]["B"][5], 3)

    def test_rows_with_missing_values_are_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            s = FlowStore(Path(t))
            self.assertEqual(s.merge("A", [{"bsop_date1": "20261001", "invest": None, "gigwan": 1, "person": 1, "stck_prpr": 1, "acml_vol": 1}]), 0)

    def test_sessions_need_enough_stocks_and_window_needs_every_day(self):
        with tempfile.TemporaryDirectory() as t:
            s = FlowStore(Path(t))
            s.merge("A", [row("20261001"), row("20261002")])
            s.merge("B", [row("20261001")])
            self.assertEqual(s.sessions(2), ["20261001"])
            self.assertIsNone(s.window("B", ["20261001", "20261002"]))
            self.assertEqual(len(s.window("A", ["20261001", "20261002"])), 2)


class RuleTest(unittest.TestCase):
    def rows(self, frgn, inst, n=20, close=100, vol=1000):
        return [[frgn, inst, -frgn - inst, close, vol] for _ in range(n)]

    def test_net_buying_share_and_days_both_required(self):
        ok, ev = flow_condition(self.rows(60, 0))                  # 매일 6% 순매수
        self.assertTrue(ok)
        self.assertAlmostEqual(ev["netShare"], 0.06)
        self.assertFalse(flow_condition(self.rows(40, 0))[0])      # 4% < 5%
        mixed = self.rows(100, 0, 11) + self.rows(-10, 0, 9)       # 순매수일 11일 < 12일
        self.assertFalse(flow_condition(mixed)[0])
        self.assertFalse(flow_condition(self.rows(60, 0, 19))[0])  # 20일이 안 되면 판정하지 않는다
        self.assertEqual(FLOW_CFG["days"], 20)

    def test_groups_nest(self):
        self.assertEqual(groups(True, True, True, True), ["FLOW", "FLOW_ACC"])
        self.assertEqual(groups(True, False, True, True), ["FLOW"])
        self.assertEqual(groups(True, True, True, False), [])
        self.assertEqual(groups(False, True, True, True), [])


class V2RuleTest(unittest.TestCase):
    def rows(self, f, i, prog=0, n=20, closes=None, vol=1000):
        closes = closes or [100] * n
        return [[f, i, -f - i, closes[k], vol, prog] for k in range(n)]

    def test_quiet_steady_base_passes(self):
        ok, ev = v2_condition(self.rows(60, 0), 0.75)                         # 매일 6%, 가격 그대로, 고점 대비 75%
        self.assertTrue(ok)
        self.assertEqual((ev["posWeeks"], ev["lead"]), (4, "FRG"))

    def test_each_condition_can_fail(self):
        self.assertFalse(v2_condition(self.rows(60, 0), 0.95)[0])             # 고점 근처(2단계) — 바닥권 아님
        self.assertFalse(v2_condition(self.rows(60, 0), 0.50)[0])             # 너무 깊은 하락
        up = [100 * (1 + 0.01 * k) for k in range(20)]                       # 20일 +19%
        self.assertFalse(v2_condition(self.rows(60, 0, closes=up), 0.75)[0])
        self.assertFalse(v2_condition(self.rows(60, 0, prog=60), 0.75)[0])    # 순매수가 전부 프로그램 매매
        lumpy = self.rows(0, 0)
        lumpy[3] = [1300, 0, -1300, 100, 1000, 0]                             # 하루에 몰린 순매수(블록딜)
        ok, ev = v2_condition(lumpy, 0.75)
        self.assertFalse(ok)
        self.assertGreater(ev["topDayShare"], V2_CFG["max_day_share"])

    def test_missing_program_is_not_judged(self):
        rows = self.rows(60, 0)
        rows[0] = rows[0][:5]
        self.assertIsNone(v2_condition(rows, 0.75)[0])

    def test_inst_led(self):
        self.assertEqual(v2_condition(self.rows(10, 50), 0.75)[1]["lead"], "INST")


class DedupeTest(unittest.TestCase):
    def test_consecutive_appearances_count_once(self):
        days = {f"2026100{i}": ["A"] for i in range(1, 6)}
        days["20261001"].append("B")
        self.assertEqual(fw.dedupe(days, cooldown=10), [("20261001", "A"), ("20261001", "B")])

    def test_reappearance_after_cooldown_counts_again(self):
        ev = {f"d{i:02d}": (["A"] if i in (0, 12) else []) for i in range(13)}
        self.assertEqual(fw.dedupe(ev, cooldown=10), [("d00", "A"), ("d12", "A")])


class ForwardTest(unittest.TestCase):
    def setUp(self):
        self.df = make()
        self.dates = [d.strftime("%Y%m%d") for d in self.df.index[-20:]]
        self.day = self.dates[-1]

    def store(self, tmp, frgn):
        s = FlowStore(Path(tmp))
        for i, d in enumerate(self.dates):
            c = float(self.df["close"].iloc[-20 + i])
            s.merge("111111", [row(d, frgn, 0, 0, int(c), 1000)])
        return s

    def test_record_day_is_write_once_and_groups_follow_rule(self):
        with tempfile.TemporaryDirectory() as t:
            store = self.store(t, 80)                                # 매일 8% 순매수
            data = {"111111.KS": self.df}
            rec = fw.record_day(store, data, {"111111": "KOSPI"}, self.day, Path(t) / "fwd", min_stocks=1)
            self.assertEqual(rec["eligible"], 1)
            self.assertEqual([p["code"] for p in rec["groups"]["FLOW"]], ["111111"])
            self.assertEqual(len(rec["groups"]["FLOW_ACC"]), 1)      # 합성 데이터는 A·B·D·E를 모두 충족
            self.assertIsNone(fw.record_day(store, data, {"111111": "KOSPI"}, self.day, Path(t) / "fwd", min_stocks=1))
            self.assertEqual(len(fw.load_records(Path(t) / "fwd")), 1)

    def test_record_is_withheld_when_prices_are_missing(self):
        with tempfile.TemporaryDirectory() as t:
            store = self.store(t, 80)
            res = fw.record_day(store, {}, {"111111": "KOSPI"}, self.day, Path(t) / "fwd", min_stocks=1)      # 가격 데이터가 하나도 없다
            self.assertIn("skipped", res)
            self.assertEqual(res["diag"]["noPrice"], 1)
            self.assertFalse((Path(t) / "fwd").exists())                                                       # 빈 기록이 남지 않는다
            late = fw.record_day(store, {"111111.KS": self.df}, {"111111": "KOSPI"}, self.day, Path(t) / "fwd", min_stocks=1)
            self.assertEqual(late["eligible"], 1)                                                              # 데이터가 오면 뒤늦게라도 기록된다

    def test_v2_waits_for_program_values_then_records(self):
        with tempfile.TemporaryDirectory() as t:
            store = self.store(t, 80)                                        # 프로그램 값 없는 예전 형식
            data = {"111111.KS": self.df}
            res = fw.record_day(store, data, {"111111": "KOSPI"}, self.day, Path(t) / "v2", min_stocks=1, series="v2")
            self.assertIn("skipped", res)
            self.assertEqual(res["diag"]["noProgram"], 1)
            for i, d in enumerate(self.dates):
                store.merge("111111", [row(d, 80, 0, 0, int(self.df["close"].iloc[-20 + i]), 1000, program=0)])
            rec = fw.record_day(store, data, {"111111": "KOSPI"}, self.day, Path(t) / "v2", min_stocks=1, series="v2")
            self.assertEqual(rec["series"], "v2")
            self.assertEqual(rec["eligible"], 1)                             # 합성 데이터는 고점 근처라 V2(바닥권) 후보는 아니다
            self.assertEqual(rec["groups"]["V2"], [])

    def test_baselines_use_same_day_rules(self):
        with tempfile.TemporaryDirectory() as t:
            store = self.store(t, 80)
            data = {"111111.KS": self.df}
            b = fw.baselines(store, data, fw.features_for(data), {"111111": "KOSPI"}, [self.day], min_stocks=1)
            self.assertEqual(b[self.day], {"C_ONLY": ["111111"], "D_ONLY": ["111111"]})

    def test_short_flow_history_is_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            store = FlowStore(Path(t))
            store.merge("111111", [row(d) for d in self.dates[-5:]])
            res = fw.record_day(store, {"111111.KS": self.df}, {"111111": "KOSPI"}, self.day, Path(t) / "fwd", min_stocks=1)
            self.assertIn("skipped", res)

    def test_forward_return_enters_next_close(self):
        df = pd.DataFrame({"close": [100.0, 100.0, 110.0, 121.0]}, index=pd.bdate_range("2026-01-05", periods=4))
        self.assertAlmostEqual(fw._fwd(df, "20260105", 1), 110 / 100 - 1 - fw.COST)   # D=01-05 → 진입 01-06(100) → 청산 01-07(110)
        self.assertIsNone(fw._fwd(df, "20260107", 1))

    def test_summarize_marks_small_samples(self):
        idx = pd.bdate_range("2026-01-05", periods=60)
        up = pd.DataFrame({"close": np.linspace(100, 160, 60)}, index=idx)
        flat = pd.DataFrame({"close": np.full(60, 100.0)}, index=idx)
        rec = {"date": "20260105", "groups": {"FLOW": [{"code": "111111", "market": "KOSPI"}], "FLOW_ACC": []}}
        out = fw.summarize(fw.events_from([rec], ("FLOW", "FLOW_ACC")), {"111111.KS": up, "222222.KS": flat}, {"111111": "KOSPI", "222222": "KOSPI"})
        s = out["groups"]["FLOW"]["5"]
        self.assertEqual(s["signals"], 1)
        self.assertFalse(s["enough"])
        self.assertGreater(s["vsAllPp"], 0)
        self.assertEqual(out["groups"]["FLOW_ACC"]["5"]["signals"], 0)


if __name__ == "__main__":
    unittest.main()
