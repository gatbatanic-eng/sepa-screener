"""근사 백테스트 엔진(screener/backtest.py) 검증 — 네트워크 없이 mock 데이터로.

실행:  python tests/test_backtest_synthetic.py   (또는 pytest)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from screener import backtest as bt  # noqa: E402


def _series(points: dict[str, float]) -> pd.Series:
    idx = pd.to_datetime(list(points.keys()))
    return pd.Series(list(points.values()), index=idx).sort_index()


def _prices() -> dict[str, pd.Series]:
    # 각 종목: 기준 100 → 1월말 (+10/-5/+20) → 2월말 그 값에서 추가 -5%
    return {
        "A": _series({"2023-12-15": 100.0, "2024-01-31": 110.0, "2024-02-29": 104.5}),
        "B": _series({"2023-12-15": 100.0, "2024-01-31": 95.0, "2024-02-29": 90.25}),
        "C": _series({"2023-12-15": 100.0, "2024-01-31": 120.0, "2024-02-29": 114.0}),
    }


DAYS = list(pd.bdate_range("2024-01-01", "2024-02-29"))
RDATES = [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]


# ---------------------------------------------------------------------------
def test_rebalance_dates_monthly():
    d = bt.rebalance_dates(date(2024, 1, 15), date(2024, 4, 10), "monthly")
    assert d == [date(2024, 2, 1), date(2024, 3, 1), date(2024, 4, 1), date(2024, 4, 10)], d


def test_rebalance_dates_quarterly():
    d = bt.rebalance_dates(date(2024, 1, 1), date(2024, 10, 1), "quarterly")
    assert d == [date(2024, 1, 1), date(2024, 4, 1), date(2024, 7, 1), date(2024, 10, 1)], d


def test_simulate_starts_at_1000():
    eq, log = bt.simulate(RDATES, DAYS, _prices(), lambda t: (["A", "B", "C"], "init"))
    assert abs(eq.iloc[0] - 1000.0) < 1e-9, eq.iloc[0]


def test_simulate_equal_weight_and_chaining():
    eq, log = bt.simulate(RDATES, DAYS, _prices(), lambda t: (["A", "B", "C"], "init"))
    # 1월말: 1000 * (1 + mean(+10%,-5%,+20%)) = 1000 * 1.083333 = 1083.3333
    v_jan = eq.loc[pd.Timestamp("2024-01-31")]
    assert abs(v_jan - 1083.3333) < 1e-3, v_jan
    # 2월말: 위 값에서 각 -5% → 1083.3333 * 0.95 = 1029.1667
    assert abs(eq.iloc[-1] - 1029.1667) < 1e-3, eq.iloc[-1]
    assert log[0]["num_holdings"] == 3


def test_simulate_skip_keeps_basket():
    picks_seq = iter([(["A", "B", "C"], "init"), (None, "")])
    eq, log = bt.simulate(RDATES, DAYS, _prices(), lambda t: next(picks_seq))
    # 2월엔 리밸런싱 건너뜀 → 여전히 3종목, 값은 위와 동일
    assert log[1]["num_holdings"] == 3
    assert "유지" in log[1]["note"]
    assert abs(eq.iloc[-1] - 1029.1667) < 1e-3, eq.iloc[-1]


def test_simulate_no_holdings_is_flat_cash():
    eq, log = bt.simulate(RDATES, DAYS, _prices(), lambda t: (None, "no data"))
    assert (eq == 1000.0).all()
    assert log[0]["num_holdings"] == 0


def test_asof_fundamentals_respects_reporting_lag():
    annual = {
        "2022-12-28": {"roe": 8.0, "eps": 1000},
        "2023-12-28": {"roe": 12.0, "eps": 1500},
        "_dividends": {"2024-01-01": 1.0},
    }
    # 2024-02-01: FY2023 은 아직 미공시(+100일 = 2024-04) → FY2022 사용
    fy = bt._asof_fy(annual, date(2024, 2, 1), bt.KR_REPORT_LAG_DAYS)
    assert fy["roe"] == 8.0
    # 2024-06-01: FY2023 공시됨 → 최신 FY2023 사용
    fy = bt._asof_fy(annual, date(2024, 6, 1), bt.KR_REPORT_LAG_DAYS)
    assert fy["roe"] == 12.0
    # 아주 과거: 사용할 FY 없음
    assert bt._asof_fy(annual, date(2022, 1, 1), bt.KR_REPORT_LAG_DAYS) is None


def test_metrics_basic():
    idx = pd.bdate_range("2024-01-01", "2024-12-31")
    eq = pd.Series(np.linspace(1000, 1200, len(idx)), index=idx)
    m = bt._metrics(eq, "monthly")
    assert abs(m["total_return"] - 0.20) < 1e-6
    assert m["mdd"] <= 0.0 and m["mdd"] > -0.01   # 단조증가 → 낙폭 ~0
    assert 0.15 < m["cagr"] < 0.25


def test_build_row_needs_enough_history():
    short = _series({f"2024-01-{d:02d}": 100.0 + d for d in range(1, 20)})
    row = bt.build_row("X", "엑스", "KR", short, {"2023-12-28": {"roe": 10, "eps": 100, "bps": 50,
                       "net_income": 5, "dps": 1}}, date(2024, 1, 18), relaxed=False)
    assert row is None  # 200일치 미만


# ---------------------------------------------------------------------------
def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
