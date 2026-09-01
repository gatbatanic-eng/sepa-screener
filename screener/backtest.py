"""근사 백테스트 — Trending Value–Quality 전략을 과거 구간에 적용해 본다.

⚠️  이것은 "대략적인" 백테스트다. 무료 데이터의 한계로 다음을 감수한다:
  1. 생존편향 — 유니버스가 '현재' 상장/구성 종목이다. 과거 상장폐지·편출 종목이 빠져 있어
     결과가 실제보다 좋게 나오는 경향이 있다.
  2. 재무지표는 '연간'이고 최근 3~4년치만 얻을 수 있다 (한국: 네이버, 미국: yfinance).
     각 리밸런싱 시점에는 그 시점까지 '공시되었을' 직전 회계연도 값을 쓴다(보고 지연 반영).
     따라서 유효 백테스트 구간은 사실상 2~3년으로 짧다 — 통계적으로 결정적이지 않다.
  3. 환율 미반영 — 각 종목 수익률을 현지통화 기준 %로 보고 동일가중 평균한다.
  4. 거래비용·세금·슬리피지·배당 재투자 세부는 무시(이론치).

제대로 된 검증은 STRATEGY.md 의 학술 백테스트와, 지금부터 순방향으로 쌓이는
screener.index_builder 의 실전 지수를 신뢰하라.

실행:
  python -m screener.backtest --start 2023-01-01 --freq monthly --top-n 20
  python -m screener.backtest --start 2024-01-01 --end 2026-06-30 --freq quarterly --refresh
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import factors, signals

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "output" / "backtest"
CACHE_DIR = OUT_DIR / "cache"

BASE_VALUE = 1000.0
TRADING_6M = 126
TRADING_12M = 252
KR_REPORT_LAG_DAYS = 100   # 사업보고서: 회계연도 종료 후 ~90일
US_REPORT_LAG_DAYS = 75    # 10-K: ~60일

_NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1",
    "Referer": "https://m.stock.naver.com/",
}
_EXCLUDE = ("스팩", "리츠")


# ===========================================================================
# 유니버스
# ===========================================================================
def universe_kr(n: int, min_marcap: float) -> list[tuple[str, str, str]]:
    import FinanceDataReader as fdr
    lst = fdr.StockListing("KRX")
    lst = lst[lst["Market"].isin(["KOSPI", "KOSDAQ"])].dropna(subset=["Marcap", "Code", "Name"])
    lst = lst[lst["Marcap"] >= min_marcap]
    lst = lst[lst["Code"].astype(str).str.endswith("0")]
    lst = lst[~lst["Name"].str.contains("|".join(_EXCLUDE))]
    lst = lst.sort_values("Marcap", ascending=False).head(n)
    return [(str(r.Code), str(r.Name), str(r.Market)) for r in lst.itertuples(index=False)]


def universe_us(n: int) -> list[tuple[str, str, str]]:
    from .data_us import _sp500_universe
    uni = _sp500_universe().head(n)
    return [(str(r.symbol), str(r.name), "US") for r in uni.itertuples(index=False)]


# ===========================================================================
# 가격 이력 (캐시)
# ===========================================================================
def _price_cache_path(market: str) -> Path:
    return CACHE_DIR / f"prices_{market}.pkl"


def fetch_prices(symbols: list[str], market: str, start: date, refresh: bool, verbose: bool) -> dict[str, pd.Series]:
    path = _price_cache_path(market)
    cache: dict[str, pd.Series] = {}
    if path.exists() and not refresh:
        cache = pd.read_pickle(path)
    # 캐시는 어떤 --start 에도 재사용되도록 고정 하한(6년 전)부터 받아둔다
    floor = date(date.today().year - 6, 1, 1)
    need_from = min(start - timedelta(days=430), floor)
    todo = [s for s in symbols
            if s not in cache or cache[s].empty or cache[s].index.min().date() > need_from + timedelta(days=20)]
    if todo:
        if verbose:
            print(f"[{market}] 가격 이력 조회 {len(todo)}종목 (캐시 {len(cache)})...")
        buf_start = need_from.strftime("%Y-%m-%d")
        if market == "US":
            import yfinance as yf
            for i, s in enumerate(todo, 1):
                try:
                    h = yf.Ticker(s).history(start=buf_start, auto_adjust=True)
                    cache[s] = h["Close"].dropna().astype(float).tz_localize(None)
                except Exception as e:  # noqa: BLE001
                    if verbose:
                        print(f"  [{s}] 실패: {e}")
                if verbose and i % 25 == 0:
                    print(f"  ...{i}/{len(todo)}")
        else:
            import FinanceDataReader as fdr
            for i, s in enumerate(todo, 1):
                try:
                    h = fdr.DataReader(s, buf_start)
                    cache[s] = h["Close"].dropna().astype(float)
                except Exception as e:  # noqa: BLE001
                    if verbose:
                        print(f"  [{s}] 실패: {e}")
                if verbose and i % 50 == 0:
                    print(f"  ...{i}/{len(todo)}")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(cache, path)
    return {s: cache[s] for s in symbols if s in cache and not cache[s].empty}


# ===========================================================================
# 연간 재무 (캐시)
# ===========================================================================
def _annual_cache_path(market: str) -> Path:
    return CACHE_DIR / f"annual_{market}.json"


def fetch_annual(symbols: list[str], market: str, refresh: bool, verbose: bool) -> dict[str, dict]:
    path = _annual_cache_path(market)
    cache: dict[str, dict] = {}
    if path.exists() and not refresh:
        cache = json.loads(path.read_text(encoding="utf-8"))
    todo = [s for s in symbols if s not in cache]
    if todo:
        if verbose:
            print(f"[{market}] 연간 재무 조회 {len(todo)}종목 (캐시 {len(cache)})...")
        getter = _annual_kr if market != "US" else _annual_us
        for i, s in enumerate(todo, 1):
            try:
                cache[s] = getter(s)
            except Exception as e:  # noqa: BLE001
                cache[s] = {}
                if verbose:
                    print(f"  [{s}] 재무 실패: {e}")
            if verbose and i % 25 == 0:
                print(f"  ...{i}/{len(todo)}")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {s: cache.get(s, {}) for s in symbols}


def _num(v) -> float:
    if v is None:
        return np.nan
    s = str(v).strip().replace(",", "").replace("배", "").replace("원", "").replace("%", "")
    if s in ("", "-", "N/A"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def _annual_kr(code: str) -> dict:
    """{ 'YYYY-12-31': {roe, d2e, eps, bps, net_income, dps} }  (네이버)"""
    import requests
    r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/finance/annual",
                     headers=_NAVER_HEADERS, timeout=12)
    fin = r.json()["financeInfo"]
    keys = [(c["key"], c.get("isConsensus")) for c in fin["trTitleList"]]
    rowmap = {row["title"]: row["columns"] for row in fin["rowList"]}

    def pick(title, key):
        cell = rowmap.get(title, {}).get(key)
        return _num(cell.get("value") if isinstance(cell, dict) else cell)

    out = {}
    for key, cons in keys:
        if cons == "Y":
            continue
        fy_end = f"{key[:4]}-{key[4:6]}-28"  # 월말 근사(비교용이라 일자는 무해)
        out[fy_end] = {
            "roe": pick("ROE", key),
            "d2e": pick("부채비율", key),
            "eps": pick("EPS", key),
            "bps": pick("BPS", key),
            "net_income": pick("당기순이익", key),
            "dps": pick("주당배당금", key),
        }
    return out


def _annual_us(ticker: str) -> dict:
    import yfinance as yf
    t = yf.Ticker(ticker)
    inc, bs = t.income_stmt, t.balance_sheet
    div = t.dividends
    div = div.tz_localize(None) if getattr(div.index, "tz", None) is not None else div

    def row(df, *names):
        for n in names:
            if n in df.index:
                return df.loc[n]
        return pd.Series(dtype=float)

    ni = row(inc, "Net Income", "Net Income Common Stockholders")
    eps = row(inc, "Diluted EPS", "Basic EPS")
    eq = row(bs, "Stockholders Equity", "Common Stock Equity")
    debt = row(bs, "Total Debt")
    sh = row(bs, "Ordinary Shares Number", "Share Issued")

    out = {}
    for col in inc.columns:
        fy = col.date().isoformat()
        n = float(ni.get(col, np.nan)) if col in ni.index else np.nan
        e = float(eq.get(col, np.nan)) if col in eq.index else np.nan
        d = float(debt.get(col, np.nan)) if col in debt.index else np.nan
        s = float(sh.get(col, np.nan)) if col in sh.index else np.nan
        ep = float(eps.get(col, np.nan)) if col in eps.index else np.nan
        if np.isnan(n) and np.isnan(e):
            continue
        out[fy] = {
            "net_income": n,
            "roe": (n / e * 100.0) if e and not np.isnan(e) and not np.isnan(n) else np.nan,
            "d2e": (d / e * 100.0) if e and not np.isnan(e) and not np.isnan(d) else np.nan,
            "eps": ep,
            "bps": (e / s) if s and not np.isnan(s) and not np.isnan(e) else np.nan,
            "_div_series": None,
        }
    # 배당은 시계열로 별도 저장 (as-of 계산에 필요)
    out["_dividends"] = {d.isoformat(): float(v) for d, v in div.items()}
    return out


# ===========================================================================
# as-of 스키마 행 구성
# ===========================================================================
def _pos(series: pd.Series, t: pd.Timestamp) -> int:
    return int(series.index.searchsorted(t, side="right")) - 1


def _asof_fy(annual: dict, t: date, lag_days: int) -> dict | None:
    cands = []
    for fy_end, vals in annual.items():
        if fy_end.startswith("_"):
            continue
        try:
            fe = date.fromisoformat(fy_end)
        except ValueError:
            continue
        if fe + timedelta(days=lag_days) <= t:
            cands.append((fe, vals))
    if not cands:
        return None
    return max(cands, key=lambda x: x[0])[1]


def build_row(sym: str, name: str, market: str, price_s: pd.Series,
              annual: dict, t: date, relaxed: bool) -> dict | None:
    ts = pd.Timestamp(t)
    pos = _pos(price_s, ts)
    if pos < TRADING_12M:  # 200일선·12개월 모멘텀 불가
        return None
    price = float(price_s.iloc[pos])
    sma200 = float(price_s.iloc[pos - 199: pos + 1].mean())
    ret_6m = price / float(price_s.iloc[pos - TRADING_6M]) - 1.0
    ret_12m = price / float(price_s.iloc[pos - TRADING_12M]) - 1.0

    lag = KR_REPORT_LAG_DAYS if market != "US" else US_REPORT_LAG_DAYS
    fy = _asof_fy(annual, t, lag)
    if fy is None and relaxed:
        actual = [v for k, v in annual.items() if not k.startswith("_")]
        fy = actual[0] if actual else None
    if fy is None:
        return None

    eps, bps = fy.get("eps"), fy.get("bps")
    per = price / eps if eps and eps > 0 else np.nan
    pbr = price / bps if bps and bps > 0 else np.nan

    if market == "US":
        divs = annual.get("_dividends", {})
        ttm = sum(v for d, v in divs.items()
                  if ts - pd.Timedelta(days=365) < pd.Timestamp(d) <= ts)
        sh_yield = (ttm / price) if price else np.nan
    else:
        dps = fy.get("dps")
        sh_yield = (dps / price) if (dps and not np.isnan(dps) and price) else np.nan

    ni = fy.get("net_income")
    return {
        "symbol": sym, "name": name, "market": market,
        "price": price, "sma200": sma200, "ret_6m": ret_6m, "ret_12m": ret_12m,
        "per": per, "pbr": pbr, "ev_ebitda": np.nan,
        "shareholder_yield": sh_yield,
        "roe": fy.get("roe"), "debt_to_equity": fy.get("d2e"),
        "net_income_positive": bool(ni > 0) if (ni is not None and not np.isnan(ni)) else False,
    }


# ===========================================================================
# 리밸런싱 날짜
# ===========================================================================
def rebalance_dates(start: date, end: date, freq: str) -> list[date]:
    out, cur = [], date(start.year, start.month, 1)
    step = 1 if freq == "monthly" else 3
    while cur <= end:
        if cur >= start:
            out.append(cur)
        m = cur.month - 1 + step
        cur = date(cur.year + m // 12, m % 12 + 1, 1)
    if not out or out[-1] < end:
        out.append(end)
    return out


# ===========================================================================
# 엔진
# ===========================================================================
def run_backtest(cfg) -> dict:
    verbose = not cfg.quiet
    start = date.fromisoformat(cfg.start)
    end = date.fromisoformat(cfg.end) if cfg.end else date.today()

    uni = universe_kr(cfg.kr_max_tickers, cfg.kr_min_marcap) if cfg.market in ("kr", "all") else []
    uni += universe_us(cfg.us_max_tickers) if cfg.market in ("us", "all") else []
    if verbose:
        print(f"유니버스 {len(uni)}종목  |  {start} ~ {end}  |  {cfg.freq}, 상위 {cfg.top_n}, "
              f"select={cfg.select}, {'relaxed' if cfg.relaxed else 'strict'}")

    kr_syms = [s for s, _, m in uni if m != "US"]
    us_syms = [s for s, _, m in uni if m == "US"]
    prices: dict[str, pd.Series] = {}
    annual: dict[str, dict] = {}
    if kr_syms:
        prices |= fetch_prices(kr_syms, "KR", start, cfg.refresh, verbose)
        annual |= fetch_annual(kr_syms, "KR", cfg.refresh, verbose)
    if us_syms:
        prices |= fetch_prices(us_syms, "US", start, cfg.refresh, verbose)
        annual |= fetch_annual(us_syms, "US", cfg.refresh, verbose)

    name_map = {s: n for s, n, _ in uni}
    mkt_map = {s: m for s, m, _ in uni}
    uni = [(s, n, m) for s, n, m in uni if s in prices]

    rdates = rebalance_dates(start, end, cfg.freq)
    all_days = _calendar(prices, start, end)

    def selector(t: date):
        return _select(uni, prices, annual, mkt_map, name_map, t,
                       cfg.top_n, cfg.relaxed, cfg.min_coverage, cfg.select)

    eq, log = simulate(rdates, all_days, prices, selector)
    bench = _benchmarks(cfg.market, start, end, eq)

    result = {
        "equity": eq, "benchmarks": bench, "log": log,
        "metrics": _metrics(eq, cfg.freq),
        "bench_metrics": {k: _metrics(v, cfg.freq) for k, v in bench.items()},
        "start": start.isoformat(), "end": end.isoformat(),
        "freq": cfg.freq, "top_n": cfg.top_n, "universe": len(uni), "select": cfg.select,
        "effective_start": eq.index[0].date().isoformat() if len(eq) else None,
    }
    _write_outputs(result, cfg)
    _print_summary(result)
    return result


def simulate(rdates: list[date], all_days: list[pd.Timestamp],
             prices: dict[str, pd.Series], selector) -> tuple[pd.Series, list[dict]]:
    """지수값 시계열을 만든다. 순수 로직 — 네트워크 없음, 테스트 대상.

    selector(t) -> (picks: list[str] | None, note: str)
      picks 가 None 이면 기존 바스켓 유지(리밸런싱 건너뜀).
    매 구간 [t, t_next) 을 일별 마크투마켓, 구간말 지수값을 다음 구간으로 이월.
    리밸런싱 시 현재 보유 종목의 entry_price 를 그 시점 가격으로 재설정한다.
    """
    equity = BASE_VALUE
    curve: dict[pd.Timestamp, float] = {}
    holdings: list[str] = []
    log: list[dict] = []

    for i, t in enumerate(rdates[:-1]):
        t_next = rdates[i + 1]
        picks, note = selector(t)
        if picks is not None:
            holdings = picks

        entry = {s: _price_at(prices[s], t) for s in holdings
                 if s in prices and _price_at(prices[s], t) is not None}
        held = [s for s in holdings if s in entry]

        seg = [d for d in all_days if pd.Timestamp(t) <= d < pd.Timestamp(t_next)]
        seg_start = equity
        for d in seg:
            if held:
                rr = [(_price_at(prices[s], d.date()) or entry[s]) / entry[s] - 1.0 for s in held]
                curve[d] = seg_start * (1.0 + float(np.mean(rr)))
            else:
                curve[d] = seg_start
        if seg:
            equity = curve[seg[-1]]
        log.append({
            "date": t.isoformat(), "index_value": round(seg_start, 2),
            "num_holdings": len(held), "picks": ",".join(held),
            "note": note or ("" if picks is not None else "기존 유지"),
        })

    eq = pd.Series(curve).sort_index()
    return eq[~eq.index.duplicated(keep="last")], log


def _calendar(prices: dict[str, pd.Series], start: date, end: date) -> list[pd.Timestamp]:
    idx = pd.DatetimeIndex([])
    for s in list(prices.values())[:40]:
        idx = idx.union(s.index)
    idx = idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
    return list(idx)


def _price_at(series: pd.Series, d: date):
    pos = _pos(series, pd.Timestamp(d))
    if pos < 0:
        return None
    return float(series.iloc[pos])


def _select(uni, prices, annual, mkt_map, name_map, t, top_n, relaxed, min_cov, mode="rank"):
    per_market: dict[str, list[dict]] = {}
    for s, n, m in uni:
        row = build_row(s, n, m, prices[s], annual.get(s, {}), t, relaxed)
        if row:
            per_market.setdefault(m if m == "US" else "KR", []).append(row)
    frames = []
    total_rows = 0
    for rows in per_market.values():
        total_rows += len(rows)
        frames.append(factors.compute_factor_scores(pd.DataFrame(rows)))
    if not frames:
        return None, "데이터 없음"
    coverage = total_rows / max(len(uni), 1)
    if coverage < min_cov:
        return None, f"유니버스 커버리지 {coverage:.0%}<{min_cov:.0%} — 리밸런싱 건너뜀"

    scored = pd.concat(frames, ignore_index=True)
    classed = signals.classify(scored)

    if mode == "signal":
        # 라이브 인덱스와 동일: BUY 신호만 (집중도 높음)
        cand = classed[classed["signal"] == "BUY"].sort_values("composite_score", ascending=False)
        if cand.empty:
            return None, "BUY 0개 — 기존 바스켓 유지"
        picks = cand["symbol"].astype(str).head(top_n).tolist()
        return picks, f"BUY {len(cand)}개 중 상위 {len(picks)}"

    # rank 모드(기본): SELL 을 제외한 뒤 composite 상위 N (O'Shaughnessy/Greenblatt 방식,
    # 항상 N종목 분산 — 팩터 모델 자체를 검증)
    cand = classed[classed["signal"] != "SELL"].sort_values("composite_score", ascending=False)
    if cand.empty:
        return None, "편입 후보 없음 — 기존 유지"
    picks = cand["symbol"].astype(str).head(top_n).tolist()
    n_buy = int((classed["signal"] == "BUY").sum())
    return picks, f"상위 {len(picks)} 편입 (BUY {n_buy})"


def _benchmarks(market: str, start: date, end: date, eq: pd.Series) -> dict[str, pd.Series]:
    import FinanceDataReader as fdr
    out: dict[str, pd.Series] = {}
    want = []
    if market in ("kr", "all"):
        want.append(("KOSPI", "KS11"))
    if market in ("us", "all"):
        want.append(("S&P500", "US500"))
    if not len(eq):
        return out
    anchor = eq.index[0]
    for label, code in want:
        try:
            h = fdr.DataReader(code, (start - timedelta(days=10)).strftime("%Y-%m-%d"))["Close"].dropna()
            h = h[(h.index >= anchor) & (h.index <= pd.Timestamp(end))]
            if len(h):
                out[label] = h / float(h.iloc[0]) * BASE_VALUE
        except Exception:  # noqa: BLE001
            pass
    if len(out) == 2:
        a, b = list(out.values())
        j = a.index.intersection(b.index)
        if len(j) >= 2:
            a, b = a.reindex(j), b.reindex(j)
            out["50/50 혼합"] = BASE_VALUE * (0.5 * a / a.iloc[0] + 0.5 * b / b.iloc[0])
    return out


def _metrics(eq: pd.Series, freq: str) -> dict:
    if eq is None:
        return {}
    eq = eq.dropna()
    if len(eq) < 2 or eq.iloc[0] == 0:
        return {}
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    days = (eq.index[-1] - eq.index[0]).days or 1
    years = days / 365.25
    cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1.0) if years > 0 else np.nan
    dret = eq.pct_change().dropna()
    vol = float(dret.std() * np.sqrt(252))
    sharpe = float((dret.mean() * 252) / vol) if vol else np.nan
    dd = eq / eq.cummax() - 1.0
    return {
        "total_return": total, "cagr": cagr, "vol_annual": vol,
        "sharpe": sharpe, "mdd": float(dd.min()), "years": round(years, 2),
        "final": round(float(eq.iloc[-1]), 1),
    }


def _write_outputs(result: dict, cfg) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    eq = result["equity"]
    df = pd.DataFrame({"date": eq.index.date, "strategy": eq.values})
    for label, series in result["benchmarks"].items():
        df[label] = series.reindex(eq.index).ffill().values
    df.to_csv(OUT_DIR / "equity_curve.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(result["log"]).to_csv(OUT_DIR / "rebalance_log.csv", index=False, encoding="utf-8-sig")

    m = result["metrics"]
    lines = [
        f"# 근사 백테스트 리포트 — Trending Value–Quality",
        "",
        f"- 구간: **{result['effective_start']} ~ {result['end']}**  (요청 {result['start']}~, 데이터 가능 시점부터)",
        f"- 선정: `{result['select']}` ({'SELL 제외 composite 상위 N' if result['select']=='rank' else 'BUY 신호만'}) "
        f"· {result['freq']} 리밸런싱 · 상위 {result['top_n']}종목 동일가중 · 유니버스 {result['universe']}종목",
        "",
        "## 성과 요약",
        "",
        "| 지표 | 전략 | " + " | ".join(result["benchmarks"].keys()) + " |",
        "| --- | --- | " + " | ".join("---" for _ in result["benchmarks"]) + " |",
    ]
    bm = result["bench_metrics"]
    def rowline(label, key, pct=True, dp=1):
        cells = [_mfmt(m.get(key), pct, dp)] + [_mfmt(bm[b].get(key), pct, dp) for b in result["benchmarks"]]
        return f"| {label} | " + " | ".join(cells) + " |"
    lines += [
        rowline("총수익률", "total_return"),
        rowline("연환산(CAGR)", "cagr"),
        rowline("연변동성", "vol_annual"),
        rowline("샤프(rf=0)", "sharpe", pct=False, dp=2),
        rowline("최대낙폭(MDD)", "mdd"),
        rowline("최종 지수값", "final", pct=False, dp=1),
        "",
        "## 주의 (반드시 읽을 것)",
        "",
        "- **생존편향**: 유니버스가 현재 상장/구성 종목. 과거 탈락 종목 누락 → 결과가 실제보다 낙관적.",
        "- **재무 연간·최근 3~4년만**: 리밸런싱 시점의 직전 회계연도 값 사용(보고 지연 반영). 유효 구간이 짧다.",
        "- **환율 미반영**, 거래비용·세금·슬리피지 무시. 이론치다.",
        "- 짧은 구간의 백테스트는 통계적으로 결정적이지 않다. 방향 참고용으로만.",
    ]
    (OUT_DIR / "backtest_report.md").write_text("\n".join(lines), encoding="utf-8")


def _mfmt(v, pct=True, dp=1) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{v*100:+.{dp}f}%" if pct else f"{v:.{dp}f}"


def _print_summary(result: dict) -> None:
    m = result["metrics"]
    print("\n" + "=" * 66)
    print(f"근사 백테스트  {result['effective_start']} ~ {result['end']}  "
          f"({result['freq']}, 상위 {result['top_n']}, select={result['select']})")
    print("=" * 66)
    if not m:
        print("  데이터 부족으로 지수를 만들지 못했습니다 (구간을 늦추거나 --relaxed 시도).")
        return
    hdr = f"  {'':12}{'전략':>12}" + "".join(f"{b:>14}" for b in result["benchmarks"])
    print(hdr)
    bm = result["bench_metrics"]
    for label, key, pc, dp in [("총수익률", "total_return", True, 1), ("CAGR", "cagr", True, 1),
                               ("연변동성", "vol_annual", True, 1), ("샤프", "sharpe", False, 2),
                               ("MDD", "mdd", True, 1), ("최종값", "final", False, 1)]:
        row = f"  {label:12}{_mfmt(m.get(key), pc, dp):>12}"
        for b in result["benchmarks"]:
            row += f"{_mfmt(bm[b].get(key), pc, dp):>14}"
        print(row)
    print(f"\n  리밸런싱 {len(result['log'])}회 · 평균 보유 "
          f"{np.mean([r['num_holdings'] for r in result['log']]):.1f}종목")
    print(f"  저장: {OUT_DIR / 'equity_curve.csv'} · {OUT_DIR / 'backtest_report.md'}")


# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Trending Value–Quality 근사 백테스트")
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default=None, help="기본: 오늘")
    p.add_argument("--freq", choices=["monthly", "quarterly"], default="monthly")
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--select", choices=["rank", "signal"], default="rank",
                   help="rank: SELL 제외 composite 상위 N (분산, 팩터 모델 검증·기본) | "
                        "signal: BUY 신호만 (라이브 인덱스와 동일, 집중도 높음)")
    p.add_argument("--market", choices=["kr", "us", "all"], default="all")
    p.add_argument("--kr-max-tickers", type=int, default=200)
    p.add_argument("--us-max-tickers", type=int, default=200)
    p.add_argument("--kr-min-marcap", type=float, default=1_000 * 1e8)
    p.add_argument("--min-coverage", type=float, default=0.4,
                   help="유니버스 중 이 비율 미만이 데이터가 있으면 해당 리밸런싱 건너뜀")
    p.add_argument("--relaxed", action="store_true",
                   help="직전 회계연도 데이터가 없는 시점에 '가장 이른' 연간치로 대체(룩어헤드 감수)")
    p.add_argument("--refresh", action="store_true", help="가격·재무 캐시 무시하고 재조회")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    cfg = build_parser().parse_args(argv)
    if cfg.kr_max_tickers == 0:
        cfg.kr_max_tickers = None
    run_backtest(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
