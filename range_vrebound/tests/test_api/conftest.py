import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_db
from src.api.main import app
from src.storage import get_engine, get_session_factory


@pytest.fixture()
def db_session(tmp_path):
    engine = get_engine(tmp_path / "api_test.db")
    session_factory = get_session_factory(engine)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
