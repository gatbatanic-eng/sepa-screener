"""투자 자문 메모 생성. (python -m advisory.main)
예약·수동 실행: 일간(평일)·주간(금)·월간(월말) 메모를 보관하고 latest.json 갱신. --preview: latest.json만 갱신(보관·알림 없음)."""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from agents import config as agent_config, freshness
from agents.report import period_keys

from . import memo, picks_track


def run(today: dt.date, preview: bool = False, force: list[str] | None = None, new_list: Path | None = None,
        hold: bool = False, note: str | None = None) -> list[str]:
    keys = period_keys(today)
    made, paths = [], []
    for kind in ("daily", "weekly", "monthly"):
        if not (keys[kind] or (force and kind in force)):
            continue
        m = memo.build(kind, today)
        if note:
            m["staleNote"] = note
        if preview:
            memo.save(m, archive=False)
            return [f"preview:{m['key']}"]
        if hold:
            memo.save(m, archive=False)  # 시세가 아직 최신이 아님: latest만 갱신, 보관·추천 기록은 뒤 실행으로 미룬다(agents.freshness)
            continue
        if memo.save(m):
            made.append(f"{kind}:{m['key']}")
            if kind == "daily":
                picks_track.record(m["key"], m["picks"])
            paths.append(str(memo.config.OUT_DIR / kind / f"{m['key']}.md"))
        elif kind == "daily":
            memo.save(m, archive=False)  # 이미 보관된 날이면 latest만 갱신
    if new_list:
        new_list.write_text("\n".join(paths) + ("\n" if paths else ""), encoding="utf-8")
    return made


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--today", default=agent_config.session_date().isoformat())
    p.add_argument("--preview", action="store_true")
    p.add_argument("--force", nargs="*", choices=["daily", "weekly", "monthly"])
    p.add_argument("--new-list", type=Path)
    a = p.parse_args()
    today = dt.date.fromisoformat(a.today)
    fr = freshness.check(today)
    if fr["note"]:
        print(fr["note"])
    print("생성:", run(today, a.preview, a.force, a.new_list, fr["hold"], fr["note"] if fr["forced"] or fr["hold"] else None) or "없음")


if __name__ == "__main__":
    main()
