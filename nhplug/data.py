"""NHPLUG로 모은 한국 보조 데이터(업종·수급) 읽기. 네트워크를 쓰지 않고 저장소 파일만 읽는다.
업종: research/nhplug/kr_extra.json(주 1회 전종목), 수급: research/nhplug/kr_flow.json(매일 후보·보유 종목)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRA = "research/nhplug/kr_extra.json"
FLOW = "research/nhplug/kr_flow.json"
FLOW_MAX_AGE_DAYS = 4   # 수급 기준일이 이보다 오래되면 쓰지 않는다(주말·휴일 하루 이틀은 허용)


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def sector_group(name: str | None) -> str | None:
    """'코스닥 전기·전자' → '전기·전자'. 코스피·코스닥에 같은 업종이 있어 시장 접두를 떼고 한 묶음으로 본다."""
    if not name:
        return None
    s = str(name).strip()
    for p in ("코스피 ", "코스닥 "):
        if s.startswith(p):
            s = s[len(p):]
    return s or None


def load_sectors(root: Path | None = None) -> dict[tuple[str, str], str]:
    """{('kr', 코드): 업종 묶음}. 파일이 없으면 빈 값(한도는 '미검사'로 남는다)."""
    stocks = _read((root or ROOT) / EXTRA).get("stocks") or {}
    out = {}
    for code, v in stocks.items():
        g = sector_group((v or {}).get("sector")) if isinstance(v, dict) else None
        if g:
            out[("kr", str(code).zfill(6))] = g
    return out


def load_flow(root: Path | None = None, today: dt.date | None = None) -> dict[str, dict]:
    """{코드: {frgn5, inst5, ..., asOf}}. 기준일이 오래된 종목은 뺀다."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    out = {}
    for code, v in (_read((root or ROOT) / FLOW).get("stocks") or {}).items():
        if not isinstance(v, dict) or v.get("asOf") is None:
            continue
        try:
            as_of = dt.datetime.strptime(str(v["asOf"]), "%Y%m%d").date()
        except ValueError:
            continue
        if (today - as_of).days <= FLOW_MAX_AGE_DAYS:
            out[str(code).zfill(6)] = v
    return out
