"""파일 입출력. 선정 기록은 한 번 쓰면 수정하지 않는다."""
from __future__ import annotations

import json
from pathlib import Path


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"), allow_nan=False),
                   encoding="utf-8")
    tmp.replace(path)


def write_once(path: Path, obj) -> bool:
    """이미 있으면 쓰지 않고 False."""
    if path.exists():
        return False
    write_json(path, obj, indent=2)
    return True
