"""대규모 규칙 탐색(과최적화 통제 포함). 설계는 2026-11-03에 결과를 보기 전에 고정했다. python -m leader_backtest.search
목적: 10만 개 안팎의 진입·청산 조합 중 '우연이 아닌' 후보가 있는지 가린다. 10만 개 중 최고는 우연만으로도 좋아 보이므로 아래 장치로 통제한다.
1) 기간 분할: 훈련 2017-01-03~2020-12-31, 검증 2021-01-01~2023-12-31(2022 하락장 포함), 시험 2024-01-01~끝. 시험은 후보를 고른 뒤 한 번만 본다
   (단, 2024년 이후는 이전 백테스트에서 이미 봤으므로 완전한 미공개가 아니다).
2) 점수 = min(훈련 t, 검증 t). t = 20거래일 블록 평균으로 낸 '신호 종목 평균 수익 - 같은 날 전 종목 평균 수익(같은 청산)'의 t통계(겹침 보유로 인한 상관 완화).
   유효 조건: 구간마다 신호 250개·서로 다른 날짜 60일 이상.
3) 귀무 분포: 같은 날 종목 사이에서 수익 결과를 무작위로 섞어(신호-수익 연결 제거) 같은 조합을 평가한 최고 점수/전체 분포. 실제 최고 점수가 이를 넘는지 본다.
4) 보정: 단일 조합 귀무 꼬리확률 q에 조합 수 N을 곱한 Bonferroni p(보수적. 조합끼리 상관이 커서 실제 유효 N은 더 작다).
5) 최종 후보는 점수 상위 20개이며 시험 구간 성과를 전부 보고한다(고르지 않는다).
한계: 현재 유니버스(생존편향, 2017년부터라 더 심함), 신호 단위 평가(포트폴리오 제약 미반영 -> 후보는 이후 포트폴리오 시뮬레이션으로 재검증), 종가 체결,
추적·50일선 청산(최대 80거래일)은 끝 80일 신호를 제외한다."""
from __future__ import annotations

import datetime as dt
import json
import logging
import multiprocessing as mp
import os

import numpy as np
import pandas as pd

from .main import COST, OUT_DIR, ROOT, fetch_ohlcv
from .rules import features, forward_returns

log = logging.getLogger("leader_backtest.search")
PERIODS = {"train": ("2017-01-03", "2020-12-31"), "val": ("2021-01-01", "2023-12-31"), "test": ("2024-01-01", "2100-01-01")}
N_CONFIGS = int(os.environ.get("SEARCH_N", "100000"))
N_NULL_REPS = int(os.environ.get("SEARCH_NULL_REPS", "3"))
SEED = 20261103
MIN_SIGNALS, MIN_DATES, BLOCK, CAP = 250, 60, 20, 80
HOLDS, STOPS, TRAILS = (10, 20, 40, 60), (None, 0.06, 0.08, 0.12), (0.10, 0.15, 0.20)
EXITS = [f"H{h}" + (f"_S{int(s * 100)}" if s else "") for h in HOLDS for s in STOPS] + [f"TR{int(t * 100)}" for t in TRAILS] + ["SMA50X"]


def exit_outcomes(c: pd.Series, sma50: pd.Series, cost: float) -> dict[str, np.ndarray]:
    out = {}
    for h in HOLDS:
        for s in STOPS:
            out[f"H{h}" + (f"_S{int(s * 100)}" if s else "")] = forward_returns(c, h, cost, s).to_numpy(float)
    cc, ss, n = c.to_numpy(float), sma50.to_numpy(float), len(c)
    if n < CAP + 3:
        for k in [f"TR{int(t * 100)}" for t in TRAILS] + ["SMA50X"]:
            out[k] = np.full(n, np.nan)
        return out
    w = np.lib.stride_tricks.sliding_window_view(cc[1:], CAP + 1)            # w[t] = c[t+1 .. t+1+CAP]
    sw = np.lib.stride_tricks.sliding_window_view(ss[1:], CAP + 1)
    entry, rest, srest = w[:, :1], w[:, 1:], sw[:, 1:]
    peak = np.maximum.accumulate(rest, axis=1)
    base_hit = rest <= entry * 0.92

    def settle(hit):
        first = hit.argmax(axis=1)
        px = np.where(hit.any(axis=1), rest[np.arange(len(rest)), first], rest[:, -1])
        r = np.full(n, np.nan)
        r[:len(w)] = px / entry[:, 0] - 1 - cost
        return r
    for t in TRAILS:
        out[f"TR{int(t * 100)}"] = settle(base_hit | (rest <= peak * (1 - t)))
    out["SMA50X"] = settle(base_hit | (rest < srest))
    return out


