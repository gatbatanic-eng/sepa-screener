"""데이터 품질 점검: 이상치 집계(2014-01-01 이후)와 A1 이력 검사. 데이터를 고치지 않고 기록만 한다."""
from __future__ import annotations

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
        m = {"date": d.date().isoformat(), "ret": round(float(ret[d]), 4),
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
