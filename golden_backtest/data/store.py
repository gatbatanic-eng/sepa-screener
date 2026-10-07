"""data_cache(parquet, git 제외) 입출력과 manifest(수집 기록, 커밋 대상).

저장 열: open/high/low/close(완전 수정) · volume · raw_close(비수정) · dollar_volume · v_close/v_adj_close(감사용)
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data_cache"
OHLCV_DIR = CACHE_DIR / "ohlcv"
INDEX_DIR = CACHE_DIR / "index"
SPLITS_DIR = CACHE_DIR / "splits"
META_DIR = CACHE_DIR / "meta"
DIVIDENDS_DIR = CACHE_DIR / "dividends"
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
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not raw:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))  # 빈 경우에도 날짜 인덱스를 유지한다(날짜 비교가 되도록)
    return pd.Series({pd.Timestamp(k): float(v) for k, v in raw.items()}, dtype=float)


def save_dividends(symbol: str, dividends: pd.Series) -> None:
    DIVIDENDS_DIR.mkdir(parents=True, exist_ok=True)
    rows = {d.date().isoformat(): float(v) for d, v in dividends.items()}
    (DIVIDENDS_DIR / f"{symbol}.json").write_text(json.dumps(rows), encoding="utf-8")


def load_dividends(symbol: str) -> pd.Series:
    path = DIVIDENDS_DIR / f"{symbol}.json"
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not raw:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
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


class SnapshotMismatch(RuntimeError):
    """캐시가 manifest에 기록된 스냅샷과 다르다(재수집이 일어났거나 파일이 바뀌었다)."""


def frame_hash(df: pd.DataFrame) -> str:
    """프레임 내용(값·인덱스·열 이름)의 해시 16자리. 같은 데이터면 파일 쓰기(parquet 왕복)·날짜 단위(s/us/ns)와 무관하게 같다.

    날짜는 ns 정수, 값은 float64 바이트로 정규화해 해시한다(pandas 내부 해시는 날짜 단위에 따라 값이 달라진다).
    """
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(df.index.astype("datetime64[ns]").asi8).tobytes())
    for col in df.columns:
        h.update(str(col).encode() + b"|")
        h.update(np.ascontiguousarray(df[col].to_numpy(dtype="float64")).tobytes())
    return h.hexdigest()[:16]


def make_snapshot_id(collected_at: str, symbol_hashes: dict[str, str], index_hashes: dict[str, str]) -> str:
    """스냅샷 식별자 = 수집일(YYYYMMDD) + 모든 종목·지수 내용 해시의 해시 12자리. 데이터가 한 바이트라도 다르면 식별자가 다르다."""
    h = hashlib.sha256()
    for k in sorted(symbol_hashes):
        h.update(f"{k}:{symbol_hashes[k]};".encode())
    for k in sorted(index_hashes):
        h.update(f"@{k}:{index_hashes[k]};".encode())
    return f"{collected_at[:10].replace('-', '')}-{h.hexdigest()[:12]}"


_MANIFEST_CACHE: dict = {}


def load_manifest(refresh: bool = False) -> dict:
    if refresh or "m" not in _MANIFEST_CACHE:
        _MANIFEST_CACHE["m"] = json.loads((MANIFEST_DIR / "manifest.json").read_text(encoding="utf-8"))
    return _MANIFEST_CACHE["m"]


def snapshot_info() -> dict:
    """백테스트 결과에 기록할 스냅샷 정보(식별자, 수집일, 규격 버전)."""
    m = load_manifest()
    return {"snapshot_id": m["snapshot"]["id"], "collected_at": m["collected_at"], "spec_version": m["spec_version"]}


def load_ohlcv_verified(symbol: str) -> pd.DataFrame:
    """백테스트용 읽기: 캐시만 읽고(네트워크 없음) manifest의 스냅샷과 같은지 확인한다. 전체 이력을 그대로 돌려준다(자르지 않는다)."""
    df = load_ohlcv(symbol)
    rec = load_manifest()["symbols"].get(symbol)
    if rec is None:
        raise SnapshotMismatch(f"{symbol}: manifest에 없는 종목(스냅샷 밖)")
    if (len(df), df.index[0].date().isoformat(), df.index[-1].date().isoformat()) != (rec["rows"], rec["first"], rec["last"]):
        raise SnapshotMismatch(f"{symbol}: 행 수·첫 봉·마지막 봉이 manifest와 다르다")
    if frame_hash(df) != rec["content_hash"]:
        raise SnapshotMismatch(f"{symbol}: 내용 해시가 manifest와 다르다 — 재수집으로 캐시가 바뀐 뒤 manifest를 커밋하지 않았을 수 있다")
    return df


def events_hash(splits: pd.Series | None, dividends: pd.Series | None) -> str:
    """분할·배당 기록의 해시. 수정 비율 구간 경계와 대형 배당 플래그의 입력이라 스냅샷에 포함한다."""
    h = hashlib.sha256()
    for name, s in (("splits", splits), ("dividends", dividends)):
        h.update(name.encode())
        if s is not None:
            for d, v in sorted(s.items()):
                h.update(f"{pd.Timestamp(d).date().isoformat()}={float(v):.12g};".encode())
    return h.hexdigest()[:16]


def verify_events(symbol: str) -> None:
    rec = load_manifest()["symbols"].get(symbol)
    if rec is None or events_hash(load_splits(symbol), load_dividends(symbol)) != rec.get("events_hash"):
        raise SnapshotMismatch(f"{symbol}: 분할·배당 기록이 manifest 스냅샷과 다르다")
