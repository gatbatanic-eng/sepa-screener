"""합성 데이터로 factors.py / signals.py 로직 검증 (네트워크 불필요).

실행:  python tests/test_factors_synthetic.py   (또는 pytest)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from screener import factors, signals  # noqa: E402


# ---------------------------------------------------------------------------
# 합성 유니버스: 명확히 우수한 종목 1, 명확히 나쁜 종목 1, 특수 케이스 2,
# 순위 분산을 위한 평범한 종목 8개
# ---------------------------------------------------------------------------
def _mediocre_rows(n: int) -> list[dict]:
    rows = []
    for i in range(n):
        f = i / max(n - 1, 1)  # 0..1
        rows.append(
            dict(
                symbol=f"MID{i:02d}",
                name=f"평범{i}",
                market="US",
                price=100 + 5 * i,
                sma200=100 + 4 * i,          # 대체로 현재가 ≈ 200MA 부근
                ret_6m=-0.02 + 0.02 * f,     # -2% ~ 0%
                ret_12m=-0.05 + 0.15 * f,
                per=15 + 20 * f,
                pbr=1.5 + 2.5 * f,
                ev_ebitda=8 + 6 * f,
                shareholder_yield=0.005 + 0.01 * f,
                roe=6 + 8 * f,
                debt_to_equity=80 + 120 * f,
                net_income_positive=True,
            )
        )
    return rows


def build_universe() -> pd.DataFrame:
    rows: list[dict] = []

    # 모든 지표 우수 → BUY 되어야 함
    rows.append(dict(
        symbol="GREAT", name="우량성장", market="US",
        price=120, sma200=100,                 # +20% > 200MA
        ret_6m=0.25, ret_12m=0.55,
        per=6, pbr=0.7, ev_ebitda=4,
        shareholder_yield=0.05,
        roe=26, debt_to_equity=30, net_income_positive=True,
    ))

    # 모든 지표 나쁨 → SELL 되어야 함
    rows.append(dict(
        symbol="AWFUL", name="부실추락", market="US",
        price=55, sma200=100,                  # -45% < 200MA
        ret_6m=-0.42, ret_12m=-0.60,
        per=90, pbr=9.5, ev_ebitda=40,
        shareholder_yield=0.0,
        roe=-8, debt_to_equity=420, net_income_positive=False,
    ))

    # 저평가지만 모멘텀 미확인 (추세이탈은 아님) → WATCH, BUY 아님
    rows.append(dict(
        symbol="VALUE", name="소외가치", market="US",
        price=101, sma200=100,                 # 200MA 바로 위 (추세이탈 아님)
        ret_6m=0.01, ret_12m=-0.04,            # +1% < +3%  → 모멘텀 약함
        per=5, pbr=0.6, ev_ebitda=3.5,
        shareholder_yield=0.06,
        roe=11, debt_to_equity=70, net_income_positive=True,
    ))

    # 견조한 펀더멘털이나 추세 이탈 → SELL (현재가<200MA & 6M<0)
    rows.append(dict(
        symbol="BROKEN", name="추세이탈", market="US",
        price=80, sma200=100,
        ret_6m=-0.22, ret_12m=-0.08,
        per=12, pbr=1.4, ev_ebitda=7,
        shareholder_yield=0.02,
        roe=15, debt_to_equity=95, net_income_positive=True,
    ))

    rows.extend(_mediocre_rows(8))
    return pd.DataFrame(rows)


def _sig(df: pd.DataFrame, sym: str) -> str:
    return df.loc[df["symbol"] == sym, "signal"].iloc[0]


def _row(df: pd.DataFrame, sym: str) -> pd.Series:
    return df.loc[df["symbol"] == sym].iloc[0]


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------
def test_excellent_stock_is_buy():
    df = signals.classify(factors.compute_factor_scores(build_universe()))
    r = _row(df, "GREAT")
    assert r["composite_score"] >= 80, r["composite_score"]
    assert _sig(df, "GREAT") == "BUY", (r["signal"], r["reason"])


def test_terrible_stock_is_sell():
    df = signals.classify(factors.compute_factor_scores(build_universe()))
    r = _row(df, "AWFUL")
    assert r["composite_score"] < 40, r["composite_score"]
    assert _sig(df, "AWFUL") == "SELL", (r["signal"], r["reason"])


def test_undervalued_no_momentum_is_watch_not_buy():
    df = signals.classify(factors.compute_factor_scores(build_universe()))
    r = _row(df, "VALUE")
    assert r["value_score"] >= 80, r["value_score"]
    assert _sig(df, "VALUE") == "WATCH", (r["signal"], r["reason"], r["composite_score"])


def test_trend_break_is_sell():
    df = signals.classify(factors.compute_factor_scores(build_universe()))
    assert _sig(df, "BROKEN") == "SELL", _row(df, "BROKEN")["reason"]


def test_trailing_stop_overrides_score():
    """보유 중이고 고점 대비 -15% 이상 하락하면 스코어와 무관하게 SELL."""
    uni = build_universe()
    # GREAT 를 보유 중, 고점 141 대비 현재가 120 → -14.9% (아직 아님)
    holdings_ok = pd.DataFrame([dict(symbol="GREAT", buy_price=100, peak_price=140)])
    df_ok = signals.classify(factors.compute_factor_scores(uni), holdings=holdings_ok)
    assert _sig(df_ok, "GREAT") == "BUY", "-14%면 아직 트레일링 스탑 아님"

    holdings_hit = pd.DataFrame([dict(symbol="GREAT", buy_price=100, peak_price=145)])
    df_hit = signals.classify(factors.compute_factor_scores(uni), holdings=holdings_hit)
    assert _sig(df_hit, "GREAT") == "SELL", "120/145-1 = -17% → 트레일링 스탑"
    assert "트레일링" in _row(df_hit, "GREAT")["reason"]


def test_missing_metrics_are_skipped_not_zero():
    """부채비율·EV/EBITDA 가 전부 NaN 이어도(=KR 소스) 스코어가 산출되어야 한다."""
    uni = build_universe()
    uni["debt_to_equity"] = np.nan
    uni["ev_ebitda"] = np.nan
    df = signals.classify(factors.compute_factor_scores(uni))
    assert df["composite_score"].notna().all()
    assert df["quality_score"].between(0, 100).all()
    # GREAT 는 여전히 최상위권
    assert _row(df, "GREAT")["composite_score"] >= 80
    # 부채비율 데이터 없음 → BUY 조건에서 통과로 간주
    assert _sig(df, "GREAT") == "BUY"


def test_scores_bounded_and_ordered():
    df = factors.compute_factor_scores(build_universe())
    for c in ("value_score", "momentum_score", "quality_score", "composite_score"):
        assert df[c].between(0, 100).all(), c
    order = df.sort_values("composite_score", ascending=False)["symbol"].tolist()
    assert order[0] == "GREAT", order
    assert order[-1] == "AWFUL", order


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
