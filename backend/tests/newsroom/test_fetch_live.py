"""T-502 live smoke: fetch_url against the real web (https://example.com, a stable IANA page).

Run with ``pytest backend -m integration``. Free (no key); needs the network. Checks the real path:
DNS resolution to a public address, streaming the body, extraction, evidence and snapshot.
"""

import json
import uuid

import pytest

from autora.app import build_embedder
from autora.db.models import Company
from autora.domains.newsroom.models import Evidence
from autora.domains.newsroom.tools import evidence as evidence_tools
from autora.infra.blobstore import LocalFSBlobStore
from autora.infra.http import HttpFetcher
from autora.runtime.actor import Actor
from autora.runtime.tools import ToolRegistry
from tests.conftest import running_agent_run

pytestmark = pytest.mark.integration


async def test_fetch_a_real_page(committed, tmp_path):
    async with committed() as session:
        run = await running_agent_run(session, "live-fetch")
        await session.commit()
    blobs = LocalFSBlobStore(tmp_path)
    registry = ToolRegistry(committed)
    registry.tool("fetch_url", description="", side_effect="write", retryable=True)(
        evidence_tools.fetch_url_tool(HttpFetcher(), blobs, build_embedder(None))
    )
    result = await registry.invoke(
        "fetch_url",
        {"url": "https://example.com/"},
        company_id=run.company_id,
        actor=Actor.system("smoke"),
        tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
        run_id=run.id,
        task_id=run.task_id,
        agent_id=run.agent_id,
    )
    assert result.ok, result.message
    assert "Example Domain" in (result.output["title"] or "") + result.output["excerpt"]
    async with committed() as session:
        evidence = await session.get(Evidence, uuid.UUID(result.output["evidence_id"]))
        assert (await session.get(Company, run.company_id)) is not None
    snapshot = await blobs.get(evidence.blob_key)
    assert b"Example Domain" in snapshot
    assert json.dumps(result.output)  # plain data for the model
