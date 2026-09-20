"""AI 밸류체인 트래커의 네트워크 없는 단위 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_tracker import build_ai_snapshot, extend_screening_universe, load_ai_universe  # noqa: E402


def test_ai_universe_has_75_unique_tickers():
    universe = load_ai_universe()
    assert len(universe) == 75
    assert universe["Ticker"].nunique() == 75
    assert (universe["Country"] == "KR").sum() == 28
    assert (universe["Country"] != "KR").sum() == 47


def test_extend_us_universe_adds_non_sp500_ai_names():
    base = pd.DataFrame([{"Code": "NVDA", "Name": "NVIDIA", "Market": "US", "Marcap": None}])
    extended = extend_screening_universe(base, "US")
    assert len(extended) == 47
    assert {"ARM", "ASML", "FN", "MOD", "NVT", "CRWV", "SNOW", "SYM"}.issubset(set(extended["Code"]))


def test_extend_kr_universe_preserves_six_digit_codes():
    base = pd.DataFrame(columns=["Code", "Name", "Market", "Marcap"])
    extended = extend_screening_universe(base, "KR")
    assert len(extended) == 28
    assert "005930" in set(extended["Code"])
    assert extended["Code"].str.len().eq(6).all()


def test_snapshot_merges_industry_and_technical_fields():
    live = pd.DataFrame([{
        "종목코드": "NVDA", "종목명": "NVIDIA", "상태": "OK", "제외사유": "",
        "종가": 200.0, "등락률": 0.02, "전체통과(8개AND)": True, "TREND_OK_v2": True,
        "RS_백분위랭킹": 99.0, "RS_Score": 98.0, "RS_20D_Change": 4.0,
        "SetupQuality점수": 85.0, "SETUP_READY": True, "EntryState": "GO_BREAKOUT",
        "ExitState": "HOLD", "구조적손절가": 180.0, "초기리스크_pct": 0.1,
        "시장국면_v2": "GREEN", "권장진입비중": 1.0,
        "진입판정_참고용_매수신호아님": "GO",
    }])
    snapshot = build_ai_snapshot(live, "2026-09-20", "US")
    nvda = snapshot[snapshot["티커"] == "NVDA"].iloc[0]
    arm = snapshot[snapshot["티커"] == "ARM"].iloc[0]
    assert nvda["AI Exposure"] == 5
    assert nvda["Entry State"] == "GO_BREAKOUT"
    assert nvda["TREND OK v2"] is True or nvda["TREND OK v2"] == True  # noqa: E712
    assert arm["상태"] == "스크리너 미포함"
