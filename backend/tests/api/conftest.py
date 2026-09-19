import sys
from pathlib import Path

import httpx
import pytest

API_DIR = Path(__file__).resolve().parents[2] / "api"
sys.path.insert(0, str(API_DIR))

from autora.app import build_runtime  # noqa: E402
from autora.infra.blobstore import LocalFSBlobStore  # noqa: E402
from autora.infra.settings import load_settings  # noqa: E402
from autora_api.app import create_app  # noqa: E402
from autora_api.deps import get_session, runtime_dep, settings_dep  # noqa: E402
from autora_api.routers.runs import blob_store_dep  # noqa: E402
from tests.newsroom.conftest import newsroom_room  # noqa: E402, F401 (fixture)

TOKEN = "test-operator-token"


@pytest.fixture
def blobs(tmp_path):
    return LocalFSBlobStore(tmp_path / "blobs")


@pytest.fixture
def runtime():
    """A fresh wired runtime per test (task manager, workflow engine, approvals, policy)."""
    return build_runtime()


@pytest.fixture
async def api(db_session, db_settings, blobs, runtime):
    """HTTP client against the real app, sharing the test's rolled-back DB session."""
    app = create_app()

    async def _session():
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[blob_store_dep] = lambda: blobs
    app.dependency_overrides[runtime_dep] = lambda: runtime
    app.dependency_overrides[settings_dep] = lambda: load_settings(
        database_url=db_settings.database_url, api_bearer_token=TOKEN
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client
