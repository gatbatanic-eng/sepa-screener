"""momentum_signals/pipeline.py 통합 테스트 — 실제 네트워크 없이 data.py 함수를
가짜 데이터로 바꿔치기(monkeypatch)해서 1~9번 전체 흐름을 검증한다."""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as cfg  # noqa: E402
import data  # noqa: E402
import pipeline  # noqa: E402
import portfolio as pf  # noqa: E402

TODAY = dt.date(2026, 6, 1)
N = cfg.MIN_TRADING_DAYS + 10


def _trend_ohlcv(base=100.0, daily_pct=0.15, vol=1_000_000.0, seed=0, spike_last=False):
    idx = pd.date_range(end=TODAY, periods=N, freq="B")
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.3, N)
    close = base * np.cumprod(1 + (daily_pct + noise) / 100.0)
    high = close * 1.01
    low = close * 0.985
    open_ = close * 0.999
    volume = np.full(N, vol)
    if spike_last:
        volume[-1] = vol * 2.0
        high[-1] = close[-1] * 1.05
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                          "Volume": volume}, index=idx)


class FakeListing:
    @staticmethod
    def kr():
        return pd.DataFrame({"Code": [f"KR{i:03d}" for i in range(8)],
                              "Name": [f"국내종목{i}" for i in range(8)],
                              "Market": ["KOSPI"] * 8})

    @staticmethod
    def us():
        return pd.DataFrame({"Code": [f"US{i}" for i in range(6)],
                              "Name": [f"미국종목{i}" for i in range(6)],
                              "Market": ["US"] * 6})


def _fake_fetch_ohlcv(code, start):
    seed = abs(hash(code)) % 1000
    if code.startswith("KR"):
        idx = int(code[2:])
        return _trend_ohlcv(base=10_000.0, daily_pct=0.3 - idx * 0.03, vol=500_000.0,
                             seed=seed, spike_last=(idx < 2))
    idx = int(code[2:])
    return _trend_ohlcv(base=100.0, daily_pct=0.3 - idx * 0.03, vol=2_000_000.0,
                         seed=seed, spike_last=(idx < 2))


def _fake_index_ohlcv(index_code, start):
    return _trend_ohlcv(base=2500.0, daily_pct=0.05, vol=1.0, seed=1)


class TestPipelineRun(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(data, "fetch_kr_candidate_universe", return_value=FakeListing.kr()),
            mock.patch.object(data, "fetch_us_universe", return_value=FakeListing.us()),
            mock.patch.object(data, "fetch_ohlcv", side_effect=_fake_fetch_ohlcv),
            mock.patch.object(data, "fetch_index_ohlcv", side_effect=_fake_index_ohlcv),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_full_run_produces_top5_and_may_open_positions(self):
        result = pipeline.run(positions=[], today=TODAY)
        self.assertEqual(len(result["top5"]), 5)
        self.assertLessEqual(len(result["finalists"]), cfg.FINALIST_N)
        self.assertLessEqual(len(result["entries"]), cfg.ENTRY_N)
        self.assertIn("KR", result["regime"])
        self.assertIn("US", result["regime"])
        for pos in result["opened_today"]:
            self.assertGreater(pos.shares_original, 0)
            self.assertLessEqual(pos.entry_score, 100.0)

    def test_top5_has_both_markets_when_both_qualify(self):
        result = pipeline.run(positions=[], today=TODAY)
        markets = {row["metrics"].market for row in result["top5"]}
        self.assertEqual(markets, {"KR", "US"})

    def test_respects_max_concurrent_positions(self):
        existing = [pf.Position(id=f"p{i}", market="KR", code=f"HOLD{i}", name="보유",
                                 entry_date="2026-05-01", entry_price=100.0, initial_stop=90.0,
                                 entry_score=80.0, shares_original=10, shares_remaining=10)
                    for i in range(cfg.MAX_CONCURRENT_POSITIONS)]
        result = pipeline.run(positions=existing, today=TODAY)
        self.assertEqual(len(result["opened_today"]), 0)

    def test_serialize_result_is_json_safe(self):
        import json
        result = pipeline.run(positions=[], today=TODAY)
        payload = pipeline.serialize_result(result)
        json.dumps(payload, ensure_ascii=False)  # 예외 없이 직렬화돼야 함

    def test_regime_block_prevents_new_entries_in_that_market(self):
        with mock.patch.object(data, "fetch_index_ohlcv") as m:
            def side_effect(code, start):
                if code == cfg.KR_INDEX_CODE:
                    return _trend_ohlcv(base=2500.0, daily_pct=-0.3, vol=1.0, seed=2)
                return _fake_index_ohlcv(code, start)
            m.side_effect = side_effect
            result = pipeline.run(positions=[], today=TODAY)
        for pos in result["opened_today"]:
            self.assertNotEqual(pos.market, "KR")

    def test_existing_open_position_is_updated_not_duplicated(self):
        existing = [pf.Position(id="hold-1", market="KR", code="KR000", name="국내종목0",
                                 entry_date="2026-05-20", entry_price=9_000.0, initial_stop=8_500.0,
                                 entry_score=80.0, shares_original=10, shares_remaining=10)]
        result = pipeline.run(positions=existing, today=TODAY)
        kr000_positions = [p for p in result["positions"] if p.code == "KR000" and p.market == "KR"]
        self.assertEqual(len(kr000_positions), 1)
        self.assertIsNotNone(kr000_positions[0].last_close)


if __name__ == "__main__":
    unittest.main()
