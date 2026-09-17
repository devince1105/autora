import sys
from pathlib import Path

import httpx
import pytest

API_DIR = Path(__file__).resolve().parents[4] / "apps" / "api"
sys.path.insert(0, str(API_DIR))

from autora.infra.blobstore import LocalFSBlobStore  # noqa: E402
from autora.infra.settings import load_settings  # noqa: E402
from autora_api.app import create_app  # noqa: E402
from autora_api.deps import get_session, settings_dep  # noqa: E402
from autora_api.routers.runs import blob_store_dep  # noqa: E402

TOKEN = "test-operator-token"


@pytest.fixture
def blobs(tmp_path):
    return LocalFSBlobStore(tmp_path / "blobs")


@pytest.fixture
async def api(db_session, db_settings, blobs):
    """HTTP client against the real app, sharing the test's rolled-back DB session."""
    app = create_app()

    async def _session():
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[blob_store_dep] = lambda: blobs
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
