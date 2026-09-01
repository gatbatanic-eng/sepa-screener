"""추적 지수 빌더 — BUY 신호 종목으로 구성한 '나만의 스크리너 인덱스'.

매 실행마다 지수값을 시계열로 기록해 시간에 따른 성과를 추적한다(순방향, 백테스트 아님).

규칙 (요약, 자세히는 README.md):
  - 구성종목: 최신 screening_result.csv 의 signal=='BUY' 를 composite_score 내림차순
    상위 N개(기본 20). BUY 가 N개 미만이면 있는 만큼. WATCH 로 채우지 않는다.
  - 동일가중(equal weight).
  - 리밸런싱: 기본 monthly(월이 바뀐 뒤 첫 실행). weekly 선택 가능.
  - 첫 실행: 무조건 리밸런싱, 기준값 1000.0.
  - 리밸런싱 시 BUY 가 0개면: 경고만 남기고 기존 바스켓 유지.

지수값:
  - 비(非)리밸런싱: index = last_rebalance_index_value * (1 + 평균수익률)
    평균수익률 = mean(현재가/entry_price - 1)  (동일가중)
  - 리밸런싱: 먼저 '기존' 바스켓 기준으로 오늘자 지수값을 계산해 히스토리에 기록
    (리밸런싱 직전 마감값) → 새 바스켓으로 교체, entry_price 재설정 →
    last_rebalance_index_value 를 방금 계산한 값으로 갱신.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

BASE_VALUE = 1000.0
DEFAULT_TOP_N = 20
STATE_PATH = "output/index_state.json"
HISTORY_PATH = "output/index_history.csv"
SCREENING_CSV = "output/screening_result.csv"


# ---------------------------------------------------------------------------
# 상태 입출력
# ---------------------------------------------------------------------------
def load_state(path: str | Path) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 날짜 / 리밸런싱 스케줄
# ---------------------------------------------------------------------------
def _next_rebalance_date(anchor: date, freq: str) -> date:
    if freq == "weekly":
        return anchor + timedelta(days=7 - anchor.weekday() or 7)
    # monthly: 다음 달 1일
    if anchor.month == 12:
        return date(anchor.year + 1, 1, 1)
    return date(anchor.year, anchor.month + 1, 1)


def _is_rebalance_day(state: dict | None, today: date) -> bool:
    if state is None:
        return True
    nxt = state.get("next_rebalance_date")
    if not nxt:
        return True
    return today >= date.fromisoformat(nxt)


# ---------------------------------------------------------------------------
# 지수값 계산
# ---------------------------------------------------------------------------
def compute_index_value(state: dict, prices: dict[str, float]) -> float:
    """현재 보유 구성종목 기준 오늘자 지수값."""
    holdings = state.get("holdings", [])
    base = float(state.get("last_rebalance_index_value", BASE_VALUE))
    rets = []
    for h in holdings:
        sym = h["symbol"]
        p = prices.get(sym)
        ep = h.get("entry_price")
        if p is None or ep in (None, 0) or pd.isna(p) or pd.isna(ep):
            continue
        rets.append(p / ep - 1.0)
    if not rets:
        return base
    avg = sum(rets) / len(rets)
    return base * (1.0 + avg)


# ---------------------------------------------------------------------------
# 구성종목 선정
# ---------------------------------------------------------------------------
def select_constituents(screening_df: pd.DataFrame, top_n: int) -> list[str]:
    buys = screening_df[screening_df["signal"] == "BUY"].copy()
    if buys.empty:
        return []
    buys = buys.sort_values("composite_score", ascending=False)
    return buys["symbol"].astype(str).head(top_n).tolist()


# ---------------------------------------------------------------------------
# 메인 로직
# ---------------------------------------------------------------------------
def step(
    screening_df: pd.DataFrame,
    state: dict | None,
    price_lookup,
    *,
    today: date,
    top_n: int = DEFAULT_TOP_N,
    freq: str = "monthly",
) -> tuple[dict, dict]:
    """한 번의 실행. (new_state, history_row) 반환. 파일 입출력은 하지 않는다.

    price_lookup: Callable[[list[str]], dict[str, float]]
    """
    today_s = today.isoformat()
    rebalance = _is_rebalance_day(state, today)

    candidates = select_constituents(screening_df, top_n)

    # 가격이 필요한 심볼 = 기존 보유 + 신규 후보
    held_syms = [h["symbol"] for h in state["holdings"]] if state else []
    prices = price_lookup(sorted(set(held_syms) | set(candidates)))

    if state is None:
        # --- 첫 실행: 무조건 리밸런싱, 기준값 1000 ---
        idx_value = BASE_VALUE
        if candidates:
            holdings = _new_holdings(candidates, prices, today_s)
            note = f"첫 실행: {len(holdings)}종목으로 지수 개시"
        else:
            holdings = []
            note = "첫 실행이나 BUY 신호 0개 — 빈 바스켓으로 개시(다음 실행에서 재시도)"
        new_state = {
            "base_value": BASE_VALUE,
            "base_date": today_s,
            "last_rebalance_date": today_s,
            "next_rebalance_date": _next_rebalance_date(today, freq).isoformat(),
            "last_rebalance_index_value": BASE_VALUE,
            "freq": freq,
            "top_n": top_n,
            "holdings": holdings,
        }
        row = _history_row(today_s, idx_value, len(holdings), True)
        new_state["last_value"] = idx_value
        new_state["last_date"] = today_s
        return new_state, {**row, "_note": note}

    # --- 이후 실행 ---
    new_state = dict(state)

    if not rebalance:
        idx_value = compute_index_value(state, prices)
        row = _history_row(today_s, idx_value, len(state["holdings"]), False)
        new_state["last_value"] = idx_value
        new_state["last_date"] = today_s
        return new_state, {**row, "_note": "비리밸런싱: 기존 바스켓 평가"}

    # --- 리밸런싱 실행 ---
    # 1) 기존 바스켓 기준 오늘자 지수값(= 리밸런싱 직전 마감값) 계산·기록
    close_value = compute_index_value(state, prices)
    row = _history_row(today_s, close_value, len(state["holdings"]), True)

    # 2) 새 바스켓으로 교체
    if not candidates:
        note = "리밸런싱 시점이나 BUY 신호 0개 — 기존 바스켓 유지"
        new_state["last_value"] = close_value
        new_state["last_date"] = today_s
        # next_rebalance_date 는 갱신해 다음 주기에 다시 시도
        new_state["next_rebalance_date"] = _next_rebalance_date(today, freq).isoformat()
        return new_state, {**row, "_note": note}

    new_state["holdings"] = _new_holdings(candidates, prices, today_s)
    new_state["last_rebalance_date"] = today_s
    new_state["next_rebalance_date"] = _next_rebalance_date(today, freq).isoformat()
    new_state["last_rebalance_index_value"] = close_value
    new_state["last_value"] = close_value
    new_state["last_date"] = today_s
    new_state["top_n"] = top_n
    new_state["freq"] = freq
    note = f"리밸런싱: {len(new_state['holdings'])}종목으로 교체 (직전 마감 {close_value:.2f})"
    return new_state, {**row, "_note": note}


def _new_holdings(symbols: list[str], prices: dict[str, float], today_s: str) -> list[dict]:
    w = round(1.0 / len(symbols), 6)
    out = []
    for s in symbols:
        p = prices.get(s)
        out.append({
            "symbol": s,
            "weight": w,
            "entry_price": None if p is None or pd.isna(p) else float(p),
            "entry_date": today_s,
        })
    return out


def _history_row(d: str, value: float, n: int, is_rebal: bool) -> dict:
    return {
        "date": d,
        "index_value": round(value, 4),
        "num_constituents": n,
        "is_rebalance_day": str(bool(is_rebal)).lower(),
    }


def append_history(path: str | Path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {k: v for k, v in row.items() if not k.startswith("_")}
    df_row = pd.DataFrame([row])
    if p.exists():
        prev = pd.read_csv(p)
        prev = prev[prev["date"] != row["date"]]  # 같은 날 재실행 시 덮어쓰기
        out = pd.concat([prev, df_row], ignore_index=True)
    else:
        out = df_row
    out.to_csv(p, index=False)


# ---------------------------------------------------------------------------
# 가격 조회 (실데이터)
# ---------------------------------------------------------------------------
def make_price_lookup(screening_df: pd.DataFrame):
    """screening_result.csv 의 price 를 1차 사용, 없으면 라이브 조회."""
    csv_prices = {
        str(r.symbol): float(r.price)
        for r in screening_df.itertuples(index=False)
        if pd.notna(r.price)
    }

    def lookup(symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        missing = []
        for s in symbols:
            if s in csv_prices:
                out[s] = csv_prices[s]
            else:
                missing.append(s)
        for s in missing:
            p = _live_price(s)
            if p is not None:
                out[s] = p
            else:
                print(f"  [경고] {s} 현재가 조회 실패 — 지수 계산에서 제외")
        return out

    return lookup


def _live_price(symbol: str) -> float | None:
    try:
        if symbol.isdigit():
            import FinanceDataReader as fdr
            df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"))
            return float(df["Close"].dropna().iloc[-1])
        import yfinance as yf
        h = yf.Ticker(symbol).history(period="5d")
        return float(h["Close"].dropna().iloc[-1])
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def run(args) -> dict:
    src = Path(args.screening_csv)
    if not src.exists():
        raise SystemExit(f"입력 파일이 없습니다: {src}  (먼저 screener.main 실행)")
    screening_df = pd.read_csv(src, dtype={"symbol": str})

    state = load_state(args.state)
    today = date.today() if not args.date else date.fromisoformat(args.date)

    new_state, row = step(
        screening_df, state, make_price_lookup(screening_df),
        today=today, top_n=args.top_n, freq=args.rebalance,
    )

    append_history(args.history, row)
    save_state(args.state, new_state)

    _print_run(new_state, row, args.history)
    return new_state


def _print_run(state: dict, row: dict, history_path: str) -> None:
    hist = pd.read_csv(history_path)
    prev_val = hist["index_value"].iloc[-2] if len(hist) >= 2 else state["base_value"]
    cur = row["index_value"]
    chg = (cur / prev_val - 1.0) * 100.0 if prev_val else 0.0

    print("\n" + "=" * 60)
    print(f"스크리너 인덱스  {row['date']}")
    print("=" * 60)
    print(f"  지수값      : {cur:,.2f}   (직전 대비 {chg:+.2f}%)")
    print(f"  기준값      : {state['base_value']:,.2f} ({state['base_date']})")
    print(f"  구성종목 수 : {row['num_constituents']}")
    print(f"  리밸런싱일  : {'예' if row['is_rebalance_day'] == 'true' else '아니오'}"
          f"  (다음: {state['next_rebalance_date']})")
    print(f"  비고        : {row.get('_note', '')}")
    print("\n  구성종목:")
    for h in state["holdings"]:
        ep = h["entry_price"]
        print(f"    {h['symbol']:<8} w={h['weight']:.4f}  entry={ep if ep is None else f'{ep:,.2f}'}"
              f" ({h['entry_date']})")
    if not state["holdings"]:
        print("    (없음)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="추적 지수(스크리너 인덱스) 빌더")
    p.add_argument("--market", default="all", help="참고용 라벨 (수집은 screener.main 이 담당)")
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    p.add_argument("--rebalance", choices=["monthly", "weekly"], default="monthly")
    p.add_argument("--screening-csv", default=SCREENING_CSV)
    p.add_argument("--state", default=STATE_PATH)
    p.add_argument("--history", default=HISTORY_PATH)
    p.add_argument("--date", default=None, help="기준일 오버라이드(YYYY-MM-DD, 테스트/재현용)")
    return p


def main(argv: list[str] | None = None) -> int:
    run(build_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
