import os
import pytest
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.db import Base, build_engine, get_db
from app.main import app


@pytest.fixture
def sessions(tmp_path):
    # TEST_DATABASE_URL must point at a dedicated disposable PostgreSQL database.
    engine = build_engine(os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}"))
    if engine.dialect.name != "sqlite" and not (engine.url.database or "").startswith("test_"):
        raise ValueError("TEST_DATABASE_URL must use a database named test_*, never the real jobs DB")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(sessions):
    def override():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = override
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()
