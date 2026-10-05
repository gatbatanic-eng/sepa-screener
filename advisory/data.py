"""자문 위원회가 읽는 입력. 저장소에 이미 있는 산출물만 읽는다(네트워크 없음). 없거나 낡으면 데스크가 '데이터 없음'으로 표시한다."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from . import config


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load(root: Path | None = None) -> dict:
    root = root or config.ROOT
    d = root / "docs"
    meta_csv = root / "data" / "tenbagger_universe.csv"
    meta = {}
    if meta_csv.exists():
        with meta_csv.open(encoding="utf-8-sig") as f:
            meta = {str(r["Ticker"]).strip().upper(): r for r in csv.DictReader(f)}
    rules_text = (root / "macro" / "rules.yaml").read_text(encoding="utf-8") if (root / "macro" / "rules.yaml").exists() else ""
    return {
        "macro": _json(d / "macro.json"),
        "rules": parse_rules(rules_text),
        "regime_thresholds": parse_regime(rules_text),
        "rows": {m: [r for r in (_json(d / "data" / f"latest_{m}.json", []) or [])] for m in ("kr", "us")},
        "funnel": {m: (_json(d / "research" / f"funnel_{m}.json", {}) or {}).get("sectorHeat") for m in ("kr", "us")},
        "valuation": (_json(d / "data" / "valuation_us.json", {}) or {}).get("symbols", {}),
        "meta": meta,
        "league": _json(d / "research" / "agents.json"),
        "root": root,
    }


def parse_rules(text: str) -> dict[str, dict]:
    """rules.yaml에서 규칙 id → {condition, message, score}만 뽑는다(PyYAML 없이)."""
    out: dict[str, dict] = {}
    for block in re.split(r"\n\s*-\s+id:\s*", "\n" + text)[1:]:
        rid = block.split("\n", 1)[0].strip()
        cond = re.search(r'condition:\s*"(.*?)"\s*$', block, re.M)
        msg = re.search(r'message:\s*"(.*?)"\s*$', block, re.M)
        score = re.search(r"score:\s*(-?\d+)", block)
        out[rid] = {"condition": cond.group(1) if cond else None, "message": msg.group(1) if msg else None,
                    "score": int(score.group(1)) if score else None}
    return out


def parse_regime(text: str) -> dict:
    on = re.search(r"risk_on_min:\s*(-?\d+)", text)
    off = re.search(r"risk_off_max:\s*(-?\d+)", text)
    return {"risk_on_min": int(on.group(1)) if on else 1, "risk_off_max": int(off.group(1)) if off else -3}
