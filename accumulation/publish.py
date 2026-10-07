"""화면(docs/accumulation)이 읽는 공개 JSON을 만든다. (python -m accumulation.publish)
research/accumulation의 백테스트·전향 추적 결과와 최신 후보(이름·업종 포함)를 docs/research/accumulation.json 한 파일로 합친다."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "research" / "accumulation"
OUT = ROOT / "docs" / "research" / "accumulation.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def build(root: Path | None = None) -> dict:
    root = root or ROOT
    src = root / "research" / "accumulation"
    names = {str(r["code"]).zfill(6): r.get("name") for r in _read(root / "docs" / "data" / "latest_kr.json", []) if r.get("code")}
    sectors = {c: v.get("sector") for c, v in (_read(root / "research" / "nhplug" / "kr_extra.json", {}).get("stocks") or {}).items() if isinstance(v, dict)}
    records = [_read(f, {}) for f in sorted((src / "forward").glob("*.json"))]
    records = [r for r in records if r.get("date")]
    recent = []
    for r in records[-10:][::-1]:
        recent.append({"date": r["date"], "eligible": r.get("eligible"), "recordedAt": r.get("recordedAt"),
                       "FLOW": len(r["groups"].get("FLOW", [])), "FLOW_ACC": len(r["groups"].get("FLOW_ACC", []))})
    latest = None
    if records:
        r = records[-1]
        latest = {"date": r["date"], "groups": {g: [{"code": p["code"], "name": names.get(p["code"]), "market": p.get("market"), "sector": sectors.get(p["code"]),
                                                      "close": p.get("close"), "netShare": p.get("netShare"), "posDays": p.get("posDays")} for p in v]
                                                  for g, v in r["groups"].items()}}
    fwd = _read(src / "forward_summary.json", {})
    return {"schemaVersion": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "forward": {"summary": fwd, "recent": recent, "latest": latest},
            "backtest": _read(src / "backtest.json", None)}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("저장:", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
