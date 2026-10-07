"""한국 투자자별 순매수 일별 이력 저장소. NHPLUG가 한 번에 최근 30일만 주므로 매일 받아서 쌓는다.
research/nhplug/flow_history/YYYY-MM.json = {날짜(YYYYMMDD): {종목코드: [외국인(invest), 기관, 개인 순매수 수량, 종가, 거래량, 프로그램 순매수 수량]}}
같은 (날짜, 종목)은 처음 받은 값을 유지한다(덮어쓰지 않음). 다만 2026-10-07 이전 행은 프로그램 값이 없어(5칸) 나중에 받은 값으로 6번째 칸만 채운다.
오늘(KST) 행은 집계 전일 수 있어 저장하지 않는다."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "research" / "nhplug" / "flow_history"


def _i(v):
    try:
        return int(float(str(v).replace(",", "").replace("+", "")))
    except (TypeError, ValueError):
        return None


class FlowStore:
    def __init__(self, directory: Path | None = None):
        self.dir = directory or DIR
        self.days: dict[str, dict[str, list]] = {}
        self._dirty: set[str] = set()
        for f in sorted(self.dir.glob("*.json")):
            try:
                self.days.update(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue

    def merge(self, code: str, inv: list[dict], exclude_date: str | None = None) -> int:
        """투자자 동향 행들을 합친다. 새로 넣은 건수(프로그램 칸만 채운 경우 포함)를 돌려준다."""
        added = 0
        for x in inv:
            d = str(x.get("bsop_date1") or "")
            vals = [_i(x.get("invest")), _i(x.get("gigwan")), _i(x.get("person")), _i(x.get("stck_prpr")), _i(x.get("acml_vol"))]
            prog = _i(x.get("program"))
            if len(d) != 8 or d == exclude_date or any(v is None for v in vals):
                continue
            day = self.days.setdefault(d, {})
            if code not in day:
                day[code] = vals + ([prog] if prog is not None else [])
                self._dirty.add(d[:6])
                added += 1
            elif len(day[code]) == 5 and prog is not None:      # 예전 행: 기존 다섯 칸은 그대로 두고 프로그램 칸만 채운다
                day[code] = day[code] + [prog]
                self._dirty.add(d[:6])
                added += 1
        return added

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for ym in sorted(self._dirty):
            part = {d: dict(sorted(v.items())) for d, v in sorted(self.days.items()) if d[:6] == ym}
            (self.dir / f"{ym[:4]}-{ym[4:]}.json").write_text(json.dumps(part, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        self._dirty.clear()

    def sessions(self, min_stocks: int = 300) -> list[str]:
        """종목이 충분히 들어 있는 날짜(개장일)만 오름차순으로."""
        return sorted(d for d, v in self.days.items() if len(v) >= min_stocks)

    def window(self, code: str, sessions: list[str]) -> list[list] | None:
        rows = [self.days.get(d, {}).get(code) for d in sessions]
        return None if any(r is None for r in rows) else rows
