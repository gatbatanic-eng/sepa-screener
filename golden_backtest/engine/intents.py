"""주문 의도 타입. 전략은 의도만 만들고, 체결·비용은 engine이 처리한다.

의존 방향: strategies → engine 만 허용한다. 이 파일과 engine/ 안의 어떤 파일도 strategies를 import하지 않는다.

시점 규칙 (t = 신호를 낸 봉, 종가 마감 후 판단)
- EntryIntent : t+1 봉에서 체결을 시도한다. 하루짜리 주문이라 체결되지 않으면 사라지고, 전략이 다음 날 다시 낸다.
- ExitIntent  : 손절 종류(kind)로 처리 방식이 갈린다.
    kind="intraday", price=P : P를 손절가로 t+1 봉부터 장중 감시한다. 올리는 것만 반영하고 내리는 제안은 무시한다.
    kind="close",    price=P : 같은 봉 t의 종가 < P 이면 t+1 시가에 청산한다. 올리는 것만 반영한다.
    kind="close",    price=None : 조건을 이미 t 종가에서 확인한 규칙 청산. at="next_open"이면 t+1 시가, at="next_close"이면
                      날짜가 미리 정해진 종가 청산(예: B3 트랜치 A, 진입 후 5거래일째 종가)으로 t+1 종가에 체결한다.
- StopSpec    : 초기 손절. 체결가를 안 뒤 전략이 결정한다. price=None이면 손절 없음(명목 리스크로 R만 계산, 예: A2-원전).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol

StopKind = Literal["intraday", "close"]
EXIT_REASONS = ("stop", "trailing", "time", "rule", "partial")  # 기록의 exit_reason (open_mtm은 엔진이 붙인다)


@dataclass(frozen=True)
class EntryIntent:
    kind: Literal["next_open", "buy_stop"]
    price: float | None = None                     # buy_stop의 돌파가
    tranches: Mapping[str, float] = field(default_factory=lambda: {"ALL": 1.0})  # 트랜치 이름 → 비중 (합 1)

    def __post_init__(self):
        if self.kind == "buy_stop" and not (self.price and self.price > 0):
            raise ValueError("buy_stop 의도에는 양수 price(돌파가)가 필요하다")
        if self.kind == "next_open" and self.price is not None:
            raise ValueError("next_open 의도는 price를 받지 않는다")
        if not self.tranches or any(w <= 0 for w in self.tranches.values()) or abs(sum(self.tranches.values()) - 1.0) > 1e-9:
            raise ValueError("트랜치 비중은 모두 양수이고 합이 1이어야 한다")


@dataclass(frozen=True)
class StopSpec:
    price: float | None                            # None = 손절 없음
    kind: StopKind | None = None
    nominal_risk: float | None = None              # price가 None일 때 R 계산용 명목 리스크(가격 단위)
    risk_basis: str = "stop"                       # 기록용: "stop" | "nominal_3atr" ...
    reason: str = "stop"

    def __post_init__(self):
        if self.price is None:
            if not (self.nominal_risk and self.nominal_risk > 0) or self.kind is not None:
                raise ValueError("손절이 없으면 nominal_risk(>0)만 주고 kind는 비운다")
        else:
            if self.kind not in ("intraday", "close"):
                raise ValueError("손절가가 있으면 kind는 intraday | close")
            if self.price <= 0:
                raise ValueError("손절가는 양수")
        if self.reason not in EXIT_REASONS:
            raise ValueError(f"알 수 없는 reason: {self.reason}")


@dataclass(frozen=True)
class ExitIntent:
    kind: StopKind
    reason: str
    price: float | None = None                     # 손절 수준. None이면 규칙 청산(close 종류만)
    tranche: str | None = None                     # None = 열려 있는 모든 트랜치
    at: Literal["next_open", "next_close"] = "next_open"

    def __post_init__(self):
        if self.reason not in EXIT_REASONS:
            raise ValueError(f"알 수 없는 reason: {self.reason}")
        if self.kind == "intraday" and self.price is None:
            raise ValueError("intraday 의도에는 손절가(price)가 필요하다")
        if self.at == "next_close" and not (self.kind == "close" and self.price is None):
            raise ValueError("next_close는 price 없는 close 청산에만 쓴다")


class StrategyLike(Protocol):
    """엔진이 요구하는 전략의 모양. strategies/base.py의 Strategy가 이를 구현한다(engine은 strategies를 import하지 않는다)."""

    code: str
    version: str
    entry_model: str

    def prepare(self, df): ...                              # t일 종가까지만 써서 벡터화 계산
    def entry_intent(self, row) -> EntryIntent | None: ...  # 신호일 t의 행
    def initial_stop(self, row, fill_price: float) -> StopSpec: ...  # row = 신호일 t의 행
    def manage(self, position, row) -> list[ExitIntent]: ...         # row = 방금 마감한 봉
