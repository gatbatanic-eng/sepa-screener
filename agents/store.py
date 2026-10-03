"""원장 파일 입출력. 신호 파일은 한 번 쓰면 수정하지 않는다."""
from __future__ import annotations

import gzip
import json
from pathlib import Path


def read_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_immutable_gz(path: Path, obj) -> bool:
    """이미 있으면 쓰지 않고 False."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    tmp.replace(path)
    return True


def write_json(path: Path, obj, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"),
                              allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
