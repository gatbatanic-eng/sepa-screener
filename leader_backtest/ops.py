"""운용형 백테스트(포트폴리오 시뮬레이션). 규칙은 2026-11-03에 결과를 보기 전에 고정했고, 결과를 보고 바꾸지 않는다(바꾸면 새 계열).
python -m leader_backtest.ops  (환경변수 LB_FETCH_START, LB_EVAL_START, LB_OUT 은 main 과 같다. 권장: 2020-10-01 / 2022-01-03)

신호: rules.py 의 BREAKOUT(기본), LEADER, PULLBACK 그룹(재신호 10거래일 제외). 신호일 다음 거래일 종가 진입.
시장 필터: 신호일 기준 지수 종가 > 지수 200일선일 때만 신규 진입(켬/끔 둘 다 보고). 미국 ^GSPC, 한국 코스피 ^KS11.
손절: 종가가 진입가 -8% 이하가 되는 종가에서 청산.
청산 E1: 위 손절 + 종가가 50일선 아래로 마감. E2: 위 손절 + 진입 후 최고 종가 대비 -15% 트레일링.
비중: 계좌 평가금액의 12.5%(=1% 위험 / 8% 손절), 동시 최대 8종목, 신호가 넘치면 RS 높은 순. 현금 이자 0. 편도 비용 한국 25bp·미국 10bp.
대조군: 같은 날 BREAKOUT 신호 수만큼 '지표 계산 가능한 종목'에서 무작위로 뽑아(시드 5개) 같은 시장 필터·청산·비중으로 운용 -> 신호 선별이 더하는 몫을 가른다.
비교: 같은 기간 지수 매수 후 보유. 한계: 생존편향(현재 유니버스; 2022 하락장에서 이후 살아남은 종목만 본다), 유동성·슬리피지 단순화, 종가 체결 가정."""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

from .main import COST, OUT_DIR, ROOT, START_EVAL, START_FETCH, fetch_ohlcv
from .rules import CFG, features, select

log = logging.getLogger("leader_backtest.ops")
P = {"stop": 0.08, "trail": 0.15, "weight": 0.125, "max_pos": 8, "regime_sma": 200, "seeds": (11, 12, 13, 14, 15)}
SIGNALS = ("BREAKOUT", "LEADER", "PULLBACK")
EXITS = ("E1", "E2")


def simulate(close: pd.DataFrame, sma50: pd.DataFrame, sig: pd.DataFrame, rs: pd.DataFrame, regime: pd.Series, exit_rule: str, cost: float, p: dict = P) -> dict:
    """close/sma50/sig/rs: 날짜×종목(같은 인덱스·열). sig[t]=t일 종가에 난 신호 -> t+1 종가 진입. regime[t]=t일 신규 진입 허용."""
    dates = close.index
    cl, sm, sg, rr = close.to_numpy(float), sma50.to_numpy(float), sig.to_numpy(bool), rs.to_numpy(float)
    reg = regime.reindex(dates).fillna(False).to_numpy(bool)
    cash, pos, trades, eq = 1.0, {}, [], []
    last = np.full(cl.shape[1], np.nan)
    for i in range(len(dates)):
        row = cl[i]
        ok = ~np.isnan(row)
        last[ok] = row[ok]
        for j in list(pos):
            if np.isnan(row[j]):
                continue
            q = pos[j]
            q["peak"] = max(q["peak"], row[j])
            hit = row[j] <= q["entry"] * (1 - p["stop"])
            if exit_rule == "E1":
                hit = hit or (not np.isnan(sm[i, j]) and row[j] < sm[i, j])
            else:
                hit = hit or row[j] <= q["peak"] * (1 - p["trail"])
            if hit:
                cash += q["shares"] * row[j] * (1 - cost)
                trades.append({"ret": row[j] / q["entry"] - 1 - 2 * cost, "days": i - q["i"], "exit": dates[i]})
                del pos[j]
        equity = cash + sum(q["shares"] * (last[j] if not np.isnan(last[j]) else q["entry"]) for j, q in pos.items())
        if i > 0 and reg[i - 1] and len(pos) < p["max_pos"]:
            cand = np.flatnonzero(sg[i - 1] & ok)
            cand = [j for j in cand[np.argsort(-np.nan_to_num(rr[i - 1, cand]))] if j not in pos]
            for j in cand:
                if len(pos) >= p["max_pos"]:
                    break
                notional = p["weight"] * equity
                if cash < notional * (1 + cost):
                    break
                cash -= notional * (1 + cost)
                pos[j] = {"entry": row[j], "shares": notional / row[j], "peak": row[j], "i": i}
        eq.append(cash + sum(q["shares"] * (last[j] if not np.isnan(last[j]) else q["entry"]) for j, q in pos.items()))
    eq = pd.Series(eq, index=dates)
    return {"equity": eq, "trades": trades}


