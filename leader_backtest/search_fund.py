"""재무 신호 대규모 탐색(과최적화 통제 포함). 설계는 2026-11-03에 결과를 보기 전에 고정했다. python -m leader_backtest.search_fund
search.py 와 같은 틀(훈련 2017~2020 / 검증 2021~2023 / 시험 2024~, 점수 = min(훈련 t, 검증 t), 섞은 데이터 귀무, 현실점검 p, Bonferroni)이며 진입 조건에 재무를 더한다.
재무 특징은 fund_pit.py 의 공시 시점 기준(접수일 다음 거래일부터, 처음 공시된 값, 200일 넘게 공시가 없으면 결측). 임계값을 켠 조건에서 결측은 탈락이다.
탐색 공간(무작위 10만 개): 재무 조건 1개 이상 필수 + 가격 조건(RS, 추세템플릿, 시장 필터, 거래량, 돌파, 52주 고점 근접, ATR) + 청산 20종(search.py 와 동일).
재무: 매출증가율 {0,10,20,30,50}%+, 영업이익증가율 {0,20,50}%+(흑자전환 포함), 영업이익률 {0,5,10,15}%+, 영업이익률 개선 {0,2}%p+, 매출 증가율 가속, 흑자전환, 순이익 흑자, 부채비율 {100,200,300}% 이하, 공시 후 경과 {20,60,120}일 이내.
한계: 현재 상장 종목(생존편향), 일부 종목 계정 결측, 시험 구간은 이전에 본 적 있음, 신호 단위 평가(포트폴리오 제약 미반영)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

import numpy as np
import pandas as pd

from . import search as S
from .fund_pit import FUND_COLS, attach, load_quarters, quarter_features
from .main import OUT_DIR, ROOT

log = logging.getLogger("leader_backtest.search_fund")
FUND_KEYS = ("revYoY", "opYoY", "opMargin", "dMargin", "accel", "turn", "netPos", "de", "recent")
PRICE_KEYS = ("rs", "atrMin", "highMin", "vol")
OFF = {"revYoY": -9, "opYoY": -9, "opMargin": -9, "dMargin": -9, "accel": 0, "turn": 0, "netPos": 0, "de": 999, "recent": 999}


def sample_fund_configs(n: int, seed: int = S.SEED + 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    pick = lambda xs: xs[rng.integers(len(xs))]
    out = []
    while len(out) < n:
        c = {"rs": pick([0, 50, 70, 80, 90]), "trend": pick([0, 1]), "regime": pick([0, 150, 200]), "vol": pick([0, 1.0, 1.5]), "breakout": pick([0, 1]),
             "highMin": pick([-9, -0.30, -0.20, -0.10]), "atrMin": pick([0, 0.02, 0.03]), "exit": pick(S.EXITS),
             "revYoY": pick([-9, 0, 10, 20, 30, 50]), "opYoY": pick([-9, 0, 20, 50]), "opMargin": pick([-9, 0, 5, 10, 15]), "dMargin": pick([-9, 0, 2]),
             "accel": pick([0, 1]), "turn": pick([0, 0, 1]), "netPos": pick([0, 1]), "de": pick([999, 100, 200, 300]), "recent": pick([999, 20, 60, 120])}
        if any(c[k] != OFF[k] for k in FUND_KEYS):
            out.append(c)
    return out


class FundData(S.Data):
    def __init__(self, panel, regimes, extra=()):
        super().__init__(panel, regimes, extra=extra)
        e = self.extra
        F = {}
        for v in (0, 10, 20, 30, 50): F[("revYoY", v)] = e["revYoY"] >= v
        for v in (0, 20, 50): F[("opYoY", v)] = (e["opYoY"] >= v) | (e["turn"] == 1)          # 흑자전환은 증가율 조건을 만족한 것으로 본다
        for v in (0, 5, 10, 15): F[("opMargin", v)] = e["opMargin"] >= v
        for v in (0, 2): F[("dMargin", v)] = e["dMargin"] >= v
        F[("accel", 1)] = e["accel"] > 0
        F[("turn", 1)] = e["turn"] == 1
        F[("netPos", 1)] = e["netPos"] == 1
        for v in (100, 200, 300): F[("de", v)] = e["de"] <= v
        for v in (20, 60, 120): F[("recent", v)] = e["daysSince"] <= v
        self.fmasks = F

    def mask(self, c: dict, strict: bool = True) -> np.ndarray:
        m = (self.valid[c["exit"]] if strict else self.ok) & self.regime[c["regime"]]
        if c["trend"]: m = m & self.trend
        for key in PRICE_KEYS:
            mk = self.masks.get((key, c[key]))
            if mk is not None: m = m & mk
        if c["breakout"]: m = m & self.masks[("breakout", 1)]
        for key in FUND_KEYS:
            mk = self.fmasks.get((key, c[key]))
            if mk is not None: m = m & mk
        return m


def join_key(code: pd.Series, market: str) -> pd.Series:
    """가격 쪽 키(한국 '009450.KS', 미국 'BRK-B')와 재무 쪽 키(한국 '009450', 미국 'BRK.B')를 같은 형태로 맞춘다."""
    c = code.astype(str)
    if market == "kr":
        return c.str.replace(r"\.(KS|KQ)$", "", regex=True).str.zfill(6)
    return c.str.replace(".", "-", regex=False).str.upper()


def fund_hook(market: str):
    feats = quarter_features(load_quarters(market))
    feats["code"] = join_key(feats["code"], market)

    def hook(panel: pd.DataFrame) -> pd.DataFrame:
        p = panel.copy()
        p["_orig"] = p["code"]
        p["code"] = join_key(p["_orig"], market)
        out = attach(p, feats)
        out["code"] = out["_orig"]
        n = float(out["revYoY"].notna().mean())
        log.info("%s 재무 병합 후 매출증가율 비결측 비율 %.3f", market, n)
        if n < 0.05:
            raise RuntimeError("재무 특징이 거의 붙지 않았습니다(종목 키 불일치 의심): %.3f" % n)
        return out.drop(columns="_orig")
    return hook


def build(market, universe, sym, start_fetch):
    return S.build_data(market, universe, sym, start_fetch, panel_hook=fund_hook(market), data_cls=FundData, extra=(*FUND_COLS, "daysSince"))


def to_markdown(res: dict) -> str:
    o = ["# 재무 신호 대규모 탐색 결과 (과최적화 통제)", "", f"> 실행 {res['ranAt']} · 설계 2026-11-03 고정 · 생존편향 · 신호 단위 평가 · 참고용", "", "```", __doc__.strip(), "```", ""]
    for m in res["markets"].values():
        o += [f"## {m['market'].upper()} — 조합 {m['configs']:,}개(유효 {m['validConfigs']:,}), 행 {m['rows']:,}, 거래일 {m['dates']}", "",
              f"- 귀무 복제 {m['nullReplicates']}회 최고 점수: {m['nullMaxScores']} / 귀무 점수 분위수: {m['nullScoreQuantiles']}",
              f"- 실제 점수 분위수: {m['realScoreQuantiles']}",
              f"- 현실점검 p(귀무 최고 점수가 실제 최고 이상인 비율): {m['realityCheckP']}",
              f"- 유효 조합 전체의 구간별 평균 초과수익 %p(신호 단위): {m['avgExcessPctByPeriodAllValid']}",
              f"- 상위 20개 중 시험 구간 초과수익이 양수인 비율: {m['positiveTestShareAmongTop20']}", "",
              "| 순위 | 점수(min t) | Bonferroni p(유효 N) | 훈련 t/초과% | 검증 t/초과% | 시험 t/초과%/신호 | 조건 |", "|---|---|---|---|---|---|---|"]
        for t in m["top"]:
            P, c = t["periods"], t["config"]
            fm = lambda k: f"{P[k]['t']:.2f}/{P[k]['excessPct']:.2f}" if P[k]["t"] is not None else "n/a"
            ts = f"{P['test']['t']:.2f}/{P['test']['excessPct']:.2f}/{P['test']['signals']}" if P["test"]["t"] is not None else f"n/a/{P['test']['signals']}"
            cond = ", ".join(f"{k}={v}" for k, v in c.items() if (k in ("exit",)) or (k in OFF and v != OFF[k]) or (k not in OFF and v not in (0, -9, 9, 999, 101)))
            o.append(f"| {t['rank']} | {t['score']} | {round(t['bonferroniPValidN'], 4) if t.get('bonferroniPValidN') is not None else None} | {fm('train')} | {fm('val')} | {ts} | {cond} |")
        o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    start_fetch = os.environ.get("LB_FETCH_START") or "2015-06-01"
    workers = max(1, os.cpu_count() or 2)
    kr = [r for r in json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    us = [r for r in json.loads((ROOT / "docs" / "data" / "latest_us.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    res = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "nConfigs": S.N_CONFIGS, "nullReps": S.N_NULL_REPS, "markets": {}}
    want = (os.environ.get("SF_MARKETS") or "us,kr").split(",")
    for market, uni, sym in [m for m in (("us", us, "^GSPC"), ("kr", kr, "^KS11")) if m[0] in want]:
        data = build(market, uni, sym, start_fetch)
        cov = {k: round(float(np.isfinite(v).mean()), 3) for k, v in data.extra.items()}
        log.info("%s 재무 특징 결측 아닌 비율 %s", market, cov)
        cfgs = sample_fund_configs(S.N_CONFIGS)
        real, nulls = S.run_all(data, cfgs, S.N_NULL_REPS, workers)
        res["markets"][market] = S.summarize(market, data, cfgs, real, nulls) | {"fundCoverage": cov}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "search_fund.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT_DIR / "search_fund.md").write_text(to_markdown(res), encoding="utf-8")
    print(to_markdown(res))


if __name__ == "__main__":
    main()
