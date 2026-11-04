"""지수 매수 후 보유 vs 지수 + 이동평균 시장 필터. 규칙은 2026-11-03에 결과를 보기 전에 고정했고, 결과를 보고 바꾸지 않는다(바꾸면 새 계열).
python -m leader_backtest.index_filter  (결과 research/leader_backtest/[LB_OUT/]index_filter.{json,md})

규칙: t일 종가가 t일 N일 단순이동평균 위면 t+1일 수익을 얻고(보유), 아니면 현금(수익 0). 신호는 종가 기준이고 다음 날 반영하므로 미래 정보를 쓰지 않는다.
사전 지정 기본: N=200. 민감도 확인용: N=100·150·250 (다중비교 주의, 기본값만으로 판정한다).
비교: 같은 기간 매수 후 보유. 평가 구간은 N=250 이동평균이 처음 계산되는 날부터로 모든 변형을 같게 맞춘다.
지수: 미국 ^GSPC·^IXIC, 한국 ^KS11 (가격지수라 배당 미포함, 현금 이자 0 -> 필터 쪽에 불리하지도 유리하지도 않지만 절대 수익은 낮게 나온다).
비용: 보유 전환 1회당 편도 미국 5bp·한국 15bp(ETF 수준 가정). 세금 미반영.
성공 기준(사전): 필터가 최대낙폭을 지수 대비 1/2 이하로 줄이면서 연환산 수익이 지수의 70% 이상이면 '유효'. 둘 중 하나만이면 '부분'. 둘 다 아니면 '무효'."""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

from .main import OUT_DIR

log = logging.getLogger("leader_backtest.index_filter")
INDEXES = {"US_SPX": ("^GSPC", 0.0005), "US_NDX": ("^IXIC", 0.0005), "KR_KOSPI": ("^KS11", 0.0015)}
WINDOWS = (100, 150, 200, 250)
PRIMARY = 200
FETCH_START = "1996-01-01"
PERIODS = {"FULL": None, "SINCE2022": "2022-01-03"}


def run_filter(close: pd.Series, n: int, cost: float) -> pd.Series:
    """일별 수익률 시리즈(전략). 신호 t -> t+1 수익, 전환 비용은 전환 다음 날 수익에서 뺀다."""
    sig = (close > close.rolling(n).mean()).astype(float)
    pos = sig.shift(1).fillna(0.0)
    ret = close.pct_change().fillna(0.0)
    turn = pos.diff().abs().fillna(pos.abs())
    return pos * ret - turn * cost


def metrics(daily: pd.Series, pos_days: float | None = None) -> dict:
    eq = (1 + daily).cumprod()
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    dd = float((eq / eq.cummax() - 1).min())
    vol = float(daily.std() * np.sqrt(252))
    by = {}
    prev = 1.0
    for y, g in eq.groupby(eq.index.year):
        by[str(y)] = round(float((g.iloc[-1] / prev - 1) * 100), 1)
        prev = float(g.iloc[-1])
    cagr = float(eq.iloc[-1] ** (1 / yrs) - 1)
    return {"totalPct": round(float((eq.iloc[-1] - 1) * 100), 1), "cagrPct": round(cagr * 100, 2), "mddPct": round(dd * 100, 1), "volPct": round(vol * 100, 1),
            "retOverVol": round(float(daily.mean() * 252 / vol), 2) if vol > 0 else None, "calmar": round(cagr / -dd, 2) if dd < 0 else None, "byYearPct": by}


