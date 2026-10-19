"""종목별 일봉 종가 보관. 날짜 문자열(YYYY-MM-DD) 오름차순. 신호일 이후 값은 절대 보지 않도록 모든 조회에 기준일을 받는다."""
from __future__ import annotations

from bisect import bisect_left, bisect_right


class PriceBook:
    def __init__(self, closes: dict):
        self.dates: dict[str, list[str]] = {}
        self.vals: dict[str, list[float]] = {}
        for sym, s in closes.items():
            pairs = sorted((str(d), float(v)) for d, v in zip(s.index, s.values) if v == v and float(v) > 0)
            if pairs:
                self.dates[sym] = [d for d, _ in pairs]
                self.vals[sym] = [v for _, v in pairs]

    def symbols(self):
        return self.dates.keys()

    def last_idx(self, sym: str, date: str) -> int:
        """date 이하 마지막 봉의 위치. 없으면 -1."""
        return bisect_right(self.dates.get(sym, []), date) - 1

    def close_on_or_before(self, sym: str, date: str):
        i = self.last_idx(sym, date)
        return (self.dates[sym][i], self.vals[sym][i]) if i >= 0 else None

    def first_on_or_after(self, sym: str, date: str, cut: str):
        """date 이상 첫 봉(cut 이하). 체결가 조회용."""
        ds = self.dates.get(sym, [])
        i = bisect_left(ds, date)
        return (ds[i], self.vals[sym][i]) if i < len(ds) and ds[i] <= cut else None

    def calendar(self, syms, cut: str) -> list[str]:
        out = set()
        for s in syms:
            out.update(d for d in self.dates.get(s, []) if d <= cut)
        return sorted(out)

    def features(self, sym: str, date: str):
        """date 시점 가격 특징. 봉이 모자라면 None."""
        i = self.last_idx(sym, date)
        if i < 0:
            return None
        from . import config
        if i + 1 < config.MIN_BARS:
            return None
        v = self.vals[sym]
        return {"lastBar": self.dates[sym][i], "price": v[i], "sma200": sum(v[i - 199:i + 1]) / 200.0,
                "ret_6m": v[i] / v[i - 126] - 1.0, "ret_12_1": v[i - 21] / v[i - 252] - 1.0}
