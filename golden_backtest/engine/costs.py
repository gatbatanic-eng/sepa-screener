"""비용 모델. 편도 비용 = 체결가 × (수수료 + 슬리피지), 진입·청산 각각 적용한다. 값은 config/costs.yaml에서만 온다."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class CostRate:
    commission: float
    slippage: float

    @property
    def one_way(self) -> float:
        return self.commission + self.slippage


@dataclass(frozen=True)
class Costs:
    default: CostRate
    groups: Mapping[str, CostRate] = field(default_factory=dict)

    def one_way_rate(self, group: str = "default") -> float:
        if group == "default":
            return self.default.one_way
        if group not in self.groups:
            raise ValueError(f"알 수 없는 비용 그룹: {group}")
        return self.groups[group].one_way

    def per_share(self, price: float, group: str = "default") -> float:
        """한 번의 체결(편도)이 주당 가격 단위로 얼마인가."""
        return price * self.one_way_rate(group)

    @classmethod
    def from_config(cls) -> "Costs":
        from golden_backtest import config

        raw = config.load("costs")
        d = raw["default"]
        default = CostRate(float(d["commission"]), float(d["slippage"]))
        groups = {}
        for name, over in (raw.get("groups") or {}).items():  # 빠진 항목은 default를 따른다
            groups[name] = CostRate(float(over.get("commission", default.commission)), float(over.get("slippage", default.slippage)))
        return cls(default, groups)
