"""데이터 품질 점검: 이상치 집계(2014-01-01 이후)와 A1 이력 검사. 데이터를 고치지 않고 기록만 한다."""
from __future__ import annotations

import numpy as np
import pandas as pd


def anomalies_since(df: pd.DataFrame, since: str, big_move: float) -> dict:
    """저장용 프레임(close=완전 수정 종가) 기준 since 이후 이상치.

    - zero_volume: 거래량 0인 봉 수
    - big_moves  : |일간 등락| >= big_move (since 직전 봉 대비 첫 날 포함)
    - ath_affecting: 그 봉의 종가가 그 시점까지의 사상 최고가(데이터 안)를 새로 갈아치운 경우.
      이런 봉은 A1 신호·ATR 손절선에 직접 들어가므로 따로 표시한다. 하락 이상치는 최고가를 만들 수 없어 해당 없음.
    - holds_current_ath: 데이터 끝 시점의 사상 최고가가 이상치 봉에서 만들어진 경우
    - adjustment_artifact: 수정 종가 등락과 공급자 Close 등락이 10%p 넘게 어긋남(v_close 열이 있을 때만) — 보정 오류 의심
    """
    close = df["close"]
    ret = close.pct_change()
    running_prev_max = close.cummax().shift(1)
    mask_since = df.index >= pd.Timestamp(since)
    big = (ret.abs() >= big_move) & mask_since
    sets_ath = big & (close > running_prev_max)
    ath_date = close.idxmax() if len(close) else None
    vret = df["v_close"].pct_change() if "v_close" in df.columns else None
    moves = []
    for d in df.index[big]:
        i = df.index.get_loc(d)
        base = float(df["volume"].iloc[max(0, i - 21):i].median()) if i > 0 else 0.0
        m = {"date": d.date().isoformat(), "ret": round(float(ret[d]), 4),
             "volume_ratio": round(float(df["volume"].iloc[i]) / base, 1) if base > 0 else None,  # 직전 21봉 중앙값 대비
             "sets_new_ath": bool(sets_ath[d]), "holds_current_ath": bool(d == ath_date)}
        if vret is not None:
            # 공급자 Close(분할만 보정) 등락과 수정 종가 등락이 10%p 넘게 다르면 배당·분리상장 보정 오류 의심
            m["vendor_close_ret"] = round(float(vret[d]), 4)
            m["adjustment_artifact"] = bool(abs(ret[d] - vret[d]) > 0.10)
        moves.append(m)
    return {
        "zero_volume": int(((df["volume"] == 0) & mask_since).sum()),
        "big_moves": moves,
    }


def a1_history_check(df: pd.DataFrame, asof: str, first_bars: int, starts_at_floor: bool) -> dict:
    """A1(사상 최고가) 판정 가능 여부.

    UNDETERMINABLE      : 수집 하한(첫 봉이 fetch.start)에서 시작하고, asof까지 데이터의 최고가(완전 수정 종가)가
                          첫 first_bars봉 안에 있다 → 하한 이전 이력이 잘렸을 수 있다
    NO_DATA_BEFORE_ASOF : asof 이전 데이터가 없다(asof 이후 상장). 상장일부터 전체 이력이라 검사 대상이 아니다
    OK                  : 그 외. 상장 직후 최고가여도 하한에서 시작하지 않았다면 이력이 온전하다
    starts_at_floor는 정리·시작 구간 절단 전의 첫 봉으로 판단해 호출자가 넘긴다.
    """
    sub = df.loc[: pd.Timestamp(asof), "close"]
    if sub.empty:
        return {"status": "NO_DATA_BEFORE_ASOF", "starts_at_floor": starts_at_floor}
    pos = int(sub.values.argmax())  # 동률이면 가장 이른 봉
    status = "UNDETERMINABLE" if (starts_at_floor and pos < first_bars) else "OK"
    return {"status": status, "starts_at_floor": starts_at_floor, "ath_pos": pos,
            "ath_date": sub.index[pos].date().isoformat(), "bars_to_asof": int(len(sub))}


