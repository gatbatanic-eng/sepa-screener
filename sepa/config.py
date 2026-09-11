"""
sepa/config.py — SEPA Screener v2 의 모든 임계값
================================================

한 군데에서만 파라미터를 바꾸면 되도록 전부 여기 모았다. 코드 어디에도 매직
넘버를 두지 않는다 (개발 원칙).

사용
----
    from sepa.config import CONFIG            # 기본값 싱글턴
    CONFIG.rs.rs_min                          # 예: 80.0

백테스트/실험용 오버라이드
--------------------------
- ``sepa/config_overrides.json`` 파일이 있으면 자동으로 병합한다.
- 환경변수 ``SEPA_CONFIG_JSON`` (JSON 문자열) 이 있으면 그다음으로 병합한다.
- 코드에서 ``load_config(overrides={...})`` 로 직접 만들 수도 있다.

병합은 얕은(1-depth) 그룹 단위가 아니라 "그룹.필드" 점 표기로 지정한다.
예: ``{"rs.rs_min": 75, "entry.go_max_pct": 2.5}``
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

log = logging.getLogger("sepa.config")

_OVERRIDES_FILE = Path(__file__).resolve().parent / "config_overrides.json"


# ---------------------------------------------------------------------------
# 데이터 요건 (기존 screening.py 와 동일 값 — v2 도 같은 최소 요건을 쓴다)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DataConfig:
    history_calendar_days: int = 900        # FDR 조회 범위 (달력일)
    min_trading_days: int = 260             # 이보다 적으면 확인불가
    min_history_span_days: int = 380        # 상장 이력 기간 (달력일)
    ma_trend_lookback: int = 20             # SMA200 "상승 중" 판정 lookback (거래일)


# ---------------------------------------------------------------------------
# TREND TEMPLATE (기존 8개 조건. 1~7 유지, 8번은 RS v2)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrendConfig:
    sma_fast: int = 50
    sma_mid: int = 150
    sma_slow: int = 200
    low_52w_mult: float = 1.30              # 조건6: Close >= 52w_low * 1.30
    high_52w_mult: float = 0.75             # 조건7: Close >= 52w_high * 0.75 (25% 이내)
    week52_window: int = 252                # 52주 = 252 거래일 근사
    # 52주 고점 근접도 품질 등급 (close / high_52w)
    high_proximity_min: float = 0.75        # 이 미만이면 FAIL (= 조건7 과 동일선)
    high_proximity_leader: float = 0.85
    high_proximity_super: float = 0.90


# ---------------------------------------------------------------------------
# RS v2
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RsConfig:
    # 거래일 기준 초과수익 기간
    period_short: int = 21
    period_mid: int = 63
    period_long: int = 126
    period_year: int = 252
    # RS_SCORE 가중치 (percentile 들의 가중합, 합 = 1.0)
    weight_short: float = 0.10
    weight_mid: float = 0.40
    weight_long: float = 0.30
    weight_year: float = 0.20
    rs_min: float = 80.0                    # TREND 통과용 RS_SCORE 하한
    rs_leader: float = 90.0                 # 강한 리더 후보
    accel_lookback: int = 20               # rs_change_20d = 오늘 - 20거래일 전 RS_SCORE
    rs_line_high_lookback: int = 126        # RS_LINE 신고가 판정 창


# ---------------------------------------------------------------------------
# 스윙 / 수축(contraction) 탐지
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SwingConfig:
    fractal_left: int = 3                   # 스윙 좌측 비교 봉 수
    fractal_right: int = 3                  # 스윙 우측 확인 봉 수 (이만큼은 아직 스윙 불가)
    base_lookback: int = 60                # 수축을 찾을 base 구간 길이 (거래일)
    max_legs: int = 4                      # 최근 몇 개의 pullback 다리까지 볼지
    min_contraction_count: int = 2         # 순차 감소 다리 최소 개수
    shrink_tolerance: float = 1.15         # leg[k] <= leg[k-1] * tolerance 면 "감소로 인정"


# ---------------------------------------------------------------------------
# 피벗 (base 내부 저항선)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PivotConfig:
    base_lookback: int = 60                # 피벗 후보 탐색 구간 (거래일)
    min_bars_ago: int = 1                  # 최소 이만큼 전 데이터까지만 사용 (당일 자기참조 방지)
    prefer_swing: bool = True              # 확정 스윙 고점을 우선 사용, 없으면 rolling-max
    swing_near_top_ratio: float = 0.97     # 스윙 고점이 base 최고가의 이 비율 이상이어야 채택


# ---------------------------------------------------------------------------
# SETUP 엔진
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SetupConfig:
    min_base_length: int = 20              # base_length >= 20 거래일
    max_base_length: int = 120             # base_length 상한 (지나치게 긴 횡보 방지 / 계산 캡)
    atr_fast: int = 20
    atr_slow: int = 60
    atr_contraction_max: float = 0.75     # ATR20 / ATR60 <= 0.75
    vol_dryup_fast: int = 10
    vol_dryup_mid: int = 20
    vol_dryup_slow: int = 50
    volume_dryup_max: float = 0.70        # AvgVol10 / AvgVol50 <= 0.70
    range10_window: int = 10
    range10_max: float = 10.0             # (10일 고가/10일 저가 - 1) * 100 <= 10%
    range10_hard_filter: bool = False     # True 면 range10 을 SETUP_READY hard 조건에 포함
    # setup_quality_score (0~100) 구성 가중치 (합계로 정규화)
    quality_weights: dict[str, float] = field(default_factory=lambda: {
        "rs": 0.20,
        "rs_accel": 0.08,
        "rs_line_high": 0.07,
        "high_proximity": 0.15,
        "atr_contraction": 0.15,
        "vol_dryup": 0.12,
        "contraction_count": 0.10,
        "range_tightness": 0.06,
        "pivot_proximity": 0.07,
    })


# ---------------------------------------------------------------------------
# ENTRY 상태 머신
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EntryConfig:
    # 피벗 거리(%) 구간
    setup_below_pct: float = -5.0         # pivot_distance < -5  → SETUP
    watch_min_pct: float = -5.0           # -5 <= d < -2         → WATCH
    ready_min_pct: float = -2.0           # -2 <= d < 0          → READY
    go_max_pct: float = 3.0               # 0 <= d <= 3          → breakout zone
    late_max_pct: float = 5.0             # 3 < d <= 5           → LATE ( > 5 → EXTENDED )
    # Confirmed Breakout
    breakout_volume_min: float = 1.40     # today_volume / AvgVol50
    breakout_clv_min: float = 0.70        # (close - low) / (high - low)
    # Pullback Entry
    pullback_lookback: int = 10           # 최근 N 거래일 내 confirmed breakout
    pullback_distance_pct: float = 1.5    # pivot / EMA10 / EMA20 중 하나에 1.5% 이내
    pullback_volume_ratio: float = 0.80   # 조정 시 avg_vol / AvgVol20
    pullback_clv_min: float = 0.60
    pullback_ema_fast: int = 10
    pullback_ema_slow: int = 20
    pullback_rs_min: float = 80.0         # rs_score >= 80  또는
    pullback_rs_change_floor: float = -15.0  # rs_change_20d 가 이보다 나쁘지 않을 것


# ---------------------------------------------------------------------------
# EXIT 엔진
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExitConfig:
    # FAST_FAIL
    fast_fail_window: int = 5             # 돌파 후 이 거래일 이내
    fast_fail_min_days: int = 3
    fast_fail_volume_mult: float = 1.0   # Close < Pivot AND Volume > AvgVol50 * mult
    fast_fail_consec_below: int = 2      # 2거래일 연속 Close < Pivot
    # Structural stop
    max_initial_risk_pct: float = 7.0    # (entry - stop) / entry * 100 이 넘으면 위험 경고
    stop_buffer_pct: float = 1.0         # 스윙 저점 아래 % 버퍼
    stop_buffer_atr_mult: float = 0.5    # 또는 ATR20 * mult (둘 중 큰 값)
    # Trend exit
    trend_exit_ema_fast: int = 10
    trend_exit_ema_slow: int = 20
    trend_break_sma: int = 50
    trend_break_volume_mult: float = 1.5  # SMA50 을 이 거래량 배수 이상으로 이탈
    confirm_days: int = 1                 # 1 또는 2 (연속 확인)
    # Time stop
    time_stop_days: int = 10
    time_stop_mfe_min: float = 3.0        # 진입 후 MFE(%) 가 이 미만이면
    time_stop_rs_change_max: float = 0.0  # AND rs_change_20d <= 0 (RS 약화)
    # Profit / climax alert
    profit_runup_days: int = 15
    profit_runup_pct: float = 25.0       # N거래일 내 +25% 이상 급등
    profit_ext_from_ema10_pct: float = 20.0  # EMA10 대비 이격 %
    profit_climax_volume_mult: float = 2.0   # 당일 거래량 / AvgVol50
    profit_upper_wick_ratio: float = 0.5     # 윗꼬리 / 당일 전체 range
    profit_gap_up_pct: float = 3.0


# ---------------------------------------------------------------------------
# MARKET REGIME
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RegimeConfig:
    sma_fast: int = 50
    sma_slow: int = 200
    sma_fast_weak_lookback: int = 20      # SMA50 이 20거래일 전보다 낮으면 "약화"
    recovery_lookback: int = 10           # RED 이후 이 거래일 이내면 RECOVERY 후보
    breadth_sma: int = 50
    breadth_strong: float = 0.60
    breadth_normal: float = 0.45
    breadth_weak: float = 0.30
    # 국면별 권장 신규진입 exposure (0~1)
    exposure: dict[str, float] = field(default_factory=lambda: {
        "GREEN": 1.0,
        "YELLOW": 0.6,
        "RED": 0.2,
        "RECOVERY": 0.5,
    })


# ---------------------------------------------------------------------------
# 유니버스
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UniverseConfig:
    kr_mode: str = "liquidity"            # "liquidity" | "legacy_market_cap"
    kr_liquidity_top_n: int = 600         # 20거래일 평균 거래대금 상위 N (최종 유니버스)
    kr_liquidity_candidate_n: int = 700   # 조회할 후보 pool (단일일 거래대금/시총 상위)
    kr_market_cap_top_n: int = 200        # legacy 모드: 시총 상위 N
    kr_min_avg_trading_value_krw: float = 5e8  # 극단 저유동성 floor (20일 평균 거래대금, 원)
    exclude_preferred: bool = True
    exclude_spac: bool = True
    us_keep_sp500: bool = True            # 미국은 S&P500 구성종목 유지


# ---------------------------------------------------------------------------
# 최상위
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SepaConfig:
    data: DataConfig = field(default_factory=DataConfig)
    trend: TrendConfig = field(default_factory=TrendConfig)
    rs: RsConfig = field(default_factory=RsConfig)
    swing: SwingConfig = field(default_factory=SwingConfig)
    pivot: PivotConfig = field(default_factory=PivotConfig)
    setup: SetupConfig = field(default_factory=SetupConfig)
    entry: EntryConfig = field(default_factory=EntryConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)

    def with_overrides(self, dotted: dict[str, Any]) -> "SepaConfig":
        """{"rs.rs_min": 75} 형태의 점 표기 오버라이드를 적용한 새 config 를 반환."""
        groups: dict[str, dict[str, Any]] = {}
        for key, value in dotted.items():
            if "." not in key:
                raise ValueError(f"오버라이드 키는 '그룹.필드' 형태여야 함: {key!r}")
            group, sub = key.split(".", 1)
            groups.setdefault(group, {})[sub] = value

        kwargs: dict[str, Any] = {}
        valid_groups = {f.name for f in fields(self)}
        for group, subs in groups.items():
            if group not in valid_groups:
                raise ValueError(f"알 수 없는 config 그룹: {group!r}")
            current = getattr(self, group)
            valid_subs = {f.name for f in fields(current)}
            bad = set(subs) - valid_subs
            if bad:
                raise ValueError(f"config 그룹 {group!r} 에 없는 필드: {sorted(bad)}")
            kwargs[group] = replace(current, **subs)
        return replace(self, **kwargs)


def load_config(overrides: dict[str, Any] | None = None) -> SepaConfig:
    """
    기본 config 에 (있다면) 파일 → 환경변수 → 인자 순서로 오버라이드를 병합한다.
    잘못된 오버라이드는 조용히 무시하지 않고 예외를 던진다.
    """
    cfg = SepaConfig()
    merged: dict[str, Any] = {}

    if _OVERRIDES_FILE.exists():
        try:
            merged.update(json.loads(_OVERRIDES_FILE.read_text(encoding="utf-8")))
            log.info("config 오버라이드 파일 적용: %s", _OVERRIDES_FILE.name)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"{_OVERRIDES_FILE} 파싱 실패: {exc}") from exc

    env_json = os.environ.get("SEPA_CONFIG_JSON")
    if env_json:
        merged.update(json.loads(env_json))
        log.info("config 오버라이드 환경변수(SEPA_CONFIG_JSON) 적용")

    if overrides:
        merged.update(overrides)

    return cfg.with_overrides(merged) if merged else cfg


CONFIG: SepaConfig = load_config()
