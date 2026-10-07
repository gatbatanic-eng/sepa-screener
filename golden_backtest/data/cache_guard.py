"""재수집 결과가 기존 캐시를 망가뜨리지 않는지 검사한다(순수 함수).

공급자 데이터는 소급 변경될 수 있다(예: WBD가 하루 만에 1행으로 줄었다, 분리상장 보정). 기존 캐시보다 행이 줄었거나 과거 값이
바뀌었으면 호출자는 덮어쓰지 않고 기존 캐시를 유지해야 한다.

비교 대상은 새 배당·분할이 생겨도 과거 값이 변하지 않는 것들이다.
- 날짜 집합(행 수, 사라진 날짜)
- raw_close(비수정 종가: 실제 가격이라 불변)
- 수정 종가의 일간 수익률(배당 보정은 과거 전 구간에 같은 배수를 곱할 뿐이라 수익률은 불변)
수정 종가의 절대 수준은 새 배당마다 바뀌는 것이 정상이라 비교하지 않는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RAW_REL_TOL = 1e-4
RET_ABS_TOL = 1e-4


def compare_frames(old: pd.DataFrame, new: pd.DataFrame) -> dict | None:
    """문제가 없으면 None, 있으면 사유와 수치를 담은 dict."""
    reasons: list[str] = []
    info: dict = {"rows_old": int(len(old)), "rows_new": int(len(new))}
    if len(new) < len(old):
        reasons.append("행 감소")
    missing = old.index.difference(new.index)
    info["missing_dates"] = int(len(missing))
    if len(missing):
        reasons.append("기존 날짜 소실")
        info["first_missing_date"] = missing[0].date().isoformat()
    common = old.index.intersection(new.index)
    if len(common):
        o, n = old.loc[common], new.loc[common]
        both = o["raw_close"].notna() & n["raw_close"].notna()
        rel = ((o["raw_close"] - n["raw_close"]).abs() / o["raw_close"].abs())[both]
        info["raw_close_max_rel_diff"] = float(rel.max()) if len(rel) else 0.0
        bad_raw = rel[rel > RAW_REL_TOL]
        if len(bad_raw):
            reasons.append("비수정 종가 변경")
            info["first_changed_date"] = bad_raw.index[0].date().isoformat()
        dret = (o["close"].pct_change() - n["close"].pct_change()).abs().dropna()
        info["return_max_abs_diff"] = float(dret.max()) if len(dret) else 0.0
        bad_ret = dret[dret > RET_ABS_TOL]
        if len(bad_ret):
            reasons.append("수정 종가 수익률 변경")
            info.setdefault("first_changed_date", bad_ret.index[0].date().isoformat())
            info["first_changed_date"] = min(info["first_changed_date"], bad_ret.index[0].date().isoformat())
    if not reasons:
        return None
    info["reasons"] = reasons
    return info
