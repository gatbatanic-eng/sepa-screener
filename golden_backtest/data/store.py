"""data_cache(parquet, git 제외) 입출력과 manifest(수집 기록, 커밋 대상).

저장 열: open/high/low/close(완전 수정) · volume · raw_close(비수정) · dollar_volume · v_close/v_adj_close(감사용)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data_cache"
OHLCV_DIR = CACHE_DIR / "ohlcv"
INDEX_DIR = CACHE_DIR / "index"
SPLITS_DIR = CACHE_DIR / "splits"
META_DIR = CACHE_DIR / "meta"
MANIFEST_DIR = ROOT / "manifest"


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp)
    for attempt in range(8):  # Windows에서 백신·인덱서가 방금 만든 파일을 잠깐 잡고 있을 수 있다
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.25 * (attempt + 1))


def save_ohlcv(symbol: str, df: pd.DataFrame) -> None:
    _write_parquet(df, OHLCV_DIR / f"{symbol}.parquet")


def load_ohlcv(symbol: str) -> pd.DataFrame:
    path = OHLCV_DIR / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{symbol}: 캐시 없음 ({path}). python -m golden_backtest.data.build 로 수집한다.")
    return pd.read_parquet(path)


def save_index(name: str, df: pd.DataFrame) -> None:
    _write_parquet(df, INDEX_DIR / f"{name}.parquet")


def load_index(name: str) -> pd.DataFrame:
    return pd.read_parquet(INDEX_DIR / f"{name}.parquet")


def save_splits(symbol: str, splits: pd.Series) -> None:
    SPLITS_DIR.mkdir(parents=True, exist_ok=True)
    rows = {d.date().isoformat(): float(v) for d, v in splits.items()}
    (SPLITS_DIR / f"{symbol}.json").write_text(json.dumps(rows), encoding="utf-8")


def load_splits(symbol: str) -> pd.Series:
    path = SPLITS_DIR / f"{symbol}.json"
    if not path.exists():
        return pd.Series(dtype=float)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return pd.Series({pd.Timestamp(k): float(v) for k, v in raw.items()}, dtype=float)


def save_meta(symbol: str, meta: dict) -> None:
    """수집 때의 정리 내역(결측 구간, 시작 구간 절단 등). 기존 캐시를 유지할 때 품질 집계를 다시 만드는 데 쓴다."""
    META_DIR.mkdir(parents=True, exist_ok=True)
    (META_DIR / f"{symbol}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def load_meta(symbol: str) -> dict | None:
    path = META_DIR / f"{symbol}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def cached_symbols() -> list[str]:
    return sorted(p.stem for p in OHLCV_DIR.glob("*.parquet")) if OHLCV_DIR.exists() else []


def write_manifest(name: str, obj: dict) -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / name
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return path