def metrics(eq: pd.Series, trades: list, bench: pd.Series) -> dict:
    eq = eq / eq.iloc[0]
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    dd = (eq / eq.cummax() - 1).min()
    r = [t["ret"] for t in trades]
    w, l = [x for x in r if x > 0], [x for x in r if x <= 0]
    byyear = {str(y): round(float((g.iloc[-1] / (eq[eq.index.year < y].iloc[-1] if (eq.index.year < y).any() else 1.0) - 1) * 100), 1) for y, g in eq.groupby(eq.index.year)}
    b = bench.reindex(eq.index).ffill()
    b = b / b.iloc[0]
    byyear_b = {str(y): round(float((g.iloc[-1] / (b[b.index.year < y].iloc[-1] if (b.index.year < y).any() else 1.0) - 1) * 100), 1) for y, g in b.groupby(b.index.year)}
    return {"totalPct": round(float((eq.iloc[-1] - 1) * 100), 1), "cagrPct": round(float((eq.iloc[-1] ** (1 / yrs) - 1) * 100), 1), "mddPct": round(float(dd * 100), 1),
            "trades": len(r), "winPct": round(100 * len(w) / len(r), 1) if r else None, "avgWinPct": round(100 * float(np.mean(w)), 1) if w else None,
            "avgLossPct": round(100 * float(np.mean(l)), 1) if l else None, "avgDays": round(float(np.mean([t["days"] for t in trades])), 1) if trades else None,
            "profitFactor": round(sum(w) / -sum(l), 2) if l and sum(l) < 0 else None, "expectancyPct": round(100 * float(np.mean(r)), 2) if r else None,
            "byYearPct": byyear, "benchTotalPct": round(float((b.iloc[-1] - 1) * 100), 1), "benchMddPct": round(float((b / b.cummax() - 1).min() * 100), 1), "benchByYearPct": byyear_b}


def build(market: str, universe: list[dict], idx_sym: str) -> dict:
    import yfinance as yf
    syms = {}
    for u in universe:
        code = str(u["code"])
        syms[code.replace(".", "-") if market == "us" else code.zfill(6) + (".KQ" if u.get("market") == "KOSDAQ" else ".KS")] = code
    data = fetch_ohlcv(list(syms))
    f = yf.download(idx_sym, start=START_FETCH, interval="1d", auto_adjust=True, progress=False)
    ic = f["Close"].iloc[:, 0] if isinstance(f.columns, pd.MultiIndex) else f["Close"]
    ic.index = pd.DatetimeIndex(ic.index).tz_localize(None)
    ic = ic.dropna()
    rows = []
    for code, df in data.items():
        x = features(df)
        x["code"], x["close"], x["sma50"] = code, df["close"], df["close"].rolling(50).mean()
        rows.append(x)
    panel = pd.concat(rows)
    panel.index.name = "date"
    panel = panel.reset_index()
    panel["rs"] = panel.groupby("date")["ret252"].rank(pct=True) * 100
    masks = select(panel)
    regime = (ic > ic.rolling(P["regime_sma"]).mean())
    piv = lambda col: panel.pivot(index="date", columns="code", values=col).sort_index()
    close, sma50, rs = piv("close"), piv("sma50"), piv("rs")
    sigs = {g: panel.assign(m=masks[g].values).pivot(index="date", columns="code", values="m").reindex(index=close.index, columns=close.columns).fillna(False).astype(bool) for g in SIGNALS}
    okm = panel.assign(m=panel["ok"].values).pivot(index="date", columns="code", values="m").reindex(index=close.index, columns=close.columns).fillna(False).astype(bool)
    keep = close.index >= pd.Timestamp(START_EVAL)
    first = np.argmax(keep)
    sl = slice(max(first - 1, 0), None)          # 첫 평가일의 전일 신호까지 보이도록 한 칸 앞에서 시작
    out = {"market": market, "stocks": int(close.shape[1]), "first": str(close.index[first].date()), "last": str(close.index[-1].date()), "results": {}}
    bench = ic[ic.index >= close.index[first]]
    for g in SIGNALS:
        for ex in EXITS:
            for rg in (True, False):
                reg = regime if rg else pd.Series(True, index=regime.index)
                s = simulate(close.iloc[sl], sma50.iloc[sl], sigs[g].iloc[sl], rs.iloc[sl], reg, ex, COST[market] / 2)
                eq = s["equity"][s["equity"].index >= close.index[first]]
                out["results"][f"{g}|{ex}|{'REG' if rg else 'NOREG'}"] = metrics(eq, s["trades"], bench)
    brk = sigs["BREAKOUT"].iloc[sl]
    per_day = brk.sum(axis=1).to_numpy()
    for ex in EXITS:
        for rg in (True, False):
            reg = regime if rg else pd.Series(True, index=regime.index)
            ms = []
            for seed in P["seeds"]:
                rng = np.random.default_rng(seed)
                rnd = np.zeros(okm.iloc[sl].shape, bool)
                okv = okm.iloc[sl].to_numpy(bool)
                for i, n in enumerate(per_day):
                    cand = np.flatnonzero(okv[i])
                    if n and len(cand):
                        rnd[i, rng.choice(cand, min(int(n), len(cand)), replace=False)] = True
                s = simulate(close.iloc[sl], sma50.iloc[sl], pd.DataFrame(rnd, index=close.index[sl], columns=close.columns), rs.iloc[sl], reg, ex, COST[market] / 2)
                eq = s["equity"][s["equity"].index >= close.index[first]]
                ms.append(metrics(eq, s["trades"], bench))
            keys = ("totalPct", "cagrPct", "mddPct", "trades", "winPct", "expectancyPct")
            out["results"][f"RANDOM|{ex}|{'REG' if rg else 'NOREG'}"] = {k: round(float(np.mean([m[k] for m in ms if m[k] is not None])), 2) for k in keys} | {"seeds": len(ms), "totalPctRange": [min(m["totalPct"] for m in ms), max(m["totalPct"] for m in ms)]}
    return out


