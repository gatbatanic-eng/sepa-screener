"""api/deps.py의 실제(오버라이드하지 않은) get_db 경로를 검증한다.

API 라우트 테스트는 항상 get_db를 임시 DB로 바꿔치기하므로, 지연 생성
로직 자체는 별도로 확인해야 한다.
"""
import src.api.deps as deps
from sqlalchemy.orm import Session


def test_get_db_lazily_creates_session_factory_once(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_session_factory", None)
    monkeypatch.setattr(deps, "DEFAULT_DB_PATH", tmp_path / "lazy_test.db")

    gen = deps.get_db()
    session = next(gen)
    assert isinstance(session, Session)
    factory_after_first_call = deps._session_factory
    assert factory_after_first_call is not None

    # 세션을 정상 종료(finally의 close() 실행)
    try:
        next(gen)
    except StopIteration:
        pass

    # 두 번째 호출은 이미 만들어진 세션 팩토리를 재사용해야 한다(지연 생성은 한 번만)
    gen2 = deps.get_db()
    next(gen2)
    assert deps._session_factory is factory_after_first_call
    try:
        next(gen2)
    except StopIteration:
        pass
