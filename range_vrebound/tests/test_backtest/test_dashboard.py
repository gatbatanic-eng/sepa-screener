import json
from datetime import date

import numpy as np
import pandas as pd

from src.dashboard import (
    attach_charts,
    build,
    build_chart_data,
    build_payload,
    collect_chart_symbols,
    render_html,
)
from src.market.regime import RegimeType
from src.models.signal import QualityStatus, Signal, SignalState, StrategyName


def _signal(symbol, strategy, d, signal_state, total_score=80.0, name=None):
    return Signal(
        symbol=symbol,
        name=name,
        strategy=strategy,
        date=d,
        market_regime=RegimeType.RANGE,
        setup_score=total_score,
        trigger_score=70.0,
        total_score=total_score,
        signal=signal_state,
        quality_status=QualityStatus.UNKNOWN,
        entry=None,
        stop=9500.0,
        target_1=11000.0,
        target_2=12000.0,
        rr_1=2.5,
        rr_2=4.0,
        reasons=["reason A", "reason B"],
    )


def test_build_payload_groups_by_strategy():
    signals = [
        _signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE),
        _signal("000660", StrategyName.V_REBOUND, date(2024, 1, 5), SignalState.WATCH),
    ]
    payload = build_payload(signals)
    assert "RANGE_MR" in payload
    assert "V_REBOUND" in payload
    assert len(payload["RANGE_MR"]["rows"]) == 1
    assert len(payload["V_REBOUND"]["rows"]) == 1


def test_build_payload_current_rows_only_latest_date():
    signals = [
        _signal("005930", StrategyName.RANGE_MR, date(2024, 1, 4), SignalState.WATCH),
        _signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE),
        _signal("000660", StrategyName.RANGE_MR, date(2024, 1, 4), SignalState.SETUP),
    ]
    payload = build_payload(signals)
    rows = payload["RANGE_MR"]["rows"]
    assert len(rows) == 1  # 000660은 1/4에만 있어서 최신일(1/5)엔 없음
    assert rows[0]["symbol"] == "005930"
    assert payload["RANGE_MR"]["asOf"] == "2024-01-05"


def test_build_payload_history_counts_buy_candidates_per_date():
    signals = [
        _signal("005930", StrategyName.RANGE_MR, date(2024, 1, 4), SignalState.BUY_CANDIDATE),
        _signal("000660", StrategyName.RANGE_MR, date(2024, 1, 4), SignalState.WATCH),
        _signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE),
    ]
    payload = build_payload(signals)
    history = payload["RANGE_MR"]["history"]
    assert history == [
        {"date": "2024-01-04", "total": 2, "buyCandidate": 1},
        {"date": "2024-01-05", "total": 1, "buyCandidate": 1},
    ]


def test_build_payload_empty_signals_returns_empty_payload():
    assert build_payload([]) == {}


def test_build_payload_row_includes_stock_name():
    signals = [_signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE, name="삼성전자")]
    payload = build_payload(signals)
    assert payload["RANGE_MR"]["rows"][0]["name"] == "삼성전자"


def test_build_payload_row_name_falls_back_to_symbol_when_missing():
    signals = [_signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE, name=None)]
    payload = build_payload(signals)
    assert payload["RANGE_MR"]["rows"][0]["name"] == "005930"


def test_build_payload_row_fields_are_json_safe():
    signals = [_signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE)]
    payload = build_payload(signals)
    row = payload["RANGE_MR"]["rows"][0]
    assert row["signal"] == "BUY_CANDIDATE"
    assert row["marketRegime"] == "RANGE"
    assert row["qualityStatus"] == "UNKNOWN"
    assert row["reasons"] == ["reason A", "reason B"]
    assert row["date"] == "2024-01-05"
    json.dumps(payload)  # 크래시 없이 직렬화되어야 한다


def test_render_html_embeds_data_json():
    signals = [_signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE)]
    payload = build_payload(signals)
    html = render_html(payload)
    assert "<html" in html
    assert "005930" in html
    assert json.dumps(payload, ensure_ascii=False) in html


def test_build_writes_html_file(tmp_path):
    signals = [_signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE)]
    out = tmp_path / "sub" / "index.html"
    build(signals, out)
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "005930" in content


def _bars_frame(n: int, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1.0, n)))
    volume = pd.Series(1000 + rng.random(n) * 500)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({"close": close.values, "volume": volume.values}, index=idx)


def test_collect_chart_symbols_only_current_rows():
    signals = [
        _signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE),
        _signal("005930", StrategyName.RANGE_MR, date(2024, 1, 4), SignalState.WATCH),  # 과거일, 제외
        _signal("000660", StrategyName.V_REBOUND, date(2024, 1, 5), SignalState.WATCH),
    ]
    payload = build_payload(signals)
    assert collect_chart_symbols(payload) == ["000660", "005930"]


def test_build_chart_data_shape_and_display_window():
    df = _bars_frame(300)
    chart = build_chart_data(df, display_days=250)
    assert len(chart["dates"]) == 250
    assert len(chart["close"]) == 250
    assert len(chart["volume"]) == 250
    assert len(chart["ma20"]) == 250
    assert len(chart["ma200"]) == 250
    assert len(chart["rsi"]) == 250
    assert chart["dates"][-1] == df.index[-1].strftime("%Y-%m-%d")


def test_build_chart_data_ma_uses_full_history_before_trimming():
    # 화면 표시 구간(display_days=100)이 MA200 기간보다 짧아도, 이동평균은
    # 표시 구간 밖의 과거 데이터까지 포함해 정확히 계산되어야 한다 — 화면에
    # 자르기 전에 계산하지 않으면(버그) 마지막 날 MA200이 NaN이 되어버린다.
    df = _bars_frame(300)
    chart = build_chart_data(df, display_days=100)
    assert len(chart["dates"]) == 100
    assert chart["ma200"][-1] is not None

    # 아예 200개 미만의 데이터만 넘기면(자른 뒤 계산한 것과 동일한 상황)
    # 당연히 NaN이어야 한다 — 위 결과가 우연이 아님을 대조 확인.
    short_df = df.iloc[-100:]
    short_chart = build_chart_data(short_df, display_days=100)
    assert short_chart["ma200"][-1] is None


def test_build_chart_data_nan_becomes_none():
    df = _bars_frame(50)  # MA200 계산 불가(데이터 50일뿐)
    chart = build_chart_data(df, display_days=50)
    assert chart["ma200"][-1] is None


def test_attach_charts_only_includes_symbols_in_rows():
    signals = [_signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE)]
    payload = build_payload(signals)
    fake_chart = {"dates": [], "close": []}
    attach_charts(payload, {"005930": fake_chart, "999999": fake_chart})
    assert payload["RANGE_MR"]["charts"] == {"005930": fake_chart}


def test_attach_charts_missing_symbol_silently_skipped():
    signals = [_signal("005930", StrategyName.RANGE_MR, date(2024, 1, 5), SignalState.BUY_CANDIDATE)]
    payload = build_payload(signals)
    attach_charts(payload, {})
    assert payload["RANGE_MR"]["charts"] == {}