def to_markdown(res: dict) -> str:
    o = ["# 운용형 백테스트 (포트폴리오 시뮬레이션)", "", f"> 실행 {res['ranAt']} · 규칙 2026-11-03 고정 · 생존편향 있음 · 참고용", "", "```", __doc__.strip(), "```", ""]
    for m in res["markets"].values():
        o += [f"## {m['market'].upper()} — 종목 {m['stocks']}개, {m['first']} ~ {m['last']}", "", "| 신호|청산|시장필터 | 누적% | 연환산% | MDD% | 거래 | 승률% | 평균이익% | 평균손실% | 보유일 | PF | 기대값% | 지수 누적%/MDD% |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for k, v in m["results"].items():
            if k.startswith("RANDOM"):
                o.append(f"| {k} (5시드 평균) | {v['totalPct']} (범위 {v['totalPctRange']}) | {v['cagrPct']} | {v['mddPct']} | {v['trades']} | {v['winPct']} | - | - | - | - | {v['expectancyPct']} | - |")
            else:
                o.append(f"| {k} | {v['totalPct']} | {v['cagrPct']} | {v['mddPct']} | {v['trades']} | {v['winPct']} | {v['avgWinPct']} | {v['avgLossPct']} | {v['avgDays']} | {v['profitFactor']} | {v['expectancyPct']} | {v['benchTotalPct']}/{v['benchMddPct']} |")
        o += ["", "연도별 수익률(%) — 전략 vs 지수", "| 조합 | " + " | ".join(next(iter(m["results"].values()))["byYearPct"]) + " |", "|---|" + "---|" * len(next(iter(m["results"].values()))["byYearPct"])]
        for k, v in m["results"].items():
            if not k.startswith("RANDOM"):
                o.append(f"| {k} | " + " | ".join(f"{a} ({b})" for a, b in zip(v["byYearPct"].values(), v["benchByYearPct"].values())) + " |")
        o.append("")
    return "\n".join(o)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kr = [r for r in json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    us = [r for r in json.loads((ROOT / "docs" / "data" / "latest_us.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    res = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "params": P | {"seeds": list(P["seeds"])}, "markets": {"us": build("us", us, "^GSPC"), "kr": build("kr", kr, "^KS11")}}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "ops.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "ops.md").write_text(to_markdown(res), encoding="utf-8")
    print(to_markdown(res))


if __name__ == "__main__":
    main()
