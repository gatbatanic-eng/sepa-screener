"""SEPA 추적기(research/{kr,us}.json)의 신호를 읽어 에이전트가 쓰는 속성만 남기고 보관한다.
추적기 파일이 다시 쓰이며 옛 신호가 사라져도 보관본은 줄어들지 않는다(같은 id는 처음 값 유지)."""
from __future__ import annotations

from pathlib import Path

from . import config
from .roster import USED_KEYS
from .store import read_json, write_json


def load_sepa(market: str, research_dir: Path | None = None) -> list[dict]:
    doc = read_json((research_dir or config.ROOT / "research") / f"{market}.json", {}) or {}
    out = []
    for s in doc.get("signals", []):
        attrs = s.get("attributes") or {}
        out.append({"id": f"sepa:{market}:{s['id']}", "market": market, "code": s["code"], "name": s.get("name"),
                    "date": s["date"], "group": s["group"],
                    "exchange": (s.get("benchmark") if market == "kr" else "US"),
                    "attributes": {k: attrs.get(k) for k in USED_KEYS}})
    return out


def archive(market: str, state_dir: Path | None = None, research_dir: Path | None = None) -> list[dict]:
    path = (state_dir or config.STATE_DIR) / f"signals_{market}.json"
    kept = {s["id"]: s for s in (read_json(path, {"signals": []}) or {"signals": []})["signals"]}
    for s in load_sepa(market, research_dir):
        kept.setdefault(s["id"], s)
    rows = sorted(kept.values(), key=lambda s: (s["date"], s["id"]))
    write_json(path, {"schemaVersion": 1, "signals": rows})
    return rows
