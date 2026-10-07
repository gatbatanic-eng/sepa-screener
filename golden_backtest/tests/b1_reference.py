"""B1 터틀 System 2 참조 구현. 규격 문서(docs/strategy_spec_v1.md B1 항목과 공통 규격)만 보고 순수 파이썬 반복문으로 따로 썼다.

엔진·전략·지표 패키지를 import하지 않는다(독립성). 엔진 결과와 같은지 비교하는 용도의 오라클이다.

규격에서 읽은 규칙
- N = 20일 ATR [원전]. 터틀 N: 처음 20일 TR 평균으로 시작해 N_t = (19 × N_{t-1} + TR_t) / 20. TR = max(H−L, |H−전일C|, |L−전일C|)
  (엔진의 지표는 첫 TR을 시드로 쓰는 지수평활이라 초기 구간 값이 다르다. 영향은 (19/20)^봉수로 사라진다 — 차이가 나면 여기서 설명한다)
- 진입 [원전, M2]: 직전 55일 최고가에 매수 스탑. 신호일 t 마감 후 수준 = 고가[t−54..t] 최대. 다음 봉 j의 고가 ≥ 수준이면 체결가 = max(시가, 수준)
- 초기 손절 [원전]: 진입가 − 2N(N은 신호일 값), 장중 스탑. 1R = 2N
- 청산 [원전]: 직전 20일 최저가 이탈, 장중 스탑. 봉 k의 청산선 = 저가[k−20..k−1] 최소. 손절(2N)과 20일 저가 중 높은 쪽이 먼저 닿는다
- 장중 스탑 체결 [공통 규격]: 시가 ≤ 선이면 시가, 아니면 저가 ≤ 선일 때 선 가격. 진입 당일에도 2N 손절 적용(저가 ≤ 손절가면 손절가 체결)
  진입 당일에는 20일 저가 청산선을 쓰지 않는다(진입 봉 마감 후 정해지는 값)
- 비용 [공통 규격]: 체결가 × 편도 비율을 진입·청산 각각 빼고, r = (청산 − 진입 − 비용) / 1R
- 워밍업 게이트 [임의, 공통]: 데이터 시작 후 252봉 안(봉 번호 0~251)의 신호는 쓰지 않는다
- 종목당 1포지션. 청산한 봉의 종가에서 새 신호를 낼 수 있다(다음 봉 진입). 마지막 봉의 신호는 진입하지 않는다
- 데이터 끝 미청산은 마지막 종가로 평가한다(청산 비용 포함)
"""
from __future__ import annotations


def turtle_n(h, l, c, period, seed="sma"):
    """N 시계열. 값이 없는 구간은 None.
    seed="sma": 처음 period일 TR 평균으로 시작(터틀 원전, 기본). seed="first_tr": 첫 TR로 시작하는 지수평활(진단용 — 시드 차이가
    엔진과의 불일치 원인인지 확인할 때만 쓴다)."""
    n = len(c)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    out = [None] * n
    if n < period:
        return out
    if seed == "first_tr":
        cur = tr[0]
        for i in range(1, period):
            cur = ((period - 1) * cur + tr[i]) / period
    else:
        cur = sum(tr[:period]) / period
    out[period - 1] = cur
    for i in range(period, n):
        cur = ((period - 1) * cur + tr[i]) / period
        out[i] = cur
    return out


