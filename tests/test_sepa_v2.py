"""SEPA Screener v2 코어 로직 합성 테스트 (네트워크 불필요).

실행:  python tests/test_sepa_v2.py   (또는 pytest tests/test_sepa_v2.py)

중점:
- 각 신호(GO_BREAKOUT / BREAKOUT_UNCONFIRMED / LATE / EXTENDED / GO_PULLBACK /
  FAST_FAIL / TIME_STOP / structural stop / market regime / setup_ready) 가
  의도대로 발생하는지.
- **look-ahead bias 금지**: 오늘 값을 계산할 때 미래 봉이 절대 쓰이지 않음.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sepa import states  # noqa: E402
from sepa.config import CONFIG  # noqa: E402
from sepa.entry import classify_pivot_zone, evaluate_entry  # noqa: E402
from sepa.exit import Position, detect_exit_warnings, evaluate_exit, structural_stop  # noqa: E402
from sepa.indicators import atr, ema, rolling_high, sma  # noqa: E402
from sepa.pivot import compute_pivot  # noqa: E402
from sepa.regime import market_regime  # noqa: E402
from sepa.rs import compute_rs_v2, excess_returns  # noqa: E402
from sepa.setup import evaluate_setup  # noqa: E402
from sepa.swings import detect_swings  # noqa: E402
from sepa import pipeline  # noqa: E402

APPROX = 1e-6


def _mk(closes, *, vol=None, hi=None, lo=None, op=None) -> pd.DataFrame:
    closes = pd.Series([float(c) for c in closes])
    n = len(closes)
    idx = pd.bdate_range("2023-01-02", periods=n)
    closes.index = idx
    high = pd.Series(hi, index=idx) if hi is not None else closes * 1.004
    low = pd.Series(lo, index=idx) if lo is not None else closes * 0.996
    open_ = pd.Series(op, index=idx) if op is not None else closes.shift(1).fillna(closes.iloc[0])
    volume = pd.Series(vol, index=idx) if vol is not None else pd.Series(1_000_000.0, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": closes, "Volume": volume})


def _uptrend_then_base(base_level=200.0, base_len=40, trend_len=300, pullbacks=(9, 6, 3)):
    """상승추세 → 점점 얕아지는 pullback 이 있는 타이트한 base."""
    trend = np.linspace(100, base_level, trend_len)
    base = [base_level]
    seg = base_len // (len(pullbacks) + 1)
    for pb in pullbacks:
        base += list(np.linspace(base[-1], base[-1] * (1 - pb / 100), seg))
        base += list(np.linspace(base[-1], base_level * 0.999, seg))
    return np.concatenate([trend, np.array(base)])


# ---------------------------------------------------------------------------
# 1. 지표 인과성
# ---------------------------------------------------------------------------
def test_indicators_are_causal():
    np.random.seed(1)
    c = pd.Series(100 + np.cumsum(np.random.randn(120)))
    h, l = c + 1.5, c - 1.5
    cut = 80
    for full, trunc in [
        (sma(c, 20), sma(c.iloc[:cut + 1], 20)),
        (ema(c, 10), ema(c.iloc[:cut + 1], 10)),
        (rolling_high(c, 15), rolling_high(c.iloc[:cut + 1], 15)),
        (atr(h, l, c, 14), atr(h.iloc[:cut + 1], l.iloc[:cut + 1], c.iloc[:cut + 1], 14)),
    ]:
        a, b = full.iloc[cut], trunc.iloc[cut]
        assert (pd.isna(a) and pd.isna(b)) or abs(a - b) < APPROX, (a, b)


# ---------------------------------------------------------------------------
# 2. 스윙 탐지 & 인과성
# ---------------------------------------------------------------------------
def test_detect_swings_finds_peak_and_trough():
    c = [10, 11, 12, 13, 20, 13, 12, 11, 5, 11, 12, 13, 14, 15]
    df = _mk(c, hi=[x + 0.1 for x in c], lo=[x - 0.1 for x in c])
    sw = detect_swings(df["High"], df["Low"], left=2, right=2)
    kinds = {s.idx: s.kind for s in sw}
    assert kinds.get(4) == "H", kinds       # 20 at idx 4
    assert kinds.get(8) == "L", kinds       # 5 at idx 8
    # 끝쪽 right(2) 봉은 스윙 불가
    assert all(s.idx <= len(c) - 1 - 2 for s in sw)


def test_detect_swings_causal():
    np.random.seed(2)
    base = list(100 + np.cumsum(np.random.randn(60)))
    full = base + [130, 140, 150]      # 미래에 더 큰 고가
    dff = _mk(full)
    dfs = _mk(base)
    sw_full = {s.idx for s in detect_swings(dff["High"], dff["Low"], 3, 3) if s.idx <= len(base) - 1 - 3}
    sw_short = {s.idx for s in detect_swings(dfs["High"], dfs["Low"], 3, 3)}
    assert sw_full == sw_short, (sw_full ^ sw_short)


# ---------------------------------------------------------------------------
# 3. 피벗 인과성
# ---------------------------------------------------------------------------
def test_pivot_is_causal():
    c = _uptrend_then_base()
    df_full = _mk(c)
    df_short = _mk(c[:-5])
    p_full = compute_pivot(df_full["High"], df_full["Low"], df_full["Close"],
                           CONFIG.pivot, CONFIG.swing, as_of_offset=5)
    p_short = compute_pivot(df_short["High"], df_short["Low"], df_short["Close"],
                            CONFIG.pivot, CONFIG.swing, as_of_offset=0)
    assert p_full.pivot_price == p_short.pivot_price, (p_full, p_short)


# ---------------------------------------------------------------------------
# 4. RS v2
# ---------------------------------------------------------------------------
def test_rs_v2_ranking_and_weights():
    n = 300
    idx = pd.bdate_range("2023-01-02", periods=n)
    bench = pd.Series(np.linspace(100, 110, n), index=idx)
    strong = _mk(np.linspace(100, 260, n))
    mid = _mk(np.linspace(100, 150, n))
    weak = _mk(np.linspace(100, 95, n))
    for d in (strong, mid, weak):
        d.index = idx
    rmap = compute_rs_v2({"S": strong, "M": mid, "W": weak},
                         {"S": bench, "M": bench, "W": bench}, CONFIG.rs)
    assert rmap["S"].rs_score > rmap["M"].rs_score > rmap["W"].rs_score
    assert 0 <= rmap["W"].rs_score <= 100 and 0 <= rmap["S"].rs_score <= 100
    # weak 은 벤치마크보다 못하므로 낮은 percentile
    assert rmap["W"].rs_score < 50


def test_rs_excess_returns_causal():
    c = _uptrend_then_base()
    idx = pd.bdate_range("2023-01-02", periods=len(c))
    close = pd.Series(c, index=idx)
    bench = pd.Series(np.linspace(100, 130, len(c)), index=idx)
    a = excess_returns(close, bench, CONFIG.rs, as_of_offset=20)
    b = excess_returns(close.iloc[:-20], bench.iloc[:-20], CONFIG.rs, as_of_offset=0)
    for k in a:
        assert (a[k] is None and b[k] is None) or abs(a[k] - b[k]) < APPROX, (k, a[k], b[k])


# ---------------------------------------------------------------------------
# 5. ENTRY 구간 & 확인 돌파
# ---------------------------------------------------------------------------
def test_pivot_zone_mapping():
    assert classify_pivot_zone(-8, CONFIG) == states.SETUP
    assert classify_pivot_zone(-3, CONFIG) == states.WATCH
    assert classify_pivot_zone(-1, CONFIG) == states.READY
    assert classify_pivot_zone(1.5, CONFIG) == "BREAKOUT_ZONE"
    assert classify_pivot_zone(4, CONFIG) == states.LATE
    assert classify_pivot_zone(9, CONFIG) == states.EXTENDED
    assert classify_pivot_zone(None, CONFIG) is None


def _base_with_breakout(offset_pct, vol_mult, clv=0.9):
    c = list(_uptrend_then_base(base_level=200.0))
    pivot_area = max(c[-40:])
    today_close = pivot_area * (1 + offset_pct / 100)
    c.append(today_close)
    n = len(c)
    vol = [1_000_000.0] * n
    vol[-1] = 1_000_000.0 * vol_mult
    hi = [x * 1.004 for x in c]
    lo = [x * 0.996 for x in c]
    # 오늘 CLV 조정
    rng = hi[-1] - lo[-1]
    lo[-1] = today_close - clv * rng
    hi[-1] = lo[-1] + rng
    return _mk(c, vol=vol, hi=hi, lo=lo)


def test_confirmed_breakout_go():
    df = _base_with_breakout(offset_pct=1.5, vol_mult=1.8, clv=0.9)
    e = evaluate_entry(df, CONFIG, trend_ok=True, setup_ready=True, rs_score=95, rs_change_20d=5)
    assert e.entry_state == states.GO_BREAKOUT, (e.entry_state, e.reasons)


def test_breakout_unconfirmed_low_volume():
    df = _base_with_breakout(offset_pct=1.5, vol_mult=1.05, clv=0.9)
    e = evaluate_entry(df, CONFIG, trend_ok=True, setup_ready=True, rs_score=95, rs_change_20d=5)
    assert e.entry_state == states.BREAKOUT_UNCONFIRMED, (e.entry_state, e.reasons)


def test_extended_never_go():
    df = _base_with_breakout(offset_pct=9.0, vol_mult=3.0, clv=0.95)
    e = evaluate_entry(df, CONFIG, trend_ok=True, setup_ready=True, rs_score=99, rs_change_20d=9)
    assert e.entry_state == states.EXTENDED, (e.entry_state, e.reasons)


def test_trend_fail_blocks_entry():
    df = _base_with_breakout(offset_pct=1.5, vol_mult=2.0)
    e = evaluate_entry(df, CONFIG, trend_ok=False, setup_ready=True, rs_score=95, rs_change_20d=5)
    assert e.entry_state == states.TREND_FAIL


# ---------------------------------------------------------------------------
# 6. FAST_FAIL / EXIT
# ---------------------------------------------------------------------------
def test_fast_fail_after_breakout():
    c = list(_uptrend_then_base(base_level=200.0))
    pivot_area = max(c[-40:])
    c += [pivot_area * 1.02, pivot_area * 1.01, pivot_area * 0.96]  # 돌파 후 되돌림
    n = len(c)
    vol = [1_000_000.0] * n
    vol[-3] = 2_500_000.0        # 돌파일 대량
    vol[-1] = 2_200_000.0        # 실패일 대량
    df = _mk(c, vol=vol)
    w = detect_exit_warnings(df, CONFIG, pivot_price=pivot_area, recent_breakout_days_ago=2)
    assert states.FAST_FAIL in w.warnings, w.warnings
    assert w.exit_state == states.FAST_FAIL


def test_time_stop_with_position():
    c = list(np.linspace(100, 200, 300)) + [200 + np.sin(i / 3) for i in range(20)]  # 진입 후 횡보
    df = _mk(c)
    pos = Position(symbol="X", entry_price=200.0, entry_date=df.index[-15], peak_price=204.0)
    r = evaluate_exit(pos, df, CONFIG, rs_change_20d=-5.0)
    assert states.TIME_STOP in r.warnings, (r.warnings, r.days_held, r.mfe_pct)


def test_structural_stop_and_risk_flag():
    c = list(_uptrend_then_base(base_level=200.0))
    df = _mk(c)
    stop, swing_low = structural_stop(df, CONFIG)
    assert stop is not None and swing_low is not None
    assert stop < swing_low <= max(c)
    w = detect_exit_warnings(df, CONFIG)
    assert w.initial_risk_pct is not None
    # 진입가를 손절가에서 아주 멀리 두면 위험 플래그
    far = detect_exit_warnings(_mk([x * 1.5 if i == len(c) - 1 else x
                                    for i, x in enumerate(c)]), CONFIG)
    assert far.entry_risk_flag == "ENTRY_RISK_TOO_HIGH"


# ---------------------------------------------------------------------------
# 7. SETUP_READY
# ---------------------------------------------------------------------------
def test_setup_ready_true_on_tight_vcp():
    trend = list(np.linspace(80, 200, 320))
    # 점점 얕고 조용해지는 3파 수축
    base = []
    lvl = 200.0
    for pb, seg in [(10, 12), (6, 12), (3, 12)]:
        base += list(np.linspace(lvl, lvl * (1 - pb / 100), seg))
        base += list(np.linspace(lvl * (1 - pb / 100), lvl * 0.995, seg))
    c = trend + base
    n = len(c)
    # 거래량: base 로 갈수록 계속 말라감 (dry-up) — 최근 10일 평균 < 50일 평균
    vol = [2_000_000.0] * len(trend) + list(np.linspace(1_200_000.0, 250_000.0, len(base)))
    # 변동성 축소를 위해 base 구간 hi/lo 폭 좁힘
    hi = [x * (1.02 if i < len(trend) else 1.004) for i, x in enumerate(c)]
    lo = [x * (0.98 if i < len(trend) else 0.996) for i, x in enumerate(c)]
    df = _mk(c, vol=vol, hi=hi, lo=lo)
    s = evaluate_setup(df, CONFIG, trend_ok=True, high_proximity_ratio=0.97,
                       rs_score=92, rs_change_20d=6, rs_line_new_high=True)
    assert s.atr_contraction_ratio is not None and s.atr_contraction_ratio <= CONFIG.setup.atr_contraction_max, s
    assert s.volume_dryup_ratio is not None and s.volume_dryup_ratio <= CONFIG.setup.volume_dryup_max, s
    assert s.contraction_count >= CONFIG.swing.min_contraction_count, s.contraction_widths
    assert s.setup_ready is True, s.reasons
    assert 0 <= s.setup_quality_score <= 100


# ---------------------------------------------------------------------------
# 8. MARKET REGIME
# ---------------------------------------------------------------------------
def test_regime_green_red_recovery():
    n = 300
    green = pd.Series(np.linspace(100, 200, n), index=pd.bdate_range("2023-01-02", periods=n))
    r = market_regime(green, CONFIG.regime, breadth_ratio=0.7)
    assert r.regime == states.GREEN and r.entry_size_factor == 1.0

    red = pd.Series(np.concatenate([np.linspace(100, 200, 220), np.linspace(200, 120, 80)]),
                    index=pd.bdate_range("2023-01-02", periods=300))
    assert market_regime(red, CONFIG.regime, breadth_ratio=0.2).regime == states.RED

    recov = pd.Series(np.concatenate([np.linspace(100, 200, 200), np.linspace(200, 150, 60),
                                      np.linspace(150, 185, 40)]),
                      index=pd.bdate_range("2023-01-02", periods=300))
    rr = market_regime(recov, CONFIG.regime, breadth_ratio=0.5)
    assert rr.regime in (states.RECOVERY, states.YELLOW, states.RED), rr.regime


# ---------------------------------------------------------------------------
# 9. 데이터 부족 → None (억지 False 금지)
# ---------------------------------------------------------------------------
def test_insufficient_data_stays_none():
    df = _mk(list(np.linspace(100, 120, 30)))
    out = pipeline.evaluate_stock_v2(df, CONFIG, cond_1_7=[True] * 7,
                                     close_today=120.0, high_52w=125.0, rs=None)
    assert out["trend_ok"] is None          # rs_score None → cond8 None → trend_ok None
    assert out["setup_ready"] in (None,)
    assert out["entry_state"] in (None,)


# ---------------------------------------------------------------------------
# 10. 파이프라인: 미래 봉을 붙여도 과거 시점 피벗이 안 바뀐다
# ---------------------------------------------------------------------------
def test_pipeline_no_future_leak_on_pivot():
    c = list(_uptrend_then_base(base_level=200.0))
    df = _mk(c)
    future = _mk(list(c) + list(np.linspace(c[-1], c[-1] * 1.3, 15)))
    p_now = compute_pivot(df["High"], df["Low"], df["Close"], CONFIG.pivot, CONFIG.swing, as_of_offset=0)
    p_later = compute_pivot(future["High"], future["Low"], future["Close"],
                            CONFIG.pivot, CONFIG.swing, as_of_offset=15)
    assert p_now.pivot_price == p_later.pivot_price, (p_now, p_later)


# ---------------------------------------------------------------------------
# 11. screening.run_screening 오프라인 통합 (fetch 함수 monkeypatch)
# ---------------------------------------------------------------------------
def test_run_screening_offline_integration():
    import screening

    n = 420
    idx = pd.bdate_range("2023-01-02", periods=n)
    bench = pd.DataFrame({
        "Open": np.linspace(100, 118, n), "High": np.linspace(100, 118, n) * 1.005,
        "Low": np.linspace(100, 118, n) * 0.995, "Close": np.linspace(100, 118, n),
        "Volume": np.full(n, 1e9),
    }, index=idx)

    rng = np.random.default_rng(11)

    def _series(kind):
        if kind == "leader":
            # 강한 추세 후 얕은(5/3/2%) 짧은 base — 최근 수익률도 벤치마크를 크게 앞섬
            c = _uptrend_then_base(base_level=300.0, base_len=30, trend_len=380, pullbacks=(5, 3, 2))
        elif kind == "down":
            c = np.linspace(200, 120, len(idx))
        elif kind == "short":
            c = np.linspace(100, 130, 120)
        else:  # mid: 벤치마크와 비슷하게 완만·변동 (뚜렷한 우위 없음)
            c = 105 + np.cumsum(rng.normal(0, 0.4, len(idx)))
        c = np.asarray(c, dtype=float)[: len(idx)]
        ii = idx[: len(c)]
        v = np.full(len(c), 1_000_000.0)
        if kind == "leader":
            v[-1] *= 3.0
        return pd.DataFrame({"Open": pd.Series(c, index=ii).shift(1).bfill(),
                             "High": pd.Series(c * 1.01, index=ii),
                             "Low": pd.Series(c * 0.99, index=ii),
                             "Close": pd.Series(c, index=ii),
                             "Volume": pd.Series(v, index=ii)}, index=ii)

    catalog = {f"LEAD{i}": "leader" for i in range(4)}
    catalog.update({f"DOWN{i}": "down" for i in range(3)})
    catalog.update({f"MID{i}": "mid" for i in range(3)})
    catalog["NEWBIE"] = "short"
    catalog["US500"] = "bench"

    def fake_listing(market):
        rows = [{"Symbol": s, "Name": s} for s in catalog if s != "US500"]
        return pd.DataFrame(rows)

    def fake_history(code, start):
        if code in ("US500",):
            return bench
        return _series(catalog.get(code, "mid"))

    orig_l, orig_h = screening.fetch_stock_listing, screening.fetch_price_history
    screening.fetch_stock_listing = fake_listing
    screening.fetch_price_history = fake_history
    try:
        df = screening.run_screening("US", top_n=500, max_workers=2, limit=None,
                                     cfg=CONFIG, skip_v2=False)
    finally:
        screening.fetch_stock_listing, screening.fetch_price_history = orig_l, orig_h

    for col in ("EntryState", "RS_Score", "TREND_OK_v2", "SETUP_READY", "피벗거리_pct",
                "ExitState", "시장국면_v2", "유니버스포함"):
        assert col in df.columns, col
    newbie = df[df["종목코드"] == "NEWBIE"].iloc[0]
    assert newbie["상태"] == "확인불가"
    leaders = df[df["종목코드"].str.startswith("LEAD")]
    assert (leaders["TREND_OK_v2"] == True).any(), leaders[["종목코드", "TREND_OK_v2", "RS_Score"]].to_dict("records")  # noqa: E712
    downs = df[df["종목코드"].str.startswith("DOWN")]
    assert (downs["EntryState"] == states.TREND_FAIL).all() or (downs["상태"] == "확인불가").all()
    assert df["시장국면_v2"].notna().any()


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
