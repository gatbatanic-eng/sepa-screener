"""거래 기록 스키마 (CLAUDE.md 6절). 트랜치마다 한 행이다.

- `version`은 규격 버전, `strategy_version`은 전략 구현 버전.
- `costs`와 `r_multiple`은 weight를 곱한 값이다. 같은 trade_id의 행을 합하면 거래 전체 값이 된다.
- 손절이 없는 전략(A2-원전)은 initial_stop·stop_kind가 None이고 risk_basis가 명목 리스크를 표시한다.
- regime_tag, event_flag는 P3(국면·이벤트)에서 채운다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

import pandas as pd

EXIT_REASONS = ("stop", "trailing", "time", "rule", "partial", "open_mtm")


@dataclass(frozen=True)
class Trade:
    ticker: str
    strategy: str
    version: str                      # 규격 버전
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    initial_stop: float | None
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str
    tranche: str
    hold_days: int
    costs: float                      # weight × (진입비용 + 청산비용), 주당 가격 단위
    r_multiple: float                 # weight × (청산가 − 진입가 − 진입비용 − 청산비용) / 1R
    mfe_r: float
    mae_r: float
    regime_tag: str | None
    event_flag: bool | None
    # 추가 필드 (v1.3)
    strategy_version: str
    entry_model: str
    risk_basis: str
    stop_kind: str | None
    trade_id: int
    weight: float

    def __post_init__(self):
        if self.exit_reason not in EXIT_REASONS:
            raise ValueError(f"알 수 없는 exit_reason: {self.exit_reason}")

    def to_dict(self) -> dict:
        return asdict(self)


COLUMNS = [f.name for f in fields(Trade)]


def to_frame(trades: list[Trade]) -> pd.DataFrame:
    return pd.DataFrame([t.to_dict() for t in trades], columns=COLUMNS)


def trade_level_r(trades: list[Trade]) -> dict[int, float]:
    """trade_id별 r_multiple 합(트랜치 합산)."""
    out: dict[int, float] = {}
    for t in trades:
        out[t.trade_id] = out.get(t.trade_id, 0.0) + t.r_multiple
    return out