def evaluate(close: pd.Series, cost: float) -> dict:
    close = close.dropna()
    start = close.index[max(WINDOWS) - 1]      # 가장 긴 이동평균이 처음 계산되는 날부터 모든 변형을 같은 구간으로
    out = {"first": str(start.date()), "last": str(close.index[-1].date()), "periods": {}}
    bh_all = close.pct_change().fillna(0.0)
    strat_all = {n: run_filter(close, n, cost) for n in WINDOWS}
    for pname, pstart in PERIODS.items():
        lo = max(start, pd.Timestamp(pstart)) if pstart else start
        res = {"BUYHOLD": metrics(bh_all[bh_all.index >= lo])}
        for n in WINDOWS:
            d = strat_all[n][strat_all[n].index >= lo]
            m = metrics(d)
            pos = (close > close.rolling(n).mean()).shift(1).fillna(False)
            pos = pos[pos.index >= lo]
            m["exposurePct"] = round(float(pos.mean() * 100), 1)
            m["switches"] = int(pos.astype(int).diff().abs().sum())
            res[f"SMA{n}"] = m
        b, f = res["BUYHOLD"], res[f"SMA{PRIMARY}"]
        halved = f["mddPct"] >= b["mddPct"] / 2            # 둘 다 음수: 필터 낙폭이 지수 낙폭의 절반 이하
        keeps = f["cagrPct"] >= 0.7 * b["cagrPct"] if b["cagrPct"] > 0 else f["cagrPct"] >= b["cagrPct"]
        res["verdict"] = {"mddHalved": bool(halved), "cagrKeeps70": bool(keeps), "label": "유효" if halved and keeps else "부분" if halved or keeps else "무효"}
        res["_first"] = str(lo.date())
        out["periods"][pname] = res
    return out


def to_markdown(res: dict) -> str:
    o = ["# 지수 매수 후 보유 vs 지수 + 이동평균 필터", "", f"> 실행 {res['ranAt']} · 규칙 2026-11-03 고정 · 가격지수(배당 미포함) · 현금 이자 0 · 참고용", "", "```", __doc__.strip(), "```", ""]
    for name, r in res["indexes"].items():
        o += [f"## {name} ({r['first']} ~ {r['last']})", ""]
        for pname, per in r["periods"].items():
            v = per["verdict"]
            o += [f"### {pname} (시작 {per['_first']}) — 기본 SMA{PRIMARY} 판정: **{v['label']}** (낙폭 절반 이하 {v['mddHalved']}, 수익 70% 유지 {v['cagrKeeps70']})", "",
                  "| 전략 | 누적% | 연환산% | 최대낙폭% | 변동성% | 수익/변동성 | Calmar | 노출% | 전환 |", "|---|---|---|---|---|---|---|---|---|"]
            for k, m in per.items():
                if k in ("verdict", "_first"):
                    continue
                o.append(f"| {k} | {m['totalPct']} | {m['cagrPct']} | {m['mddPct']} | {m['volPct']} | {m['retOverVol']} | {m['calmar']} | {m.get('exposurePct', 100.0)} | {m.get('switches', 0)} |")
            o.append("")
        full = r["periods"]["FULL"]
        years = list(full["BUYHOLD"]["byYearPct"])
        o += ["연도별 수익률(%) — 매수후보유 / SMA200", "| 연도 | " + " | ".join(years) + " |", "|---|" + "---|" * len(years),
              "| 매수후보유 | " + " | ".join(str(full["BUYHOLD"]["byYearPct"][y]) for y in years) + " |",
              f"| SMA{PRIMARY} | " + " | ".join(str(full[f"SMA{PRIMARY}"]["byYearPct"].get(y)) for y in years) + " |", ""]
    return "\n".join(o)


def main() -> None:
    import yfinance as yf
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    res = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "primary": PRIMARY, "windows": list(WINDOWS), "indexes": {}}
    for name, (sym, cost) in INDEXES.items():
        f = yf.download(sym, start=FETCH_START, interval="1d", auto_adjust=True, progress=False)
        c = f["Close"].iloc[:, 0] if isinstance(f.columns, pd.MultiIndex) else f["Close"]
        c.index = pd.DatetimeIndex(c.index).tz_localize(None)
        res["indexes"][name] = evaluate(c.dropna(), cost)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index_filter.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "index_filter.md").write_text(to_markdown(res), encoding="utf-8")
    print(to_markdown(res))


if __name__ == "__main__":
    main()
