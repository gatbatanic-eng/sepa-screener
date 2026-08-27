"""Walk-forward / 파라미터 민감도 테스트 (스펙 29조).

과최적화를 피하려는 목적이다 (개발 원칙 2.2). 이 모듈 자체는 최적
파라미터를 "찾아주지" 않는다 — 특정 값 하나에서만 성과가 급격히
좋아지는지 관찰하고 경고하는 도구다.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

from src.backtest.metrics import BacktestMetrics
from src.config import StrategyConfig

# 특정 값이 나머지 대비 이 정도(z-score) 이상 튀면 과최적화 가능성으로
# 본다. 2.0은 정규분포 기준 상위 약 2.3%에 해당하는 표준적인 이상치 판단
# 기준이며(임의의 전략 파라미터가 아니라 통계적 관례), 필요하면 호출 시
# z_threshold로 조정할 수 있다.
DEFAULT_Z_THRESHOLD = 2.0


def override_config(config: StrategyConfig, dotted_path: str, value: Any) -> StrategyConfig:
    """config의 중첩 필드 하나를 바꾼 새 StrategyConfig를 반환한다 (원본 불변).

    예: override_config(config, "range_mr.box.period_days", 80)
    """
    data = config.model_dump()
    keys = dotted_path.split(".")
    node = data
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    return StrategyConfig.model_validate(data)


def run_parameter_sensitivity(
    run_fn: Callable[[StrategyConfig], BacktestMetrics],
    base_config: StrategyConfig,
    dotted_path: str,
    values: list,
    metric_name: str = "avg_return",
) -> pd.DataFrame:
    """dotted_path 파라미터를 values로 하나씩 바꿔가며 run_fn(config)을
    호출하고, 결과 지표를 한 줄씩 모은 DataFrame을 반환한다.

    run_fn은 (데이터 로딩 → evaluate_range_mr/v_rebound → generate_trades →
    compute_metrics)를 감싼 호출자 쪽 함수다 — 이 모듈은 데이터를 모르므로
    그 부분은 호출자가 클로저로 넘긴다.
    """
    rows = []
    for value in values:
        cfg = override_config(base_config, dotted_path, value)
        metrics = run_fn(cfg)
        rows.append({"value": value, "total_trades": metrics.total_trades, metric_name: getattr(metrics, metric_name)})
    return pd.DataFrame(rows)


def detect_overfitting_risk(
    sensitivity_df: pd.DataFrame, metric_col: str, z_threshold: float = DEFAULT_Z_THRESHOLD
) -> dict:
    """가장 좋은 값이 나머지 값들의 평균 대비 z_threshold 표준편차 이상
    튀어나와 있으면 과최적화 위험으로 표시한다.
    """
    values = sensitivity_df[metric_col].dropna().to_numpy()
    if len(values) < 3:
        return {"risk": False, "reason": "표본이 3개 미만이라 이상치 판단 불가"}

    best_idx = int(np.argmax(values))
    best = float(values[best_idx])
    rest = np.delete(values, best_idx)
    mean_rest = float(np.mean(rest))
    std_rest = float(np.std(rest, ddof=1)) if len(rest) > 1 else 0.0

    if std_rest < 1e-9:  # 부동소수점 오차로 "완전히 동일"이 정확히 0.0으로 안 나올 수 있다
        return {"risk": False, "z_score": None, "best_value": sensitivity_df.iloc[best_idx]["value"]}

    z = (best - mean_rest) / std_rest
    return {
        "risk": bool(z > z_threshold),
        "z_score": float(z),
        "best_value": sensitivity_df.iloc[best_idx]["value"],
        "best_metric": best,
    }


def train_test_split_by_date(index: pd.DatetimeIndex, split_date) -> tuple[np.ndarray, np.ndarray]:
    """TRAIN(<=split_date) / OUT-OF-SAMPLE(>split_date) 구간을 나누는 boolean mask."""
    split_ts = pd.Timestamp(split_date)
    train_mask = index <= split_ts
    test_mask = ~train_mask
    return train_mask, test_mask
