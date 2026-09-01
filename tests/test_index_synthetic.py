"""추적 지수(index_builder) 로직 검증 — 실제 API 호출 없이 mock 데이터로.

실행:  python tests/test_index_synthetic.py   (또는 pytest)
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from screener import index_builder as ib  # noqa: E402


def _screening(rows: list[tuple]) -> pd.DataFrame:
    """rows: (symbol, signal, composite_score, price)"""
    return pd.DataFrame(rows, columns=["symbol", "signal", "composite_score", "price"])


def _fixed_prices(mapping: dict):
    return lambda symbols: {s: mapping[s] for s in symbols if s in mapping}


# ---------------------------------------------------------------------------
def test_first_run_starts_at_1000():
    scr = _screening([
        ("AAA", "BUY", 90, 100.0),
        ("BBB", "BUY", 85, 200.0),
        ("CCC", "WATCH", 70, 50.0),
    ])
    state, row = ib.step(scr, None, _fixed_prices({"AAA": 100.0, "BBB": 200.0}),
                         today=date(2026, 3, 10), top_n=20, freq="monthly")
    assert row["index_value"] == 1000.0, row
    assert row["is_rebalance_day"] == "true"
    assert state["base_value"] == 1000.0
    assert [h["symbol"] for h in state["holdings"]] == ["AAA", "BBB"]  # WATCH 제외
    assert all(h["entry_date"] == "2026-03-10" for h in state["holdings"])
    assert state["holdings"][0]["entry_price"] == 100.0


def test_equal_weight_return_matches_hand_calc():
    """종목 3개 +10% / -5% / +20%  →  평균 +8.333%  →  지수 1083.3333"""
    state = {
        "base_value": 1000.0, "base_date": "2026-03-02",
        "last_rebalance_date": "2026-03-02",
        "next_rebalance_date": "2026-04-01",
        "last_rebalance_index_value": 1000.0,
        "freq": "monthly", "top_n": 20,
        "holdings": [
            {"symbol": "A", "weight": 1/3, "entry_price": 100.0, "entry_date": "2026-03-02"},
            {"symbol": "B", "weight": 1/3, "entry_price": 100.0, "entry_date": "2026-03-02"},
            {"symbol": "C", "weight": 1/3, "entry_price": 100.0, "entry_date": "2026-03-02"},
        ],
    }
    scr = _screening([("A", "BUY", 90, 110.0), ("B", "BUY", 88, 95.0), ("C", "BUY", 80, 120.0)])
    new_state, row = ib.step(
        scr, state, _fixed_prices({"A": 110.0, "B": 95.0, "C": 120.0}),
        today=date(2026, 3, 20), top_n=20, freq="monthly",
    )
    assert row["is_rebalance_day"] == "false"
    assert abs(row["index_value"] - 1083.3333) < 1e-3, row["index_value"]
    # 비리밸런싱이므로 구성종목·entry_price 불변
    assert [h["symbol"] for h in new_state["holdings"]] == ["A", "B", "C"]
    assert new_state["holdings"][0]["entry_price"] == 100.0
    assert new_state["last_rebalance_index_value"] == 1000.0


def test_rebalance_records_old_close_then_swaps_basket():
    state = {
        "base_value": 1000.0, "base_date": "2026-02-02",
        "last_rebalance_date": "2026-02-02",
        "next_rebalance_date": "2026-03-01",     # today >= 이 날짜 → 리밸런싱
        "last_rebalance_index_value": 1000.0,
        "freq": "monthly", "top_n": 20,
        "holdings": [
            {"symbol": "OLD1", "weight": 0.5, "entry_price": 100.0, "entry_date": "2026-02-02"},
            {"symbol": "OLD2", "weight": 0.5, "entry_price": 100.0, "entry_date": "2026-02-02"},
        ],
    }
    scr = _screening([
        ("NEW1", "BUY", 95, 50.0),
        ("NEW2", "BUY", 90, 40.0),
        ("NEW3", "BUY", 82, 30.0),
        ("NEW4", "WATCH", 79, 10.0),
    ])
    prices = {"OLD1": 110.0, "OLD2": 110.0, "NEW1": 50.0, "NEW2": 40.0, "NEW3": 30.0}
    new_state, row = ib.step(scr, state, _fixed_prices(prices),
                             today=date(2026, 3, 3), top_n=20, freq="monthly")

    # 1) 기존 바스켓 마감값이 먼저 기록됨: 1000 * (1 + 0.10) = 1100
    assert row["is_rebalance_day"] == "true"
    assert abs(row["index_value"] - 1100.0) < 1e-6, row["index_value"]
    assert row["num_constituents"] == 2  # 기록 시점은 아직 기존 2종목

    # 2) 새 바스켓으로 교체 + entry_price 재설정
    assert [h["symbol"] for h in new_state["holdings"]] == ["NEW1", "NEW2", "NEW3"]
    assert new_state["holdings"][0]["entry_price"] == 50.0
    assert all(h["entry_date"] == "2026-03-03" for h in new_state["holdings"])
    assert abs(new_state["holdings"][0]["weight"] - 1/3) < 1e-6

    # 3) last_rebalance_index_value 가 방금 계산한 마감값으로 갱신
    assert abs(new_state["last_rebalance_index_value"] - 1100.0) < 1e-6
    assert new_state["last_rebalance_date"] == "2026-03-03"
    assert new_state["next_rebalance_date"] == "2026-04-01"


def test_rebalance_with_zero_buy_keeps_basket():
    state = {
        "base_value": 1000.0, "base_date": "2026-02-02",
        "last_rebalance_date": "2026-02-02",
        "next_rebalance_date": "2026-03-01",
        "last_rebalance_index_value": 1000.0,
        "freq": "monthly", "top_n": 20,
        "holdings": [
            {"symbol": "KEEP1", "weight": 0.5, "entry_price": 100.0, "entry_date": "2026-02-02"},
            {"symbol": "KEEP2", "weight": 0.5, "entry_price": 100.0, "entry_date": "2026-02-02"},
        ],
    }
    scr = _screening([("X", "WATCH", 70, 10.0), ("Y", "SELL", 20, 5.0)])  # BUY 0개
    prices = {"KEEP1": 90.0, "KEEP2": 90.0}
    new_state, row = ib.step(scr, state, _fixed_prices(prices),
                             today=date(2026, 3, 5), top_n=20, freq="monthly")
    assert [h["symbol"] for h in new_state["holdings"]] == ["KEEP1", "KEEP2"]
    assert new_state["holdings"][0]["entry_price"] == 100.0  # 재설정 안 됨
    assert "유지" in row["_note"]
    # 마감값은 기록됨: 1000 * (1 - 0.10) = 900
    assert abs(row["index_value"] - 900.0) < 1e-6
    # 다음 리밸런싱일은 미뤄짐
    assert new_state["next_rebalance_date"] == "2026-04-01"


def test_state_json_roundtrip():
    scr = _screening([("AAA", "BUY", 90, 100.0), ("BBB", "BUY", 85, 200.0)])
    state, _ = ib.step(scr, None, _fixed_prices({"AAA": 100.0, "BBB": 200.0}),
                       today=date(2026, 3, 10), top_n=20, freq="monthly")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "index_state.json"
        ib.save_state(p, state)
        loaded = ib.load_state(p)
    assert loaded == state, (loaded, state)


def test_history_append_and_dedup(tmp_path=None):
    import tempfile as _t
    d = Path(_t.mkdtemp())
    hp = d / "index_history.csv"
    ib.append_history(hp, ib._history_row("2026-03-01", 1000.0, 3, True))
    ib.append_history(hp, ib._history_row("2026-03-02", 1010.0, 3, False))
    ib.append_history(hp, ib._history_row("2026-03-02", 1015.0, 3, False))  # 같은 날 재실행
    hist = pd.read_csv(hp)
    assert len(hist) == 2, hist
    assert hist.iloc[-1]["index_value"] == 1015.0
    assert list(hist.columns) == ["date", "index_value", "num_constituents", "is_rebalance_day"]


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