def adjustment_artifacts(df: pd.DataFrame, splits: pd.Series | None, threshold: float, split_match_days: int) -> list[dict]:
    """|수정 종가 수익률 − 공급자 Close 수익률| > threshold인 봉 전부(전 구간).

    공급자 Close는 분할만, 수정 종가는 분할+배당을 보정하므로 일반 배당일에는 두 수익률이 거의 같다. 크게 어긋나는 날은
    특별배당·분리상장·보정 오류다. split_event: 분할 이벤트 ex-date가 그 봉 ±split_match_days 달력일 안에 있는가
    (Yahoo는 분리상장을 분할로 기록하기도 한다).
    """
    adj, ven = df["close"].pct_change(), df["v_close"].pct_change()
    hits = (adj - ven).abs() > threshold
    sp = splits if splits is not None else pd.Series(dtype=float)
    out = []
    for d in df.index[hits.values]:
        near = [(x, r) for x, r in sp.items() if abs((d - x).days) <= split_match_days]
        out.append({"date": d.date().isoformat(), "adj_ret": round(float(adj[d]), 4), "vendor_ret": round(float(ven[d]), 4),
                    "split_event": bool(near), "split_ratio": float(near[0][1]) if near else None})
    return out


def adjustment_errors(df: pd.DataFrame, splits: pd.Series | None, divergence: float, min_adj_ret: float,
                      split_match_days: int) -> list[dict]:
    """DHR형 보정 오류: 수정 종가가 공급자 Close보다 훨씬 크게 튀는 봉.

    조건: |수정 수익률| − |공급자 수익률| > divergence 이고 |수정 수익률| > min_adj_ret.
    특별배당·분리상장을 Adj Close가 제대로 보정한 날은 수정 쪽이 매끄럽고 공급자 Close가 크게 떨어지므로(예: TDG 2014-06,
    KDP 2018-07) 이 조건에 걸리지 않는다. 반대로 수정 쪽이 튀는 날(DHR 2016-07-05: 수정 +61%, 공급자 +3.6%)만 걸린다.
    """
    adj, ven = df["close"].pct_change(), df["v_close"].pct_change()
    hits = ((adj.abs() - ven.abs()) > divergence) & (adj.abs() > min_adj_ret)
    sp = splits if splits is not None else pd.Series(dtype=float)
    out = []
    for d in df.index[hits.values]:
        near = [(x, r) for x, r in sp.items() if abs((d - x).days) <= split_match_days]
        out.append({"date": d.date().isoformat(), "adj_ret": round(float(adj[d]), 4), "vendor_ret": round(float(ven[d]), 4),
                    "split_event": bool(near), "split_ratio": float(near[0][1]) if near else None})
    return out


def a1_history_break(df: pd.DataFrame, event_date: str, asof: str) -> dict:
    """서로 다른 법인 이력이 이어 붙은 종목(예: JCI는 Tyco, TMUS는 MetroPCS 이력)의 사상 최고가 판정 가능 시점.

    사건 이전 최고가(수정 종가)가 사건 이후 최고가보다 크면 사건 이후 가격은 이전 이력과 같은 척도가 아닐 수 있어, 매일의 누적
    최고가(A1 신호 기준)가 오염된다. 조건 "사건 이후 최고가 >= 사건 이전 최고가"가 asof까지 충족되면 문제 없음(blocked_until=None).
    아니면 그 조건이 처음 충족되는 날(사건 이후 종가가 사건 이전 최고가 이상이 되는 첫 날)까지 A1 판정 불가이고,
    끝까지 충족되지 않으면 blocked_until="NOT_YET". 해제 조건을 이상(>=)으로 둔 것은 A1 진입 조건(종가 >= 사상 최고가)과 맞추기 위해서다.

    이 차단은 신호를 바꾸지 않는다. 신호가 없는 이유가 데이터 문제임을 표시하는 용도이고, 신호 판정 자체는 A1 전략이 한다.
    """
    t = pd.Timestamp(event_date)
    pre = df.loc[: t - pd.Timedelta(days=1), "close"]
    post = df.loc[t:, "close"]
    if pre.empty or post.empty:
        return {"event_date": event_date, "blocked_until": None, "satisfied_asof": True, "note": "사건 이전 또는 이후 데이터 없음"}
    pre_max = float(pre.max())
    post_asof = post.loc[: pd.Timestamp(asof)]
    post_max_asof = float(post_asof.max()) if len(post_asof) else float("nan")
    satisfied = bool(post_max_asof >= pre_max)
    blocked_until = None
    if not satisfied:
        crossed = post[post >= pre_max]
        blocked_until = crossed.index[0].date().isoformat() if len(crossed) else "NOT_YET"
    return {"event_date": event_date, "pre_event_max": round(pre_max, 4), "pre_event_max_date": pre.idxmax().date().isoformat(),
            "post_event_max_asof": None if np.isnan(post_max_asof) else round(post_max_asof, 4), "satisfied_asof": satisfied,
            "blocked_until": blocked_until}


