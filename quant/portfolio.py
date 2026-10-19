"""보유 종목 선정(QUANT.md §5): 보유는 순위 KEEP_RANK 안이면 유지, 빈 자리는 순위 높은 비보유 종목부터 채움. 순수 함수."""
from __future__ import annotations

from . import config


def select(ranked: list[str], industry: dict[str, str | None], holdings: set[str], slots: int,
           keep_rank: int = config.KEEP_RANK, max_per_industry: int = config.MAX_PER_INDUSTRY) -> dict:
    pos = {s: i + 1 for i, s in enumerate(ranked)}
    kept = sorted((s for s in holdings if pos.get(s, 10**9) <= keep_rank), key=lambda s: pos[s])[:slots]
    counts: dict[str, int] = {}
    for s in kept:
        ind = industry.get(s)
        if ind:
            counts[ind] = counts.get(ind, 0) + 1
    entries, skipped = [], []
    for s in ranked:
        if len(kept) + len(entries) >= slots:
            break
        if s in holdings and s in kept:
            continue
        if s in holdings:      # 보유 중이지만 완충대 밖 → 신규로 다시 사지 않는다(순위가 올라오면 다음 달 유지 대상이 됨)
            continue
        ind = industry.get(s)
        if ind and counts.get(ind, 0) >= max_per_industry:
            skipped.append(s)
            continue
        entries.append(s)
        if ind:
            counts[ind] = counts.get(ind, 0) + 1
    exits = sorted(holdings - set(kept))
    return {"targets": kept + entries, "kept": kept, "entries": entries, "exits": exits, "industrySkipped": skipped}
