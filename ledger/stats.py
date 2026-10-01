"""그룹·호라이즌별 통계. 표본 독립성을 위해 종목당 최초 신호만 세고, 신뢰구간은 신호일 단위로 묶어 부트스트랩한다."""
from __future__ import annotations

import datetime as dt
import random

import numpy as np
import statistics
from collections import defaultdict

from . import config

LABEL_KO = {"none": "완료 표본 없음", "thin": "표본 부족", "concentrated": "독립 구간 부족", "ok": "해석 가능"}


def independent_windows(dates: list[str], h: int) -> int:
    """신호일 중 서로 h거래일(주말만 제외한 근사) 이상 떨어진 날을 그리디로 센다. 연속된 신호일은 보유 기간이 겹쳐 독립 표본이 아니다."""
    count, last = 0, None
    for d in sorted(set(dates)):
        if last is None or int(np.busday_count(last, d)) >= h:
            count, last = count + 1, d
    return count


def dedupe_first(signals: list[dict]) -> list[dict]:
    """(전략·시장·그룹·종목)당 가장 이른 신호 1개. 대조군은 (날짜·종목)당 1개."""
    best: dict[tuple, dict] = {}
    for s in signals:
        key = (s["strategy"], s["market"], s["group"], s["date"], s["symbol"]) if s["group"] == config.CONTROL \
            else (s["strategy"], s["market"], s["group"], s["symbol"])
        if key not in best or s["date"] < best[key]["date"]:
            best[key] = s
    return list(best.values())


def cluster_ci(by_date: dict[str, list[float]], seed: str) -> tuple[float, float] | None:
    """평균의 95% 신뢰구간(신호일 단위 부트스트랩). 신호일이 2개 미만이면 구하지 않는다."""
    dates = sorted(by_date)
    if len(dates) < 2:
        return None
    rng = random.Random(f"{config.BOOT_SEED}:{seed}")
    means = []
    for _ in range(config.BOOT_N):
        vals: list[float] = []
        for d in (rng.choice(dates) for _ in dates):
            vals.extend(by_date[d])
        means.append(sum(vals) / len(vals))
    means.sort()
    return means[int(0.025 * config.BOOT_N)], means[int(0.975 * config.BOOT_N) - 1]


def _status(n: int, windows: int) -> str:
    return "none" if n == 0 else "thin" if n < config.MIN_N else "concentrated" if windows < config.MIN_WINDOWS else "ok"


def _matures(first_date: str | None, h: int) -> str | None:
    """가장 이른 신호의 호라이즌 도래 추정일(주말만 건너뛴 근사)."""
    if not first_date:
        return None
    d, left = dt.date.fromisoformat(first_date), h
    while left:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d.isoformat()


def summarize_group(samples: list[dict], outcomes: dict, h: int, control_by_date: dict[str, float], seed: str) -> dict:
    done, pending, unavailable = [], 0, 0
    reasons: dict[str, int] = defaultdict(int)
    for s in samples:
        o = outcomes.get(s["id"], {}).get(str(h)) or outcomes.get(s["id"], {}).get(h) or {}
        if o.get("status") == "complete":
            done.append((s, o))
        elif o.get("status") == "pending" or not o:
            pending += 1
        else:
            unavailable += 1
            reasons[o.get("reason") or "unknown"] += 1
    n_dates = len({s["date"] for s, _ in done})
    windows = independent_windows([s["date"] for s, _ in done], h)
    res = {"n": len(done), "pending": pending, "unavailable": unavailable, "unavailableReasons": dict(reasons),
           "distinctDates": n_dates, "independentWindows": windows, "status": _status(len(done), windows),
           "firstSignal": min((s["date"] for s in samples), default=None)}
    res["firstMature"] = _matures(res["firstSignal"], h)
    if not done:
        return res
    rets = [o["returnPct"] for _, o in done]
    excess = [o["excessPct"] for _, o in done if o.get("excessPct") is not None]
    res.update(meanReturnPct=round(statistics.fmean(rets), 2), medianReturnPct=round(statistics.median(rets), 2),
               meanMaxDownPct=round(statistics.fmean(o["maxDownPct"] for _, o in done if o.get("maxDownPct") is not None), 2)
               if any(o.get("maxDownPct") is not None for _, o in done) else None)
    if excess:
        res.update(meanExcessPct=round(statistics.fmean(excess), 2), medianExcessPct=round(statistics.median(excess), 2),
                   winRate=round(sum(1 for x in excess if x > 0) / len(excess), 3))
        by_date: dict[str, list[float]] = defaultdict(list)
        for s, o in done:
            if o.get("excessPct") is not None:
                by_date[s["date"]].append(o["excessPct"])
        ci = cluster_ci(by_date, seed + ":excess")
        res["excessCI95"] = [round(ci[0], 2), round(ci[1], 2)] if ci else None
    matched = [(s["date"], o["returnPct"] - control_by_date[s["date"]]) for s, o in done if s["date"] in control_by_date]
    if matched:
        by_date2: dict[str, list[float]] = defaultdict(list)
        for d, v in matched:
            by_date2[d].append(v)
        res.update(vsControlPct=round(statistics.fmean(v for _, v in matched), 2), vsControlN=len(matched),
                   vsControlWinRate=round(sum(1 for _, v in matched if v > 0) / len(matched), 3))
        ci2 = cluster_ci(by_date2, seed + ":control")
        res["vsControlCI95"] = [round(ci2[0], 2), round(ci2[1], 2)] if ci2 else None
    return res


def control_means(signals: list[dict], outcomes: dict, h: int) -> dict[str, float]:
    """신호일별 대조군 평균 수익률(%)."""
    by_date: dict[str, list[float]] = defaultdict(list)
    for s in dedupe_first([s for s in signals if s["group"] == config.CONTROL]):
        o = outcomes.get(s["id"], {}).get(str(h)) or {}
        if o.get("status") == "complete":
            by_date[s["date"]].append(o["returnPct"])
    return {d: statistics.fmean(v) for d, v in by_date.items()}


def build(signals: list[dict], outcomes: dict) -> dict:
    """{전략: {시장: {그룹: {호라이즌: 요약}}}}. 요약에는 대조군 자체의 성과도 함께 들어간다."""
    out: dict = {}
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for s in dedupe_first(signals):
        by_key[(s["strategy"], s["market"])].append(s)
    for (strategy, market), rows in sorted(by_key.items()):
        groups: dict[str, list[dict]] = defaultdict(list)
        for s in rows:
            groups[s["group"]].append(s)
        res: dict = {}
        for h in config.HORIZONS:
            ctrl = control_means(rows, outcomes, h)
            for group, samples in sorted(groups.items()):
                res.setdefault(group, {})[str(h)] = summarize_group(
                    samples, outcomes, h, {} if group == config.CONTROL else ctrl, f"{strategy}:{market}:{group}:{h}")
        out.setdefault(strategy, {})[market] = res
    return out
