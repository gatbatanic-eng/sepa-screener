"""체결 규칙(순수 함수). 체결가 계산은 이 파일에서만 한다 — 전략 코드는 체결가를 계산하지 않는다.

모든 함수는 한 봉의 시가·고가·저가·종가와 주문 수준만 받는다. 일봉으로는 봉 안의 순서를 알 수 없으므로,
순서가 결과를 바꾸는 경우는 모두 보수적으로(손절 우선) 처리한다.
"""
from __future__ import annotations


def entry_next_open(open_: float) -> float:
    """[원전/근사, M1] 신호 다음 봉 시가에 체결."""
    return open_


def entry_buy_stop(level: float, open_: float, high: float) -> float | None:
    """[원전, M2] 고가 ≥ 돌파가면 max(시가, 돌파가)에 체결(갭 상승이면 시가). 닿지 않으면 None."""
    if high >= level:
        return max(open_, level)
    return None


def intraday_stop_fill(level: float, open_: float, low: float) -> float | None:
    """보유 중인 포지션의 장중 스탑. 시가 ≤ 손절가면 시가(갭), 아니면 저가 ≤ 손절가일 때 손절가. 닿지 않으면 None."""
    if open_ <= level:
        return open_
    if low <= level:
        return level
    return None


def entry_bar_intraday_stop_fill(level: float, low: float) -> float | None:
    """진입 당일 intraday 손절. 저가 ≤ 손절가면 **손절가에 체결**로 고정한다.

    근거: M2는 장중(돌파가)에 진입하므로 진입 전 시가는 이후 손절 체결가와 무관하다. 시가가 손절가 아래였더라도 포지션은
    그 시가에 없었다. M1은 시가에 진입하고 손절가는 체결가 아래이므로 시가가 손절가 이하일 수 없다.
    일봉으로 진입과 저가의 순서를 알 수 없어 저가가 손절가에 닿았으면 손절된 것으로 본다(보수적 가정).
    """
    if low <= level:
        return level
    return None


def close_stop_triggered(level: float, close: float) -> bool:
    """종가형 손절: 종가 < 손절가. 체결은 다음 봉 시가(호출자가 처리)."""
    return close < level
