"""성과 요약·도태 단계. 순수 함수."""
from __future__ import annotations

import numpy as np

from . import config


def max_drawdown(values: list[float]) -> float:
    peak, worst = None, 0.0
    for v in values:
        peak = v if peak is None or v > peak else peak
        if peak:
            worst = min(worst, (v / peak - 1) * 100)
    return round(worst, 2)


def boot_mean_ci(xs: list[float]):
    """월 초과수익의 평균과 95% 구간(단순 부트스트랩, 시드 고정). 표본이 2 미만이면 None."""
    if len(xs) < 2:
        return None
    a = np.array(xs, dtype=float)
    rng = np.random.default_rng(config.BOOT_SEED)
    means = rng.choice(a, size=(config.BOOT_N, len(a)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return round(float(a.mean()), 3), round(float(lo), 3), round(float(hi), 3)


def excess(periods: list[dict], ref: dict[str, float]) -> list[float]:
    return [p["returnPct"] - ref[p["start"][:7]] for p in periods if p["start"][:7] in ref]


def review_stage(prev: str, mdd: float, n_periods: int, vs_control: list[float]) -> tuple[str, str | None]:
    """도태 단계(QUANT.md §9). 소멸은 되돌리지 않는다. 낙폭 규칙은 표본과 무관, 성과 규칙은 12회 이상 리밸런싱 뒤에만."""
    if prev == "RETIRED":
        return "RETIRED", None
    if mdd <= config.MDD_RETIRE:
        return "RETIRED", f"최대 낙폭 {mdd}% (기준 {config.MDD_RETIRE}% 이하)"
    ci = boot_mean_ci(vs_control) if n_periods >= config.RETIRE_MIN_PERIODS else None
    if prev == "PROBATION" and ci and ci[2] < 0:
        return "RETIRED", f"리밸런싱 {n_periods}회, 대조군 대비 월 초과수익 95% 구간 상한 {ci[2]}%p < 0"
    if prev == "ACTIVE":
        if mdd <= config.MDD_PROBATION:
            return "PROBATION", f"최대 낙폭 {mdd}% (기준 {config.MDD_PROBATION}% 이하)"
        if n_periods >= config.MIN_PERIODS and vs_control and sum(vs_control) / len(vs_control) < 0:
            return "PROBATION", f"리밸런싱 {n_periods}회, 대조군 대비 월 평균 초과수익 {sum(vs_control) / len(vs_control):.2f}%p < 0"
    if prev == "PROBATION" and mdd > config.MDD_PROBATION and n_periods >= config.MIN_PERIODS + config.MIN_PERIODS \
            and vs_control and sum(vs_control) / len(vs_control) > 0:
        return "ACTIVE", f"리밸런싱 {n_periods}회, 대조군 대비 월 평균 초과수익 {sum(vs_control) / len(vs_control):.2f}%p > 0, 낙폭 {mdd}%"
    return prev, None
