"""FastAPI 의존성: DB 세션.

엔진은 첫 요청이 실제로 들어올 때(지연 생성) 만든다 — 모듈을 import만
해도 `data/screener.db` 파일이 생기지 않게 하기 위함이다. 테스트는
`app.dependency_overrides[get_db]`로 이 함수 자체를 통째로 바꿔치기한다.
"""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from src.storage import DEFAULT_DB_PATH, get_engine, get_session_factory

_session_factory: sessionmaker | None = None


def _get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        engine = get_engine(DEFAULT_DB_PATH)
        _session_factory = get_session_factory(engine)
    return _session_factory


def get_db():
    session = _get_session_factory()()
    try:
        yield session
    finally:
        session.close()
