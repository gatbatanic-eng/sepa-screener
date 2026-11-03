"""대규모 탐색의 한국 상위 후보를 포트폴리오 시뮬레이션(ops.simulate)으로 재검증. 설계는 2026-11-03에 결과를 보기 전에 고정했다.
python -m leader_backtest.search_portfolio  (입력: research/leader_backtest/search_2017_2026/search.json 의 한국 상위 20개)
대상 선정 규칙(사후 선택 방지): 상위 20개 중 청산이 SMA50X(손절 -8% + 50일선 이탈)인 후보 '전부'. ops.simulate 의 E1 과 같은 청산이라 그대로 적용된다
(보유일·트레일링 청산 후보와 미국 후보는 시뮬레이터가 지원하지 않아 제외하고, 제외 사실을 보고한다).
운용: ops 와 같다 - 계좌의 12.5%, 동시 최대 8종목, RS 높은 순, 편도 비용 25bp, 다음 거래일 종가 진입. 시장 필터는 후보 자신의 regime(지수 100/150/200일선)을 신호에 포함한다.
평가: 훈련 2017~2020, 검증 2021~2023, 시험 2024~ 로 나눠 구간별 누적·낙폭을 코스피 매수후보유와 비교. 신호는 청산 확정 여부와 무관하게 지표가 계산되는 모든 행(끝 80일 포함).
한계: 생존편향, 종가 체결, 현금 이자 0, 조합이 사후 상위라 선택 편향이 남는다(시험 구간도 이전에 본 적 있음)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

import numpy as np
import pandas as pd

from .main import COST, OUT_DIR, ROOT
from .ops import metrics, simulate
from .search import PERIODS, build_data

log = logging.getLogger("leader_backtest.search_portfolio")
SEARCH_JSON = ROOT / "research" / "leader_backtest" / "search_2017_2026" / "search.json"


def signal_matrix(data, mask: np.ndarray) -> pd.DataFrame:
    sig = np.zeros((data.D, len(data.codes)), bool)
    sig[data.didx[mask], data.cidx[mask]] = True
    return pd.DataFrame(sig, index=data.dates, columns=data.codes)


def run_config(data, cfg: dict, cost: float) -> dict:
    sig = signal_matrix(data, data.mask(cfg, strict=False))
    close, sma50, rs = data.mats["close"], data.mats["sma50"], data.mats["rs"]
    allow = pd.Series(True, index=data.dates)                 # 시장 필터는 mask 안(regime)에서 이미 적용
    s = simulate(close, sma50, sig, rs, allow, "E1", cost)
    eq, trades = s["equity"], s["trades"]
    bench = data.bench[data.bench.index >= eq.index[0]]
    out = {"signals": int(sig.values.sum())}
    for per, (lo, hi) in PERIODS.items():
        sl = eq[(eq.index >= pd.Timestamp(lo)) & (eq.index <= pd.Timestamp(hi))]
        if len(sl) < 20:
            continue
        tr = [t for t in trades if sl.index[0] <= t["exit"] <= sl.index[-1]]
        out[per] = metrics(sl, tr, bench[(bench.index >= sl.index[0]) & (bench.index <= sl.index[-1])])
    out["full"] = metrics(eq, trades, bench)
    return out


def to_markdown(res: dict) -> str:
    o = ["# 대규모 탐색 한국 상위 후보 포트폴리오 재검증", "", f"> 실행 {res['ranAt']} · 설계 2026-11-03 고정 · 참고용", "", "```", __doc__.strip(), "```", "",
         f"제외한 상위 20개 후보: SMA50X 아님 {res['excluded']}개", "",
         "| 순위 | 조건 | 구간 | 누적% | 연환산% | MDD% | 거래 | 승률% | 기대값% | 코스피 누적%/MDD% |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["results"]:
        c = ", ".join(f"{k}={v}" for k, v in r["config"].items() if v not in (0, -9, 9, 999, 101))
        for per in ("train", "val", "test", "full"):
            m = r.get(per)
            if m:
                o.append(f"| {r['rank']} | {c if per == 'train' else ''} | {per} | {m['totalPct']} | {m['cagrPct']} | {m['mddPct']} | {m['trades']} | {m['winPct']} | {m['expectancyPct']} | {m['benchTotalPct']}/{m['benchMddPct']} |")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    src = json.loads(SEARCH_JSON.read_text(encoding="utf-8"))
    top = src["markets"]["kr"]["top"]
    picked = [t for t in top if t["config"]["exit"] == "SMA50X"]
    kr = [r for r in json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    data = build_data("kr", kr, "^KS11", os.environ.get("LB_FETCH_START") or "2015-06-01")
    results = []
    for t in picked:
        r = run_config(data, t["config"], COST["kr"] / 2)
        results.append({"rank": t["rank"], "config": t["config"], "searchScore": t["score"], **r})
        log.info("순위 %s 완료", t["rank"])
    res = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "excluded": len(top) - len(picked), "results": results}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "search_portfolio.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT_DIR / "search_portfolio.md").write_text(to_markdown(res), encoding="utf-8")
    print(to_markdown(res))


if __name__ == "__main__":
    main()
