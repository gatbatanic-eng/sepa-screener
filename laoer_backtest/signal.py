"""TQQQ 변동성조절(+ 나스닥 50/200일선 추세 규칙) 일일 주문 계산.

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


def compute(pos, closes, price=None, asof=None, index_closes=None):
    """index_closes: 나스닥100 종가 (추세 규칙용, 전일까지). 없거나 trend_rule 이 없으면 추세 규칙 미적용."""
    tv, lb, band = pos.get("target_vol", 0.35), pos.get("lookback", 20), pos.get("band", 0.10)
    r = np.diff(np.log(closes[-lb - 1:]))
    vol = r.std() * math.sqrt(252)
    w_vol = min(1.0, tv / vol) if vol > 0 else 1.0
    trend = None
    if pos.get("trend_rule") == "cross50_200" and index_closes is not None and len(index_closes) >= 200:
        # 백테스트 VolTarget2(mom="cross"): 50일선 < 200일선이면 비중 x trend_k
        ma50, ma200 = index_closes[-50:].mean(), index_closes[-200:].mean()
        trend = dict(ma50=ma50, ma200=ma200, up=ma50 >= ma200, k=float(pos.get("trend_k", 0.5)))
    w = w_vol * (1.0 if trend is None or trend["up"] else trend["k"])
    px = price or closes[-1]
    shares, cash = float(pos["shares"]), float(pos["cash_usd"])
    total = cash + shares * px
    cur = shares * px / total if total > 0 else 0.0
    ramp = _ramp(pos, asof)
    eff = w
    if ramp and w > ramp["start_w"]:
        # 분할 진입: 시작 비중에서 목표까지 주차별로 1/K 씩만 올린다. 매도(위험 축소)는 평소대로.
        eff = ramp["start_w"] + (w - ramp["start_w"]) * ramp["k"] / ramp["K"]
    gap = eff - cur
    qty = 0
    if cur - w > band:
        # 매도(위험 축소)는 분할 진입 중에도 최종 목표 w 기준. 분할 목표(eff)보다 미리 많이 산 것은 팔지 않는다
        qty = -math.floor(min((cur - w) * total / px, shares))
    elif gap > (ramp["min_gap"] if ramp else band):
        qty = math.floor(min(eff * total / px - shares, cash / px))
    # 다음 매매가 생기는 가격대 (보유·현금 고정, 목표비중 w 고정 가정)
    # 비중 s*p/(c+s*p) = w±band → p = c*x/(s*(1-x))
    def trig(x):
        return cash * x / (shares * (1 - x)) if shares > 0 and 0 < x < 1 else float("nan")
    lo = eff - (ramp["min_gap"] if ramp else band)
    return dict(vol=vol, w=w, w_vol=w_vol, trend=trend, eff=eff, ramp=ramp, px=px, total=total, cur=cur, gap=gap, qty=qty,
                buy_below=trig(lo), sell_above=trig(w + band), band=band, tv=tv, lb=lb)


def _ramp(pos, asof):
    """position.yml 의 ramp_start/ramp_weeks 로 분할 진입 진행 상황. 기간이 끝났거나 설정이 없으면 None."""
    if not pos.get("ramp_start") or asof is None:
        return None
    K = int(pos.get("ramp_weeks", 5))
    k = (pd.Timestamp(asof) - pd.Timestamp(pos["ramp_start"])).days // 7 + 1
    if k < 1 or k > K:
        return None
    return dict(k=k, K=K, start_w=float(pos.get("ramp_start_weight", 0.0)),
                min_gap=float(pos.get("ramp_min_gap", 0.02)))


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
        f"- 기준가 ${s['px']:.2f} · 20일 변동성 {s['vol']:.1%} → 최종 목표 비중 **{s['w']:.1%}** (현재 {s['cur']:.1%})",
    ]
    if s["trend"]:
        t = s["trend"]
        state = ("상승 추세 → 비중 그대로" if t["up"]
                 else f"**하락 추세 → 비중 x{t['k']:g}** (변동성 기준 {s['w_vol']:.1%} → {s['w']:.1%})")
        lines.append(f"- 추세: 나스닥 50일선 {t['ma50']:,.0f} / 200일선 {t['ma200']:,.0f} → {state}")
    if s["ramp"]:
        rp = s["ramp"]
        lines.append(f"- 분할 진입 {rp['k']}/{rp['K']}주차: 이번 주까지 맞출 비중 **{s['eff']:.1%}** "
                     f"(시작 {rp['start_w']:.1%} → 목표까지 매주 1/{rp['K']}씩, 변동성이 오르면 목표도 낮아짐)")
    else:
        lines.append(f"- 목표와 차이 {s['gap']:+.1%}p (밴드 ±{s['band']:.0%}p 넘을 때만 매매)")
    lines += [
        f"- 계좌: {pos['shares']}주 + 현금 ${pos['cash_usd']:,.0f} = ${s['total']:,.0f}",
    ]
    if s["qty"]:
        lines.append(f"- 체결 후 position.yml: shares {after_sh}, cash_usd {after_cash:,.0f} (실제 체결가로 고쳐 주세요)")
    else:
        sell = (f"${s['sell_above']:.2f} 위면 매도 구간" if s["sell_above"] < 2 * s["px"]
                else "가격만으로는 매도 신호 없음(변동성이 오르면 매도)")
        lines.append(f"- 참고: 가격이 ${s['buy_below']:.2f} 아래면 매수, {sell} (목표 비중이 그대로일 때)")
    if s["ramp"] and s["qty"] >= 0:
        lines.append("- 이번 주 매수를 했다면 같은 주에는 추가 매수 신호가 거의 나오지 않습니다 (position.yml 갱신 기준).")
    lines += ["", "주문은 다음 거래일 시가(또는 종가 LOC) 근처 체결을 가정한 백테스트 규칙입니다. 투자 권유가 아닙니다."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--price", type=float, help="현재가 (없으면 최근 종가)")
    a = ap.parse_args()
    with open(os.path.join(HERE, "position.yml")) as f:
        pos = yaml.safe_load(f)
    df = D.load("TQQQ")
    try:
        ndx = D.load("NDX")["Close"].loc[:df.index[-1]].to_numpy(float)
    except FileNotFoundError:
        ndx = None
    s = compute(pos, df["Close"].to_numpy(float), a.price, df.index[-1], ndx)
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
