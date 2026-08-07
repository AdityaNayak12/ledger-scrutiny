import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import get_db
from app.main import app

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False)


@pytest.fixture(autouse=True)
def db_session_fixture(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-auth-unit-tests-only-12345")
    connection = engine.connect()
    connection.execute(text("PRAGMA foreign_keys=ON"))
    TestingSessionLocal.configure(bind=connection)
    Base.metadata.create_all(bind=connection)

    def _override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=connection)
    connection.close()
