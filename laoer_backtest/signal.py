"""TQQQ 변동성조절 일일 주문 계산.

  python -m laoer_backtest.signal [--price 현재가]

position.yml(보유 주식·현금)과 data/TQQQ.csv(최근 종가)로 목표 비중과 주문 수량을 계산해
signal/latest.md 에 쓰고 signal/history.csv 에 한 줄 추가한다.
백테스트의 '시가 체결' 변형과 같은 순서: 전일 종가까지로 변동성 계산 → 다음 거래일에 체결.
"""
import argparse
import math
import os

import numpy as np
import pandas as pd
import yaml

from . import data as D

HERE = os.path.dirname(__file__)
SIG = os.path.join(HERE, "signal")


def compute(pos, closes, price=None):
    tv, lb, band = pos.get("target_vol", 0.35), pos.get("lookback", 20), pos.get("band", 0.10)
    r = np.diff(np.log(closes[-lb - 1:]))
    vol = r.std() * math.sqrt(252)
    w = min(1.0, tv / vol) if vol > 0 else 1.0
    px = price or closes[-1]
    shares, cash = float(pos["shares"]), float(pos["cash_usd"])
    total = cash + shares * px
    cur = shares * px / total if total > 0 else 0.0
    gap = w - cur
    qty = 0
    if abs(gap) > band:
        want = w * total / px - shares
        qty = math.floor(min(want, cash / px)) if want > 0 else -math.floor(min(-want, shares))
    # 다음 매매가 생기는 가격대 (보유·현금 고정, 목표비중 w 고정 가정)
    # 비중 s*p/(c+s*p) = w±band → p = c*x/(s*(1-x))
    def trig(x):
        return cash * x / (shares * (1 - x)) if shares > 0 and 0 < x < 1 else float("nan")
    return dict(vol=vol, w=w, px=px, total=total, cur=cur, gap=gap, qty=qty,
                buy_below=trig(w - band), sell_above=trig(w + band), band=band, tv=tv, lb=lb)


def render(s, asof, pos):
    if s["qty"] > 0:
        action = f"**매수 {s['qty']}주** (약 ${s['qty'] * s['px']:,.0f})"
    elif s["qty"] < 0:
        action = f"**매도 {-s['qty']}주** (약 ${-s['qty'] * s['px']:,.0f})"
    else:
        action = "**매매 없음** (목표와 차이가 밴드 이내)"
    after_sh = pos["shares"] + s["qty"]
    after_cash = pos["cash_usd"] - s["qty"] * s["px"]
    lines = [
        f"## TQQQ 변동성조절 신호 — {asof:%Y-%m-%d} 종가 기준",
        "",
        f"- 오늘 할 일: {action}",
        f"- 기준가 ${s['px']:.2f} · 20일 변동성 {s['vol']:.1%} → 목표 비중 **{s['w']:.1%}** "
        f"(현재 {s['cur']:.1%}, 차이 {s['gap']:+.1%}p, 밴드 ±{s['band']:.0%}p)",
        f"- 계좌: {pos['shares']}주 + 현금 ${pos['cash_usd']:,.0f} = ${s['total']:,.0f}",
    ]
    if s["qty"]:
        lines.append(f"- 체결 후 position.yml: shares {after_sh}, cash_usd {after_cash:,.0f} (실제 체결가로 고쳐 주세요)")
    else:
        lines.append(f"- 참고: 가격이 ${s['buy_below']:.2f} 아래면 매수, ${s['sell_above']:.2f} 위면 매도 구간 "
                     f"(목표 비중이 그대로일 때)")
    lines += ["", "주문은 다음 거래일 시가(또는 종가 LOC) 근처 체결을 가정한 백테스트 규칙입니다. 투자 권유가 아닙니다."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--price", type=float, help="현재가 (없으면 최근 종가)")
    a = ap.parse_args()
    with open(os.path.join(HERE, "position.yml")) as f:
        pos = yaml.safe_load(f)
    df = D.load("TQQQ")
    s = compute(pos, df["Close"].to_numpy(float), a.price)
    md = render(s, df.index[-1], pos)
    os.makedirs(SIG, exist_ok=True)
    with open(os.path.join(SIG, "latest.md"), "w") as f:
        f.write(md + "\n")
    hist = os.path.join(SIG, "history.csv")
    row = pd.DataFrame([dict(date=df.index[-1].date(), price=round(s["px"], 2), vol=round(s["vol"], 4),
                             target_w=round(s["w"], 4), cur_w=round(s["cur"], 4), qty=s["qty"],
                             shares=pos["shares"], cash_usd=pos["cash_usd"])])
    if os.path.exists(hist):
        old = pd.read_csv(hist)
        row = pd.concat([old[old.date != str(row.date[0])], row])
    row.to_csv(hist, index=False)
    print(md)


if __name__ == "__main__":
    main()