def run_b1(dates, o, h, l, c, *, entry_n=55, exit_n=20, atr_n=20, stop_mult=2.0, rate=0.001, trade_start=None, n_seed="sma", warmup_bars=252):
    """dates: 정렬된 날짜 목록(비교 가능한 값). 반환: 거래 목록(dict)."""
    n = len(c)
    N = turtle_n(h, l, c, atr_n, n_seed)
    trades = []
    t = 0
    while t < n - 1:
        if t < warmup_bars:                      # 워밍업 게이트 [임의]: 데이터 시작 후 252봉 안의 신호는 쓰지 않는다
            t += 1
            continue
        if trade_start is not None and dates[t] < trade_start:
            t += 1
            continue
        if t < entry_n - 1 or N[t] is None:
            t += 1
            continue
        level = max(h[t - entry_n + 1: t + 1])
        j = t + 1
        if h[j] < level:
            t += 1
            continue
        fill = max(o[j], level)
        risk = stop_mult * N[t]
        stop0 = fill - risk if fill - risk > 0 else None      # 손절가가 0 이하면 닿을 수 없다: 손절 없이 R = 2N
        ex = None
        if stop0 is not None and l[j] <= stop0:
            ex = (j, stop0, "stop")
        else:
            for k in range(j + 1, n):
                low_line = min(l[k - exit_n: k]) if k - exit_n >= 0 else None
                candidates = [x for x in (stop0, low_line) if x is not None]
                if not candidates:
                    continue
                line = max(candidates)
                reason = "rule" if (low_line is not None and (stop0 is None or low_line > stop0)) else "stop"
                if o[k] <= line:
                    ex = (k, o[k], reason)
                    break
                if l[k] <= line:
                    ex = (k, line, reason)
                    break
        if ex is None:
            ex = (n - 1, c[n - 1], "open_mtm")
        k, px, reason = ex
        r = (px - fill - fill * rate - px * rate) / risk
        trades.append({"signal_idx": t, "entry_idx": j, "entry_date": dates[j], "entry_price": fill, "stop0": stop0, "risk": risk,
                       "exit_idx": k, "exit_date": dates[k], "exit_price": px, "reason": reason, "r": r, "hold_days": k - j})
        if reason == "open_mtm":
            break
        t = k            # 청산한 봉의 종가에서 새 신호를 낼 수 있다
    return trades


TOL_ENTRY_PRICE = 1e-9    # 진입 체결가(돌파 수준 또는 시가): N에 의존하지 않으므로 사실상 완전 일치
TOL_EXIT_PRICE = 1e-6     # 청산 체결가: 초기 2N 손절가는 N에 의존해 N 시드 잔차(상대 1e-6대)가 남는다
TOL_R = 1e-4              # r 절대 차이


def compare(ref: list[dict], engine_trades, tol_r: float = TOL_R, tol_entry_price: float = TOL_ENTRY_PRICE,
            tol_exit_price: float = TOL_EXIT_PRICE):
    """엔진 거래(Trade, 단일 트랜치)와 참조 거래를 순서대로 비교한다. 반환: (일치 건수, 불일치 목록).

    공식 일치 기준: 진입일·청산일·청산 사유 일치 + 진입 체결가 상대 오차 ≤ tol_entry_price + 청산 체결가 상대 오차 ≤ tol_exit_price
    + r 절대 차이 ≤ tol_r. 하나라도 벗어나면 불일치.
    """
    mism = []
    matched = 0
    for i in range(max(len(ref), len(engine_trades))):
        if i >= len(ref) or i >= len(engine_trades):
            mism.append({"index": i, "problem": "거래 수 다름", "ref": ref[i] if i < len(ref) else None,
                         "engine": engine_trades[i] if i < len(engine_trades) else None})
            continue
        a, e = ref[i], engine_trades[i]
        diffs = []
        if a["entry_date"] != e.entry_date: diffs.append("entry_date")
        if a["exit_date"] != e.exit_date: diffs.append("exit_date")
        if a["reason"] != e.exit_reason: diffs.append("reason")
        if abs(a["entry_price"] - e.entry_price) > tol_entry_price * max(1.0, abs(a["entry_price"])): diffs.append("entry_price")
        if abs(a["exit_price"] - e.exit_price) > tol_exit_price * max(1.0, abs(a["exit_price"])): diffs.append("exit_price")
        if abs(a["r"] - e.r_multiple) > tol_r: diffs.append("r")
        if diffs:
            mism.append({"index": i, "fields": diffs, "ref": a, "engine": e})
        else:
            matched += 1
    return matched, mism
