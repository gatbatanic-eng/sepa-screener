"""비용, 포지션 상태, 의도 타입 검증, 거래 기록 스키마."""
from __future__ import annotations

import unittest

import pandas as pd

from golden_backtest.engine.costs import CostRate, Costs
from golden_backtest.engine.intents import EntryIntent, ExitIntent, StopSpec
from golden_backtest.engine.position import Position, TrancheState
from golden_backtest.records import trade as trade_mod


class TestCosts(unittest.TestCase):
    def test_default_one_way_rate_is_commission_plus_slippage(self):
        # 0.05% + 0.05% = 0.1%
        self.assertAlmostEqual(Costs(CostRate(0.0005, 0.0005)).one_way_rate(), 0.001)

    def test_per_share(self):
        # 110 × 0.001 = 0.11
        self.assertAlmostEqual(Costs(CostRate(0.0005, 0.0005)).per_share(110), 0.11)

    def test_group_override_and_unknown_group(self):
        costs = Costs(CostRate(0.0005, 0.0005), {"v": CostRate(0.0005, 0.0015)})
        self.assertAlmostEqual(costs.one_way_rate("v"), 0.002)
        with self.assertRaises(ValueError):
            costs.one_way_rate("x")

    def test_from_config_reads_yaml_default(self):
        costs = Costs.from_config()
        self.assertAlmostEqual(costs.one_way_rate(), 0.001)


def _pos(**kw):
    base = dict(ticker="T", strategy="T", signal_date=pd.Timestamp("2024-01-02"), entry_index=1,
                entry_date=pd.Timestamp("2024-01-03"), entry_price=100.0, initial_stop=92.0, stop_kind="intraday",
                risk=8.0, risk_basis="stop", trade_id=1,
                tranches={"A": TrancheState("A", 0.5), "B": TrancheState("B", 0.5)})
    base.update(kw)
    return Position(**base)


class TestPosition(unittest.TestCase):
    def test_intraday_stop_only_rises(self):
        p = _pos()
        p.raise_intraday_stop(92, "stop")
        p.raise_intraday_stop(90, "trailing")   # 내리는 제안은 무시
        self.assertEqual(p.tranches["A"].stop_intraday, 92)
        p.raise_intraday_stop(95, "trailing")
        self.assertEqual((p.tranches["A"].stop_intraday, p.tranches["A"].stop_intraday_reason), (95, "trailing"))

    def test_stop_can_target_single_tranche(self):
        p = _pos()
        p.raise_intraday_stop(100, "stop", "B")
        self.assertIsNone(p.tranches["A"].stop_intraday)
        self.assertEqual(p.tranches["B"].stop_intraday, 100)

    def test_closed_tranche_is_not_updated(self):
        p = _pos()
        p.tranches["A"].open = False
        p.raise_intraday_stop(99, "stop")
        self.assertIsNone(p.tranches["A"].stop_intraday)
        self.assertEqual(p.tranches["B"].stop_intraday, 99)

    def test_close_stop_only_rises(self):
        p = _pos()
        p.raise_close_stop(92, "stop")
        p.raise_close_stop(85, "trailing")
        self.assertEqual(p.tranches["A"].stop_close, 92)

    def test_excursions_in_r(self):
        p = _pos()
        p.update_excursions(112, 96)
        p.update_excursions(120, 94)
        p.update_excursions(118, 99)
        # MFE = (120 − 100)/8 = 2.5, MAE = (94 − 100)/8 = −0.75
        self.assertAlmostEqual(p.mfe_r, 2.5)
        self.assertAlmostEqual(p.mae_r, -0.75)


class TestIntentValidation(unittest.TestCase):
    def test_buy_stop_needs_price(self):
        with self.assertRaises(ValueError):
            EntryIntent("buy_stop")
        with self.assertRaises(ValueError):
            EntryIntent("next_open", 100.0)

    def test_tranche_weights_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            EntryIntent("next_open", None, {"A": 0.5, "B": 0.4})
        self.assertEqual(EntryIntent("next_open", None, {"A": 0.5, "B": 0.5}).tranches["A"], 0.5)

    def test_intraday_exit_needs_price(self):
        with self.assertRaises(ValueError):
            ExitIntent("intraday", "stop")

    def test_next_close_only_for_priceless_close_exit(self):
        ExitIntent("close", "partial", at="next_close")
        with self.assertRaises(ValueError):
            ExitIntent("close", "partial", price=90, at="next_close")
        with self.assertRaises(ValueError):
            ExitIntent("intraday", "stop", price=90, at="next_close")

    def test_unknown_reason_rejected(self):
        with self.assertRaises(ValueError):
            ExitIntent("close", "whatever")

    def test_stop_spec_rules(self):
        StopSpec(92, "intraday")
        StopSpec(None, None, nominal_risk=6, risk_basis="nominal_3atr")
        with self.assertRaises(ValueError):
            StopSpec(92, None)
        with self.assertRaises(ValueError):
            StopSpec(None, None)                       # 명목 리스크도 없음
        with self.assertRaises(ValueError):
            StopSpec(None, "close", nominal_risk=6)    # 손절이 없는데 kind가 있음


class TestTradeSchema(unittest.TestCase):
    def test_columns_cover_claude_md_schema_and_v13_extras(self):
        required = ["ticker", "strategy", "version", "signal_date", "entry_date", "entry_price", "initial_stop", "exit_date",
                    "exit_price", "exit_reason", "tranche", "hold_days", "costs", "r_multiple", "mfe_r", "mae_r",
                    "regime_tag", "event_flag", "strategy_version", "entry_model", "risk_basis", "stop_kind", "trade_id", "weight"]
        self.assertEqual(trade_mod.COLUMNS, required)

    def test_exit_reason_values(self):
        self.assertEqual(trade_mod.EXIT_REASONS, ("stop", "trailing", "time", "rule", "partial", "open_mtm"))

    def test_invalid_exit_reason_rejected(self):
        with self.assertRaises(ValueError):
            trade_mod.Trade(ticker="T", strategy="T", version="1.3", signal_date=pd.Timestamp("2024-01-02"),
                            entry_date=pd.Timestamp("2024-01-03"), entry_price=1, initial_stop=None, exit_date=pd.Timestamp("2024-01-04"),
                            exit_price=1, exit_reason="bogus", tranche="ALL", hold_days=1, costs=0, r_multiple=0, mfe_r=0, mae_r=0,
                            regime_tag=None, event_flag=None, strategy_version="0", entry_model="M1", risk_basis="stop",
                            stop_kind=None, trade_id=1, weight=1.0)


if __name__ == "__main__":
    unittest.main()
