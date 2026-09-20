"""AI 밸류체인 트래커의 네트워크 없는 단위 테스트."""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_tracker import build_ai_snapshot, extend_screening_universe, load_ai_universe  # noqa: E402


@contextmanager
def _sample_universe():
    rows = []
    for idx in range(1, 76):
        country = "US" if idx <= 47 else "KR"
        ticker = f"U{idx:03d}" if country == "US" else f"{900000 + idx:06d}"
        rows.append({
            "ID": idx, "Country": country, "Ticker": ticker, "Company": f"Company {idx}",
            "Screening_Market": "US" if country == "US" else "KOSPI",
            "Primary_ValueChain": "AI Compute", "AI_Subsector": "Test",
            "AI_Revenue_Disclosure": "공시됨", "AI_Exposure": 4,
            "Bottleneck_Importance": 4, "Pricing_Power": 3, "Technology_Moat": 4,
            "CAPEX_Sensitivity": 4, "Earnings_Momentum": 4, "Supply_Constraint": 3,
            "Revenue_Visibility": 4, "Valuation_Burden": 3, "Catalyst_Strength": 4,
            "Cycle_Stage": "가속", "Stage_Tags": "B,E", "Catalyst_Date": "2027",
            "Catalyst": "Test catalyst", "Risk": "Test risk", "Valuation": "n/a",
            "Valuation_vs_History": "n/a", "AI_Priced_In": "중간",
            "Checkpoints": "Test checkpoint", "Research_Sources": "Test source",
            "Research_Update_Date": "2026-09-20", "Score_Rationale": "Test rationale",
        })
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
        path = Path(handle.name)
    pd.DataFrame(rows).to_csv(path, index=False)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_ai_universe_has_75_unique_tickers():
    with _sample_universe() as path:
        universe = load_ai_universe(path)
    assert len(universe) == 75
    assert universe["Ticker"].nunique() == 75
    assert (universe["Country"] == "KR").sum() == 28
    assert (universe["Country"] != "KR").sum() == 47


def test_extend_us_universe_adds_non_base_names():
    with _sample_universe() as path:
        base = pd.DataFrame([{"Code": "U001", "Name": "Company 1", "Market": "US", "Marcap": None}])
        extended = extend_screening_universe(base, "US", path)
    assert len(extended) == 47
    assert {"U001", "U047"}.issubset(set(extended["Code"]))


def test_extend_kr_universe_preserves_six_digit_codes():
    with _sample_universe() as path:
        base = pd.DataFrame(columns=["Code", "Name", "Market", "Marcap"])
        extended = extend_screening_universe(base, "KR", path)
    assert len(extended) == 28
    assert extended["Code"].str.len().eq(6).all()


def test_snapshot_merges_industry_and_technical_fields():
    live = pd.DataFrame([{
        "종목코드": "U001", "종목명": "Company 1", "상태": "OK", "제외사유": "",
        "종가": 200.0, "등락률": 0.02, "전체통과(8개AND)": True, "TREND_OK_v2": True,
        "RS_백분위랭킹": 99.0, "RS_Score": 98.0, "RS_20D_Change": 4.0,
        "SetupQuality점수": 85.0, "SETUP_READY": True, "EntryState": "GO_BREAKOUT",
        "ExitState": "HOLD", "구조적손절가": 180.0, "초기리스크_pct": 0.1,
        "시장국면_v2": "GREEN", "권장진입비중": 1.0,
        "진입판정_참고용_매수신호아님": "GO",
    }])
    with _sample_universe() as path:
        snapshot = build_ai_snapshot(live, "2026-09-20", "US", path)
    first = snapshot[snapshot["티커"] == "U001"].iloc[0]
    second = snapshot[snapshot["티커"] == "U002"].iloc[0]
    assert first["AI Exposure"] == 4
    assert first["Entry State"] == "GO_BREAKOUT"
    assert first["TREND OK v2"] is True or first["TREND OK v2"] == True  # noqa: E712
    assert second["상태"] == "스크리너 미포함"
