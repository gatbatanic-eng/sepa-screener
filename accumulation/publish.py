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
    def load(sub):
        recs = [_read(f, {}) for f in sorted((src / sub).glob("*.json"))]
        return [r for r in recs if r.get("date")]

    records, records_v2 = load("forward"), load("forward_v2")
    v2_by_day = {r["date"]: r for r in records_v2}
    v1_by_day = {r["date"]: r for r in records}
    recent = []
    for d in sorted(set(v1_by_day) | set(v2_by_day), reverse=True)[:10]:
        r, q = v1_by_day.get(d), v2_by_day.get(d)
        recent.append({"date": d, "eligible": r.get("eligible") if r else None, "recordedAt": (r or q).get("recordedAt"),
                       "FLOW": len(r["groups"].get("FLOW", [])) if r else None, "FLOW_ACC": len(r["groups"].get("FLOW_ACC", [])) if r else None,
                       "v2Eligible": q.get("eligible") if q else None, "V2": len(q["groups"].get("V2", [])) if q else None})

    def rows(ps):
        keep = ("close", "netShare", "posDays", "topDayShare", "posWeeks", "ret20", "hiRatio", "lead")
        return [{"code": p["code"], "name": names.get(p["code"]), "market": p.get("market"), "sector": sectors.get(p["code"]), **{k: p.get(k) for k in keep if k in p}} for p in ps]

    latest = {"date": None, "v2Date": None, "groups": {}}
    if records:
        latest["date"] = records[-1]["date"]
        latest["groups"].update({g: rows(v) for g, v in records[-1]["groups"].items()})
    if records_v2:
        latest["v2Date"] = records_v2[-1]["date"]
        latest["groups"]["V2"] = rows(records_v2[-1]["groups"].get("V2", []))
    latest = latest if (records or records_v2) else None
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
