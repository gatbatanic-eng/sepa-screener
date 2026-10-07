"""4단계 B1 검증 산출물 생성: 거래 CSV, 참조 구현 대조, 지표, 사람 대조용 15건(차트 + 한 줄 요약).

실행: python -m golden_backtest.reports.b1_check            (검증 종목 5개)
      python -m golden_backtest.reports.b1_check --universe (P1 전 종목 참조 구현 대조만, 2013-01-01부터 잘라서)
출력: golden_backtest/reports/b1_check/
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from golden_backtest.data import quality, store
from golden_backtest.engine.costs import Costs
from golden_backtest.engine.simulator import simulate
from golden_backtest.evaluation import metrics
from golden_backtest.strategies.b1_turtle import B1Turtle
from golden_backtest.tests import b1_reference as ref

OUT = Path(__file__).resolve().parent / "b1_check"
TICKERS = ("AAPL", "NVDA", "OXY", "KDP", "KO")
SPEC_VERSION = "1.6"
LARGE_DIV_PCT = 0.10
MANIFEST = Path(__file__).resolve().parents[1] / "manifest"


def load_events(sym: str, since: str = "2015-01-01") -> list[tuple[pd.Timestamp, str]]:
    """분할(2015 이후)과 수정·공급자 수익률이 크게 어긋난 봉(특별배당·분리상장)의 날짜."""
    ev = [(d, f"분할 {r:g}:1") for d, r in store.load_splits(sym).items() if d >= pd.Timestamp(since)]
    q = json.loads((MANIFEST / "quality_report.json").read_text(encoding="utf-8"))["per_symbol"].get(sym, {})
    ev += [(pd.Timestamp(a["date"]), "특별배당·분리상장 보정") for a in q.get("adjustment_divergences", []) if a["date"] >= since]
    return sorted(ev)


def run_symbol(sym: str, costs: Costs, df: pd.DataFrame | None = None):
    df = store.load_ohlcv(sym) if df is None else df
    strat = B1Turtle()
    res = simulate(df, strat, sym, costs, SPEC_VERSION)
    return df, strat, res


def trade_table(sym: str, df: pd.DataFrame, strat: B1Turtle, res) -> pd.DataFrame:
    prep = strat.prepare(df)
    pos = {d: i for i, d in enumerate(df.index)}
    events = load_events(sym)
    divs = quality.large_dividends(df, store.load_dividends(sym), LARGE_DIV_PCT, "2015-01-01")   # 배당락 수익률 >= 10%
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    rows = []
    for t in res.with_open_mtm():
        si, ei, xi = pos[t.signal_date], pos[t.entry_date], pos[t.exit_date]
        level = float(prep["entry_level"].iloc[si])
        span = [lab for d, lab in events if t.entry_date <= d <= t.exit_date]
        # 보유 기간에 낀 대형 배당: 진입 이후(진입일 다음 봉부터) 청산일까지 배당락 봉이 있는 경우. 진입일 당일 배당락은 보유 중이 아니다
        ld = [d for d in divs if t.entry_date < pd.Timestamp(d["bar_date"]) <= t.exit_date]
        fac = lambda i: float(df["v_close"].iloc[i] / df["close"].iloc[i])  # 수정가 → 공급자 종가 기준(분할 보정, 배당 미보정)
        lvl_idx = si - strat.entry_n + 1 + int(np.argmax(h[si - strat.entry_n + 1: si + 1][::-1][::-1]))
        rows.append(dict(
            ticker=sym, trade_id=t.trade_id, signal_date=t.signal_date.date(), breakout_level=level, n_at_signal=float(prep["n"].iloc[si]),
            entry_date=t.entry_date.date(), entry_open=float(o[ei]), entry_price=t.entry_price, gap_up_entry=bool(o[ei] > level),
            initial_stop=t.initial_stop, exit_date=t.exit_date.date(), exit_open=float(o[xi]), exit_price=t.exit_price,
            exit_reason=t.exit_reason, hold_days=t.hold_days, same_day_stop=bool(ei == xi and t.exit_reason == "stop"),
            gap_exit=bool(xi > ei and t.exit_reason in ("stop", "rule") and t.exit_price == o[xi]),
            cost_rate=costs_rate(), costs=t.costs, r_multiple=t.r_multiple, mfe_r=t.mfe_r, mae_r=t.mae_r, status="open_mtm" if t.exit_reason == "open_mtm" else "closed",
            raw_close_signal=float(df["raw_close"].iloc[si]), raw_lt_10=bool(df["raw_close"].iloc[si] < 10),
            event_in_trade="; ".join(span), spans_event=bool(span),
            large_div_in_trade=bool(ld),
            large_div_detail="; ".join(f"{d['bar_date']} 주당 {d['amount']:g} (배당락 전일 종가 대비 {d['pct_of_prev_close']:.1%})" for d in ld),
            level_vendor=level * fac(lvl_idx), entry_vendor=t.entry_price * fac(ei), stop_vendor=(t.initial_stop * fac(ei)) if t.initial_stop else None,
            exit_vendor=t.exit_price * fac(xi), signal_idx=si, entry_idx=ei, exit_idx=xi))
    return pd.DataFrame(rows)


_COSTS = Costs.from_config()


def costs_rate() -> float:
    return _COSTS.one_way_rate()


def mark_review_set(tab: pd.DataFrame) -> pd.Series:
    """73건 대조 대상(CSV 표시용): 당일 손절, 갭 청산, 분할·특별배당에 걸친 거래 전부 + 종목별 처음·마지막 2건 + 갭 상승 진입 표본 2건."""
    sel = pd.Series(False, index=tab.index)
    for _, g in tab.groupby("ticker"):
        s = g.same_day_stop | g.gap_exit | g.spans_event
        s.iloc[[0, 1, -2, -1]] = True
        sel.loc[g.index[s]] = True
        sel.loc[g[g.gap_up_entry & ~s].index[:2]] = True
    return sel


def pick_human_15(tab: pd.DataFrame) -> pd.DataFrame:
    """사람 대조 15건. 결정 규칙은 고정(성과를 보고 고르지 않는다).
    - 분할·특별배당에 걸친 거래 4건: 전부 (다른 범주와 겹치지 않도록 가장 먼저 확정)
    - 진입 당일 손절 3건: 당일 손절이 많은 종목 순(OXY, KO, NVDA 등)으로 종목마다 가장 이른 1건
    - 갭 하락 청산 3건: r이 가장 나쁜 순(이미 뽑힌 거래 제외)
    - 갭 상승 진입 3건: 갭 크기(시가/돌파 수준 − 1)가 큰 순(이미 뽑힌 거래 제외)
    - KO 실패 돌파 2건: KO에서 초기 손절(2N)로 청산된 거래 중 보유 기간이 짧은 순(당일 손절 제외)
    """
    chosen: list[tuple[int, str]] = []   # (tab index, category)
    used = set()

    def take(idx, cat):
        if idx not in used:
            used.add(idx)
            chosen.append((idx, cat))

    closed = tab[tab.status == "closed"]
    # 분할·특별배당에 걸친 거래를 먼저 확정한다(다른 범주와 겹쳐도 4건이 모두 남도록)
    for idx in tab[tab.spans_event].sort_values(["ticker", "entry_date"]).index:
        take(idx, "분할·특별배당에 걸친 거래")
    sd = closed[closed.same_day_stop & ~closed.index.isin(used)]
    order = sd.groupby("ticker").size().sort_values(ascending=False, kind="stable").index
    for sym in list(order)[:3]:
        take(sd[sd.ticker == sym].index[0], "진입 당일 손절")
    gd = closed[closed.gap_exit & ~closed.index.isin(used)].sort_values(["r_multiple", "exit_date"])
    for idx in gd.index[:3]:
        take(idx, "갭 하락 청산")
    gu = tab[tab.gap_up_entry & ~tab.index.isin(used)].copy()
    gu["gap"] = gu.entry_open / gu.breakout_level - 1
    for idx in gu.sort_values("gap", ascending=False).index[:3]:
        take(idx, "갭 상승 진입")
    ko = closed[(closed.ticker == "KO") & (closed.exit_reason == "stop") & ~closed.same_day_stop & ~closed.index.isin(used)]
    for idx in ko.sort_values(["hold_days", "entry_date"]).index[:2]:
        take(idx, "KO 실패 돌파")
    out = tab.loc[[i for i, _ in chosen]].copy()
    out["category"] = [c for _, c in chosen]
    return out


def one_line(r) -> str:
    gap = " (갭 상승)" if r.gap_up_entry else ""
    xg = " (갭 청산)" if r.gap_exit else (" (진입 당일)" if r.same_day_stop else "")
    stop = f"{r.initial_stop:.2f}" if r.initial_stop else "없음"
    return (f"{r.ticker} | 신호일 {r.signal_date} | 돌파 수준 {r.breakout_level:.2f} | 체결가 {r.entry_price:.2f}{gap} (진입일 {r.entry_date}) | "
            f"초기 손절선 {stop} | 청산가 {r.exit_price:.2f}{xg} (청산일 {r.exit_date}) | 청산 사유 {r.exit_reason} | r {r.r_multiple:+.3f}")


def one_line_vendor(r) -> str:
    stop = f"{r.stop_vendor:.2f}" if r.stop_vendor else "없음"
    return (f"  공급자 종가 기준(분할 보정, 배당 미보정; 일반 차트와 비교용): 돌파 수준 {r.level_vendor:.2f} | 체결가 {r.entry_vendor:.2f} | "
            f"초기 손절선 {stop} | 청산가 {r.exit_vendor:.2f}")


def draw_chart(r, df: pd.DataFrame, strat: B1Turtle, path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Malgun Gothic", "AppleGothic", "NanumGothic"):
        if cand in names:
            matplotlib.rcParams["font.family"] = cand
            break
    matplotlib.rcParams["axes.unicode_minus"] = False
    si, ei, xi = int(r.signal_idx), int(r.entry_idx), int(r.exit_idx)
    i0, i1 = max(0, si - 25), min(len(df) - 1, xi + 8)
    xs = np.arange(i0, i1 + 1)
    d = df.iloc[i0: i1 + 1]
    lows = df["low"].values; hi = df["high"].values
    stop0 = r.initial_stop
    # 청산선(적용 중인 손절선): 진입일은 2N 손절, 이후 봉 k는 max(2N 손절, 직전 20봉 최저 저가)
    line = {}
    for k in range(ei, xi + 1):
        if k == ei:
            line[k] = stop0
        else:
            lo20 = lows[k - strat.exit_n: k].min() if k - strat.exit_n >= 0 else None
            vals = [v for v in (stop0, lo20) if v is not None]
            line[k] = max(vals) if vals else None
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 7.5), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    ax.vlines(xs, d["low"], d["high"], color="0.45", lw=1)
    ax.hlines(d["open"], xs - 0.3, xs, color="0.45", lw=1)
    ax.hlines(d["close"], xs, xs + 0.3, color="0.45", lw=1)
    ax.hlines(r.breakout_level, si, ei, colors="tab:blue", linestyles="--", lw=1.4, label=f"돌파 수준(매수 스탑) {r.breakout_level:.2f}")
    ax.plot([ei], [r.entry_price], "^", color="tab:green", ms=11, label=f"체결 {r.entry_price:.2f}")
    ks = [k for k in line if line[k] is not None]
    ax.step(ks, [line[k] for k in ks], where="mid", color="tab:red", lw=1.4, label="적용 중인 손절선 (2N 손절 → 20일 저가)")
    if stop0:
        ax.hlines(stop0, ei, xi, colors="tab:red", linestyles=":", lw=1, label=f"초기 손절 {stop0:.2f}")
    ax.plot([xi], [r.exit_price], "X", color="black", ms=11, label=f"청산 {r.exit_price:.2f} ({r.exit_reason})")
    for k in (si, ei, xi):
        for a in (ax, ax2):
            a.axvline(k, color="0.8", lw=0.8)
    ax2.plot(xs, d["raw_close"], color="tab:orange", lw=1.2)
    ax2.set_ylabel("비수정 종가")
    for ev_date, lab in load_events(r.ticker):
        if ev_date in df.index:
            k = df.index.get_loc(ev_date)
            if i0 <= k <= i1:
                for a in (ax, ax2):
                    a.axvline(k, color="tab:purple", ls="-.", lw=1)
                ax.text(k, ax.get_ylim()[1], lab, color="tab:purple", fontsize=8, va="top", rotation=90)
    step = max(1, len(xs) // 8)
    ax2.set_xticks(xs[::step])
    ax2.set_xticklabels([df.index[k].strftime("%Y-%m-%d") for k in xs[::step]], rotation=30, fontsize=8)
    ax.set_ylabel("수정 가격 (분할·배당 보정)")
    ax.legend(loc="best", fontsize=8)
    import textwrap
    fig.suptitle(chr(10).join(textwrap.wrap(title, 105)), fontsize=8.5)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=110)
    plt.close(fig)


def fmt(x, pct=False):
    if x is None or (isinstance(x, float) and (np.isnan(x))):
        return "정의 불가"
    return f"{x:.1%}" if pct else f"{x:.3f}"


def stats_rows(label: str, trades) -> list[str]:
    out = []
    for inc in (False, True):
        s = metrics.trade_stats(trades, include_open=inc)
        flag = " 30거래 미만: 판정 불가" if s.get("insufficient") else ""
        out.append(f"| {label} | {'포함' if inc else '제외'} | {s['n']} | {fmt(s.get('win_rate'), True)} | {fmt(s.get('avg_r'))} | "
                   f"{fmt(s.get('avg_win_r'))} | {fmt(s.get('avg_loss_r'))} | {fmt(s.get('median_r'))} | {fmt(s.get('profit_factor'))} | "
                   f"{s.get('max_consecutive_losses')} |{flag}")
    return out


def main_five() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "charts").mkdir(exist_ok=True)
    costs = _COSTS
    tabs, all_trades, runs, ref_lines = [], [], {}, []
    tot_n = tot_ok = 0
    for sym in TICKERS:
        df, strat, res = run_symbol(sym, costs)
        runs[sym] = (df, strat, res)
        tab = trade_table(sym, df, strat, res)
        tabs.append(tab)
        all_trades.append(res.with_open_mtm())
        cols = {k: df[k].tolist() for k in ("open", "high", "low", "close")}
        r = ref.run_b1(list(df.index), cols["open"], cols["high"], cols["low"], cols["close"], entry_n=strat.entry_n, exit_n=strat.exit_n,
                       atr_n=strat.atr_n, stop_mult=strat.stop_mult, rate=costs.one_way_rate(), trade_start=strat.trade_start)
        matched, mism = ref.compare(r, res.with_open_mtm())
        tot_n += len(r); tot_ok += matched
        ref_lines.append(f"| {sym} | {len(r)} | {len(res.with_open_mtm())} | {matched} | {len(mism)} |")
    tab = pd.concat(tabs, ignore_index=True)
    tab["review_set_73"] = mark_review_set(tab)
    tab.drop(columns=["signal_idx", "entry_idx", "exit_idx"]).to_csv(OUT / "trades_all.csv", index=False, encoding="utf-8-sig")

    # 사람 대조 15건
    h15 = pick_human_15(tab)
    md = ["# B1 사람 대조용 15건", "", "차트: 위 = 수정 OHLC(분할·배당 보정) + 돌파 수준·체결·손절선·청산, 아래 = 비수정 종가. 파란 점선 = 돌파 수준, 초록 ▲ = 체결, 빨간 계단 = 적용 중인 손절선, 검은 X = 청산, 보라 점선 = 분할·특별배당 사건.", ""]
    rows_csv = []
    for n, (_, r) in enumerate(h15.iterrows(), 1):
        df, strat, _ = runs[r.ticker]
        fn = f"{n:02d}_{r.ticker}_{r.signal_date}_{r.category.replace(' ', '').replace('·', '')}.png"
        title = f"[{n}] {r.category} — " + one_line(r)
        draw_chart(r, df, strat, OUT / "charts" / fn, title)
        md += [f"## {n}. {r.category}", f"![]({'charts/' + fn})", "", one_line(r), "", one_line_vendor(r), ""]
        rows_csv.append({"no": n, "category": r.category, "summary": one_line(r), "summary_vendor_basis": one_line_vendor(r).strip(), "chart": f"charts/{fn}"})
    (OUT / "human_check_15.md").write_text("\n".join(md), encoding="utf-8")
    pd.DataFrame(rows_csv).to_csv(OUT / "human_check_15.csv", index=False, encoding="utf-8-sig")

    # 지표
    lines = ["# B1 기본 지표 (5종목, 2015-01-01 이후 신호, 비용 편도 0.1%, 데이터 시작 후 252봉 안의 신호 제외)", "",
             "r_multiple은 비용 반영 후. 승률 = r > 0, PF = 이익 합 / 손실 합. '기대값'은 평균 R과 같은 값이라 뺐다.",
             "**최대 연속 손실은 거래를 청산일 순으로 정렬해 계산한다**(5종목 합산도 종목별로 이어 붙이지 않고 전체를 청산일 순으로 섞는다). 미청산 거래의 청산일은 마지막 봉 날짜다.",
             "", "| 대상 | 미청산 | 거래 수 | 승률 | 평균 R | 평균 이익 R | 평균 손실 R | 중앙값 R | 비용 후 PF | 최대 연속 손실 | |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for sym, tr in zip(TICKERS, all_trades):
        lines += stats_rows(sym, tr)
    pooled = [t for tr in all_trades for t in tr]   # (ticker, trade_id)로 거래를 구분하므로 그대로 합쳐도 겹치지 않는다
    lines += stats_rows("5종목 합산", pooled)
    n_open = int((tab.status == "open_mtm").sum())
    lines += ["", f"- 미청산 거래(데이터 끝): {n_open}건 (종목별: " + ", ".join(f"{s} {int(((tab.ticker == s) & (tab.status == 'open_mtm')).sum())}" for s in TICKERS) + ")",
              f"- 비수정 종가 10달러 미만에서 진입한 거래 수(신호일 비수정 종가 기준): **{int(tab.raw_lt_10.sum())}건** / 전체 {len(tab)}건 "
              "(종목별: " + ", ".join(f"{s} {int(((tab.ticker == s) & tab.raw_lt_10).sum())}" for s in TICKERS) + ")",
              f"- 진입 당일 손절 {int(tab.same_day_stop.sum())}건, 갭 청산 {int(tab.gap_exit.sum())}건, 갭 상승 진입 {int(tab.gap_up_entry.sum())}건, 분할·특별배당에 걸친 거래 {int(tab.spans_event.sum())}건",
              f"- 사람·CSV 대조 대상(review_set_73): {int(tab.review_set_73.sum())}건",
              f"- **보유 기간에 대형 배당락(배당락 수익률 ≥ 10%)이 낀 거래: {int(tab.large_div_in_trade.sum())}건** (`large_div_in_trade` 열): "
              + (", ".join(f"{r.ticker} 신호일 {r.signal_date} — {r.large_div_detail}" for r in tab[tab.large_div_in_trade].itertuples()) or "없음")]
    (OUT / "metrics.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "reference_match_5tickers.md").write_text(
        "# 참조 구현 대조 (5종목, 전체 이력)\n\n| 종목 | 참조 거래 수 | 엔진 거래 수 | 일치 | 불일치 |\n|---|---|---|---|---|\n" + "\n".join(ref_lines)
        + f"\n\n합계: 일치 {tot_ok} / {tot_n}\n", encoding="utf-8")
    print("\n".join(lines))
    print("참조 대조 합계", tot_ok, "/", tot_n)
    print(h15[["ticker", "category", "signal_date", "r_multiple"]].to_string())


def main_universe() -> None:
    """P1 전 종목: 엔진 vs 참조 구현(워밍업 게이트 252봉 적용). 2013-01-01부터 잘라 비교한다.

    참조 구현은 터틀 원전 N 시드(처음 20일 TR 평균)로 돌린다. 불일치는 허용오차 두 가지로 센다: 엄격(상대 1e-8)과 느슨(상대 1e-4).
    진단용으로 엔진과 같은 N 시드(첫 TR 지수평활)의 참조도 돌려, 남는 차이가 시드 영향인지 확인한다.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    included = json.loads((MANIFEST / "universe_p1.json").read_text(encoding="utf-8"))["included"]
    total = strict = loose = struct = diag = 0
    max_rel = 0.0
    bad = {}
    for k, sym in enumerate(included, 1):
        df = store.load_ohlcv(sym).loc["2013-01-01":]
        strat = B1Turtle()
        res = simulate(df, strat, sym, _COSTS, SPEC_VERSION)
        cols = {c: df[c].tolist() for c in ("open", "high", "low", "close")}
        kw = dict(entry_n=strat.entry_n, exit_n=strat.exit_n, atr_n=strat.atr_n, stop_mult=strat.stop_mult, rate=_COSTS.one_way_rate(),
                  trade_start=strat.trade_start)
        eng = res.with_open_mtm()
        r1 = ref.run_b1(list(df.index), cols["open"], cols["high"], cols["low"], cols["close"], **kw)     # 워밍업 252는 기본값
        r2 = ref.run_b1(list(df.index), cols["open"], cols["high"], cols["low"], cols["close"], n_seed="first_tr", **kw)
        _, x1 = ref.compare(r1, eng)
        _, x_loose = ref.compare(r1, eng, tol=1e-4)
        _, x2 = ref.compare(r2, eng)
        total += len(r1); strict += len(x1); loose += len(x_loose); diag += len(x2)
        struct += sum(1 for x in x_loose if "fields" not in x or any(f in ("entry_date", "exit_date", "reason") for f in x["fields"]))
        for x in x1:
            if x.get("ref") is not None and hasattr(x.get("engine"), "r_multiple"):
                max_rel = max(max_rel, abs(x["ref"]["r"] - x["engine"].r_multiple))
        if x1:
            bad[sym] = len(x1)
        if k % 100 == 0:
            print(k, "symbols,", total, "trades", flush=True)
    text = [f"# 참조 구현 대조 (P1 {len(included)}종목, 2013-01-01 이후 데이터, 신호 2015-01-01 이후, 워밍업 게이트 252봉 적용)", "",
            f"- 비교한 거래: {total}건",
            f"- 참조 구현(터틀 원전 N 시드): 엄격 허용오차(상대 1e-8)에서 일치 {total - strict}건, **불일치 {strict}건**(종목 {len(bad)}개)",
            f"- 같은 비교를 느슨한 허용오차(상대 1e-4)로: **불일치 {loose}건**, 그중 날짜·청산 사유가 다른 구조적 불일치 {struct}건",
            f"- 엄격 허용오차 불일치의 r 최대 절대 차이: {max_rel:.2e}",
            f"- 참조 구현(진단용, 엔진과 같은 N 시드): 엄격 허용오차에서 불일치 **{diag}건**", "",
            "엄격 허용오차 불일치 종목(거래 수): " + ", ".join(f"{s} {n}" for s, n in sorted(bad.items()))]
    (OUT / "reference_match_universe.md").write_text(chr(10).join(text), encoding="utf-8")
    print(chr(10).join(text))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", action="store_true")
    args = ap.parse_args()
    main_universe() if args.universe else main_five()
