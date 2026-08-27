import json
from datetime import date

from src.dashboard import build, build_payload, render_html
from src.market.regime import RegimeType
from src.models.signal import QualityStatus, Signal, SignalState, StrategyName


def _signal(symbol, strategy, d, signal_state, total_score=80.0):
    return Signal(
        symbol=symbol,
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
