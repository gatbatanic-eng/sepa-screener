from __future__ import annotations

import gzip
import io
import json
from pathlib import Path

from .config import canonical_json


def _gzip_bytes(payload: dict) -> bytes:
    raw = canonical_json(payload).encode("utf-8")
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw)
    return out.getvalue()


def write_immutable_gzip_json(path: Path, payload: dict) -> None:
    """Write one canonical PIT snapshot and never overwrite an existing one."""
    if path.exists():
        raise RuntimeError(f"PIT raw snapshot already exists; overwrite prohibited: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(_gzip_bytes(payload))
    tmp.replace(path)


def read_gzip_json(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_replaceable_json(path: Path, payload: dict) -> None:
    """Write rebuildable/public state atomically; unlike raw PIT data this may update."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(canonical_json(payload), encoding="utf-8")
    tmp.replace(path)
