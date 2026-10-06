import os
import tempfile
from pathlib import Path

# Point the app at an isolated, throwaway database and upload dir *before* importing it.
_TMP = Path(tempfile.mkdtemp(prefix="geo-test-"))
os.environ["GEO_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["GEO_UPLOAD_DIR"] = str(_TMP / "uploads")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def upload(client):
    def _upload(filename: str, content: bytes):
        return client.post("/api/files/", files={"file": (filename, content)})

    return _upload