def stock_frame(df: pd.DataFrame, cost: float) -> pd.DataFrame:
    f = features(df)
    c, h, l = df["close"], df["high"], df["low"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    ema = c.ewm(span=20, adjust=False).mean()
    hi = h.rolling(252, min_periods=200).max()
    f["atrPct"] = atr / c
    f["distEma"] = c / ema - 1
    f["distHigh"] = c / hi - 1
    f["ret20"] = c / c.shift(20) - 1
    sma50 = c.rolling(50).mean()
    f["close"], f["sma50"] = c, sma50
    for k, v in exit_outcomes(c, sma50, cost).items():
        f["x_" + k] = v.astype(np.float32)
    return f


def sample_configs(n: int, seed: int = SEED) -> list[dict]:
    rng = np.random.default_rng(seed)
    pick = lambda xs: xs[rng.integers(len(xs))]
    cfgs = []
    for _ in range(n):
        lo_e, hi_e = pick([-9, -0.10, -0.05, -0.02, 0.0]), pick([0.0, 0.02, 0.05, 0.10, 9])
        if lo_e >= hi_e:
            lo_e, hi_e = -9, hi_e
        cfgs.append({"rs": pick([0, 50, 60, 70, 80, 85, 90, 95]), "trend": pick([0, 1]), "atrMin": pick([0, 0.02, 0.03, 0.04]), "atrMax": pick([0.05, 0.08, 9]),
                     "emaLo": lo_e, "emaHi": hi_e, "highMin": pick([-9, -0.30, -0.20, -0.10, -0.05]), "rsiLo": pick([0, 30, 40, 50, 60]), "rsiHi": pick([50, 60, 70, 80, 101]),
                     "vol": pick([0, 1.0, 1.5, 2.0]), "breakout": pick([0, 1]), "ret20Min": pick([-9, -0.10, 0.0, 0.10]), "riskMax": pick([999, 8, 15, 25]),
                     "regime": pick([0, 100, 150, 200]), "exit": pick(EXITS)})
    return cfgs


class Data:
    """종목×날짜 행을 한 배열로 쌓은 것. 조건 하나하나의 불리언 마스크를 미리 만들어 조합은 AND 로만 계산한다."""

    def __init__(self, panel: pd.DataFrame, regimes: dict[int, pd.Series], extra: tuple = ()):
        p = panel.sort_values(["date", "code"]).reset_index(drop=True)
        self.extra = {k: p[k].to_numpy(np.float32) for k in extra}
        self.dates = np.sort(p["date"].unique())
        self.didx = np.searchsorted(self.dates, p["date"].to_numpy()).astype(np.int32)
        self.codes = np.sort(p["code"].unique())
        self.cidx = np.searchsorted(self.codes, p["code"].to_numpy()).astype(np.int32)
        self.mats = {k: p.pivot(index="date", columns="code", values=v).reindex(index=self.dates, columns=self.codes) for k, v in (("close", "close"), ("sma50", "sma50"), ("rs", "rs"))}
        self.bench = None
        self.D = len(self.dates)
        a = lambda k: p[k].to_numpy(np.float32)
        self.ok = p["ok"].to_numpy(bool)
        self.p = {k: a(k) for k in ("rs", "atrPct", "distEma", "distHigh", "rsi", "volRatio", "pivDist", "ret20", "risk")}
        self.trend = p["trend"].to_numpy(bool)
        self.out = {k: p["x_" + k].to_numpy(np.float32) for k in EXITS}
        self.valid = {k: self.ok & np.isfinite(v) for k, v in self.out.items()}
        self.regime = {0: np.ones(len(p), bool)} | {n: s.reindex(self.dates).fillna(False).to_numpy(bool)[self.didx] for n, s in regimes.items()}
        self.period_slice = {k: (np.searchsorted(self.dates, np.datetime64(lo)), np.searchsorted(self.dates, np.datetime64(hi))) for k, (lo, hi) in PERIODS.items()}
        self.base = {k: self._date_mean(self.out[k], self.valid[k]) for k in EXITS}
        self.masks = self._build_masks()

    def _date_mean(self, out, m):
        n = np.bincount(self.didx[m], minlength=self.D)
        s = np.bincount(self.didx[m], weights=out[m].astype(np.float64), minlength=self.D)
        return np.divide(s, n, out=np.full(self.D, np.nan), where=n > 0)

    def _build_masks(self):
        P = self.p
        M = {}
        for v in (50, 60, 70, 80, 85, 90, 95): M[("rs", v)] = P["rs"] >= v
        for v in (0.02, 0.03, 0.04): M[("atrMin", v)] = P["atrPct"] >= v
        for v in (0.05, 0.08): M[("atrMax", v)] = P["atrPct"] <= v
        for v in (-0.10, -0.05, -0.02, 0.0): M[("emaLo", v)] = P["distEma"] >= v
        for v in (0.0, 0.02, 0.05, 0.10): M[("emaHi", v)] = P["distEma"] <= v
        for v in (-0.30, -0.20, -0.10, -0.05): M[("highMin", v)] = P["distHigh"] >= v
        for v in (30, 40, 50, 60): M[("rsiLo", v)] = P["rsi"] >= v
        for v in (50, 60, 70, 80): M[("rsiHi", v)] = P["rsi"] <= v
        for v in (1.0, 1.5, 2.0): M[("vol", v)] = P["volRatio"] >= v
        M[("breakout", 1)] = (P["pivDist"] > 0) & (P["pivDist"] <= 5)
        for v in (-0.10, 0.0, 0.10): M[("ret20Min", v)] = P["ret20"] >= v
        for v in (8, 15, 25): M[("riskMax", v)] = (P["risk"] <= v) & (P["risk"] > 0)
        return M

    def mask(self, c: dict, strict: bool = True) -> np.ndarray:
        """strict=True: 청산 결과가 확정된 행만(탐색용). False: 지표 계산 가능한 모든 행(포트폴리오 시뮬레이션용; 끝까지 보유 중인 포지션을 마지막 종가로 평가)."""
        m = (self.valid[c["exit"]] if strict else self.ok) & self.regime[c["regime"]]
        if c["trend"]: m = m & self.trend
        for key in ("rs", "atrMin", "atrMax", "emaLo", "emaHi", "highMin", "rsiLo", "rsiHi", "vol", "ret20Min", "riskMax"):
            mk = self.masks.get((key, c[key]))
            if mk is not None: m = m & mk
        if c["breakout"]: m = m & self.masks[("breakout", 1)]
        return m

    def score(self, c: dict, out_override: dict | None = None, base_override: dict | None = None) -> dict:
        m = self.mask(c)
        out = (out_override or self.out)[c["exit"]]
        n = np.bincount(self.didx[m], minlength=self.D)
        s = np.bincount(self.didx[m], weights=out[m].astype(np.float64), minlength=self.D)
        mean = np.divide(s, n, out=np.full(self.D, np.nan), where=n > 0)
        exc = mean - (base_override or self.base)[c["exit"]]
        res = {}
        for per, (a, b) in self.period_slice.items():
            nn, ee = n[a:b], exc[a:b]
            have = nn > 0
            sig, nd = int(nn.sum()), int(have.sum())
            if sig < MIN_SIGNALS or nd < MIN_DATES:
                res[per] = {"t": None, "signals": sig, "dates": nd, "excessPct": None, "meanPct": None}
                continue
            blk = (np.arange(b - a) // BLOCK)[have]
            bs = np.bincount(blk, weights=ee[have])
            bc = np.bincount(blk)
            bm = bs[bc > 0] / bc[bc > 0]
            t = float(bm.mean() / (bm.std(ddof=1) / np.sqrt(len(bm)))) if len(bm) > 2 and bm.std(ddof=1) > 0 else None
            res[per] = {"t": t, "signals": sig, "dates": nd, "excessPct": float(np.nanmean(ee[have]) * 100), "meanPct": float(s[a:b].sum() / max(sig, 1) * 100)}
        return res


def combined(r: dict) -> float:
    ts = [r["train"]["t"], r["val"]["t"]]
    return min(ts) if all(t is not None for t in ts) else -99.0


_G: dict = {}


def _work(args):
    lo, hi, null_rep = args
    d = _G["data"]
    out_override, base_override = (_G["nulls"][null_rep] if null_rep is not None else (None, None))
    res = []
    for i in range(lo, hi):
        r = d.score(_G["cfgs"][i], out_override, base_override)
        res.append((i, combined(r), None if null_rep is not None else r))
    return res


def run_all(data: Data, cfgs: list[dict], null_reps: int, workers: int) -> tuple[list, list[np.ndarray]]:
    rng = np.random.default_rng(SEED + 1)
    starts = np.r_[0, np.cumsum(np.bincount(data.didx, minlength=data.D))]
    nulls = []
    for _ in range(null_reps):
        perm = np.arange(len(data.didx))
        for dd in range(data.D):                                   # 같은 날 종목끼리 결과를 서로 바꿔 끼워 신호-수익 연결을 끊는다(날짜별 시장 효과는 보존)
            lo, hi = starts[dd], starts[dd + 1]
            perm[lo:hi] = lo + rng.permutation(hi - lo)
        outs = {k: np.nan_to_num(v[perm], nan=0.0) for k, v in data.out.items()}
        nulls.append((outs, {k: data._date_mean(outs[k], data.valid[k]) for k in EXITS}))
    _G.update(data=data, cfgs=cfgs, nulls=nulls)
    chunks = [(a, min(a + 2000, len(cfgs))) for a in range(0, len(cfgs), 2000)]
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        real = [x for part in pool.imap_unordered(_work, [(a, b, None) for a, b in chunks]) for x in part]
        null_scores = []
        for k in range(null_reps):
            log.info("귀무 %d/%d", k + 1, null_reps)
            null_scores.append(np.array([s for part in pool.imap_unordered(_work, [(a, b, k) for a, b in chunks]) for _, s, _ in part]))
    return real, null_scores


def build_data(market: str, universe: list[dict], idx_sym: str, start_fetch: str, panel_hook=None, data_cls=None, extra: tuple = ()) -> Data:
    import yfinance as yf
    syms = {}
    for u in universe:
        code = str(u["code"])
        syms[code.replace(".", "-") if market == "us" else code.zfill(6) + (".KQ" if u.get("market") == "KOSDAQ" else ".KS")] = code
    data = fetch_ohlcv(list(syms), start=start_fetch)
    f = yf.download(idx_sym, start=start_fetch, interval="1d", auto_adjust=True, progress=False)
    ic = f["Close"].iloc[:, 0] if isinstance(f.columns, pd.MultiIndex) else f["Close"]
    ic.index = pd.DatetimeIndex(ic.index).tz_localize(None)
    ic = ic.dropna()
    regimes = {n: ic > ic.rolling(n).mean() for n in (100, 150, 200)}
    rows = []
    for code, df in data.items():
        x = stock_frame(df, COST[market])
        x["code"] = code
        rows.append(x)
    panel = pd.concat(rows)
    panel.index.name = "date"
    panel = panel.reset_index()
    panel["rs"] = panel.groupby("date")["ret252"].rank(pct=True) * 100
    panel = panel[panel["date"] >= PERIODS["train"][0]]
    if panel_hook:
        panel = panel_hook(panel)
    d = (data_cls or Data)(panel, regimes, extra=extra) if data_cls else Data(panel, regimes, extra=extra)
    d.bench = ic
    return d


def summarize(market: str, data: Data, cfgs: list[dict], real: list, null_scores: list[np.ndarray]) -> dict:
    scores = np.full(len(cfgs), -99.0)
    detail = {}
    for i, s, r in real:
        scores[i] = s
        detail[i] = r
    order = np.argsort(-scores)
    pooled = np.concatenate(null_scores) if null_scores else np.array([])
    pooled = pooled[pooled > -99]
    n_valid = int((scores > -99).sum())
    best = float(scores.max())
    rc_p = float((sum(float(ns.max()) >= best for ns in null_scores) + 1) / (len(null_scores) + 1)) if null_scores else None   # White 식 현실점검: 귀무 복제 중 최고 점수가 실제 최고 이상인 비율

    def q(s):
        return float((pooled >= s).sum() + 1) / (len(pooled) + 1) if len(pooled) else None

    top = []
    for i in order[:20]:
        full = data.score(cfgs[i])
        s = float(scores[i])
        qq = q(s)
        top.append({"rank": len(top) + 1, "config": cfgs[i], "score": round(s, 2), "nullTail": qq, "bonferroniP": min(1.0, qq * len(cfgs)) if qq is not None else None,
                    "bonferroniPValidN": min(1.0, qq * n_valid) if qq is not None else None,
                    "periods": {k: {a: (round(b, 3) if isinstance(b, float) else b) for a, b in v.items()} for k, v in full.items()}})
    allp = {per: [detail[i][per]["excessPct"] for i in detail if detail[i][per]["excessPct"] is not None] for per in PERIODS}
    return {"market": market, "configs": len(cfgs), "validConfigs": n_valid, "rows": int(len(data.didx)), "dates": int(data.D),
            "nullReplicates": len(null_scores), "realityCheckP": rc_p, "bonferroniPValidN": None, "nullMaxScores": [round(float(s.max()), 2) for s in null_scores],
            "nullScoreQuantiles": {str(p): round(float(np.quantile(pooled, p)), 2) for p in (0.5, 0.9, 0.99, 0.999)} if len(pooled) else None,
            "realScoreQuantiles": {str(p): round(float(np.quantile(scores[scores > -99], p)), 2) for p in (0.5, 0.9, 0.99, 0.999)} if n_valid else None,
            "positiveTestShareAmongTop20": round(float(np.mean([(t["periods"]["test"]["excessPct"] or -1) > 0 for t in top])), 2) if top else None, "top": top,
            "avgExcessPctByPeriodAllValid": {k: round(float(np.mean(v)), 3) if v else None for k, v in allp.items()}}


def to_markdown(res: dict) -> str:
    o = ["# 대규모 규칙 탐색 결과 (과최적화 통제)", "", f"> 실행 {res['ranAt']} · 설계 2026-11-03 고정 · 생존편향 · 신호 단위 평가(포트폴리오 제약 미반영) · 참고용", "", "```", __doc__.strip(), "```", ""]
    for m in res["markets"].values():
        o += [f"## {m['market'].upper()} — 조합 {m['configs']:,}개(유효 {m['validConfigs']:,}), 행 {m['rows']:,}, 거래일 {m['dates']}", "",
              f"- 귀무 복제 {m['nullReplicates']}회 최고 점수: {m['nullMaxScores']} / 귀무 점수 분위수: {m['nullScoreQuantiles']}",
              f"- 실제 점수 분위수: {m['realScoreQuantiles']}",
              f"- 현실점검 p(귀무 {m['nullReplicates']}회 중 최고 점수가 실제 최고 이상인 비율, 최소 1/(복제+1)): {m['realityCheckP']}",
              f"- 유효 조합 전체의 구간별 평균 초과수익 %p(신호 단위): {m['avgExcessPctByPeriodAllValid']}",
              f"- 상위 20개 중 시험 구간 초과수익이 양수인 비율: {m['positiveTestShareAmongTop20']}", "",
              "| 순위 | 점수(min t) | Bonferroni p | 훈련 t/초과% | 검증 t/초과% | 시험 t/초과%/신호 | 조건 |", "|---|---|---|---|---|---|---|"]
        for t in m["top"]:
            P, c = t["periods"], t["config"]
            fm = lambda k: f"{P[k]['t']:.2f}/{P[k]['excessPct']:.2f}" if P[k]["t"] is not None else "n/a"
            ts = f"{P['test']['t']:.2f}/{P['test']['excessPct']:.2f}/{P['test']['signals']}" if P["test"]["t"] is not None else f"n/a/{P['test']['signals']}"
            cond = ", ".join(f"{k}={v}" for k, v in c.items() if v not in (0, -9, 9, 999, 101) or k in ("exit",))
            o.append(f"| {t['rank']} | {t['score']} | {t['bonferroniP'] if t['bonferroniP'] is None else round(t['bonferroniP'], 4)} | {fm('train')} | {fm('val')} | {ts} | {cond} |")
        o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    start_fetch = os.environ.get("LB_FETCH_START") or "2015-06-01"
    workers = max(1, (os.cpu_count() or 2))
    kr = [r for r in json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    us = [r for r in json.loads((ROOT / "docs" / "data" / "latest_us.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    res = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "nConfigs": N_CONFIGS, "nullReps": N_NULL_REPS, "markets": {}}
    for market, uni, sym in (("us", us, "^GSPC"), ("kr", kr, "^KS11")):
        data = build_data(market, uni, sym, start_fetch)
        cfgs = sample_configs(N_CONFIGS)
        real, nulls = run_all(data, cfgs, N_NULL_REPS, workers)
        res["markets"][market] = summarize(market, data, cfgs, real, nulls)
        log.info("%s 완료", market)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "search.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT_DIR / "search.md").write_text(to_markdown(res), encoding="utf-8")
    print(to_markdown(res))


if __name__ == "__main__":
    main()