def split_discontinuities(df: pd.DataFrame, splits: pd.Series | None, since: str, vendor_ret_abs: float, band: tuple[float, float],
                          raw_since: str) -> list[dict]:
    """VTR형 후보(목록만, 수정 없음): 분할로 기록된 날 공급자 Close가 vendor_ret_abs 이상 불연속인 이벤트.

    분할이 공급자 종가에 제대로 반영됐다면 그날 공급자 종가는 시장 변동 정도만 움직인다. 크게 움직였다면 분할 기록이 종가에
    반영되지 않았을 수 있다(분할 비율이 1에 가까우면 못 잡는 휴리스틱). 비수정 종가 역산(raw_close)이 그 비율을 이미 곱한
    구간(raw_since 이후 ~ 사건 이전)에서 band 범위 비수정 종가 봉 수를 함께 표시한다(10달러 필터 영향 점검용).
    """
    out = []
    for ex, ratio in (splits if splits is not None else pd.Series(dtype=float)).items():
        if ex < pd.Timestamp(since):
            continue
        pos = df.index.searchsorted(ex)
        if pos <= 0 or pos >= len(df):
            continue
        v = df["v_close"].iloc[pos] / df["v_close"].iloc[pos - 1] - 1
        if abs(v) < vendor_ret_abs:
            continue
        pre = df["raw_close"].loc[pd.Timestamp(raw_since): df.index[pos] - pd.Timedelta(days=1)]
        out.append({"ex_date": ex.date().isoformat(), "bar_date": df.index[pos].date().isoformat(), "ratio": round(float(ratio), 4),
                    "vendor_ret": round(float(v), 4),
                    "raw_ret": round(float(df["raw_close"].iloc[pos] / df["raw_close"].iloc[pos - 1] - 1), 4),
                    "adj_ret": round(float(df["close"].iloc[pos] / df["close"].iloc[pos - 1] - 1), 4),
                    "pre_event_raw_close_min": round(float(pre.min()), 2) if len(pre) else None,
                    "pre_event_raw_close_max": round(float(pre.max()), 2) if len(pre) else None,
                    "bars_pre_event_raw_in_band": int(((pre >= band[0]) & (pre <= band[1])).sum()) if len(pre) else 0})
    return out


def large_dividends(df: pd.DataFrame, dividends: pd.Series | None, threshold: float, since: str) -> list[dict]:
    """배당락 수익률(주당 배당 / 배당락 전일 공급자 종가) >= threshold인 배당. 특별배당 후보다.

    배당 금액은 분할 보정 기준이라 공급자 Close(v_close, 분할 보정)와 같은 기준으로 나눈다. ex-date가 휴장일이면 다음 개장일 봉에 붙인다.
    """
    out = []
    for ex, amt in (dividends if dividends is not None else pd.Series(dtype=float)).items():
        if ex < pd.Timestamp(since):
            continue
        pos = df.index.searchsorted(ex)
        if pos <= 0 or pos >= len(df):
            continue
        pct = float(amt) / float(df["v_close"].iloc[pos - 1])
        if pct >= threshold:
            out.append({"ex_date": ex.date().isoformat(), "bar_date": df.index[pos].date().isoformat(), "amount": round(float(amt), 4),
                        "pct_of_prev_close": round(pct, 4)})
    return out
