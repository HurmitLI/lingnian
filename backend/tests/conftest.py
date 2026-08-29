from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = BACKEND_ROOT / ".test-data"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'test.db'}"
os.environ["ASSET_ROOT"] = str(TEST_ROOT / "data")
os.environ["ASR_PROVIDER"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"

from app.core.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    if TEST_ROOT.exists():
        for child in TEST_ROOT.iterdir():
            if child.name != "test.db":
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session
