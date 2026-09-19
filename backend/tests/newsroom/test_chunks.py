"""T-503: chunking, the embed binding, halfvec storage, and search_evidence.

Embeddings are the fake hashing ones (deterministic, no network) or stubs; the real NVIDIA model
is covered by the integration smoke in test_embed_live.py.
"""

import json
import math
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

import autora.domains.newsroom as newsroom
from autora.app import build_embedder
from autora.db.models import Company, ModelCall
from autora.domains.newsroom.chunks import MAX, TARGET, chunk_text
from autora.domains.newsroom.models import EMBED_DIM, Evidence, EvidenceChunk
from autora.domains.newsroom.tools import evidence as evidence_tools
from autora.infra.blobstore import LocalFSBlobStore
from autora.infra.http import FixtureFetcher
from autora.infra.settings import SettingsError, load_settings
from autora.runtime.actor import Actor
from autora.runtime.models.embeddings import (
    EmbedCaller,
    Embedder,
    EmbeddingError,
    EmbeddingOutput,
    EmbeddingUnavailable,
    HashingEmbeddings,
    OpenAICompatibleEmbeddings,
)
from autora.runtime.models.factory import FAKE_EMBED_MODEL, embedder_from_settings
from autora.runtime.tools import ToolRegistry
from tests.conftest import running_agent_run, unique_company

FIXTURES = Path(newsroom.__file__).parent / "fixtures"
DB = "postgresql+asyncpg://u:p@localhost/db"


# --- chunking -------------------------------------------------------------------------------


def test_chunks_pack_paragraphs_and_locate_themselves():
    paragraphs = [f"Paragraph {i}. " + "word " * 60 for i in range(8)]
    text = "\n\n".join(p.strip() for p in paragraphs)
    chunks = chunk_text(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert text[chunk.start : chunk.end].strip() == chunk.text  # always locatable
        assert len(chunk.text) <= max(TARGET, MAX)
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    assert chunks[0].text.startswith("Paragraph 0.") and "Paragraph 7." in chunks[-1].text
    covered = "".join(text[c.start : c.end] for c in chunks)
    assert covered.replace("\n", "") == text.replace("\n", "")


def test_long_paragraphs_split_at_sentences_or_hard():
    sentences = "".join(f"第{i}句關於微電網的說明，內容很長很長很長。" for i in range(80))
    chunks = chunk_text(sentences)
    assert len(chunks) > 1 and all(len(c.text) <= MAX for c in chunks)
    assert all(c.text.endswith("。") for c in chunks[:-1])
    no_stops = "x" * (MAX * 2 + 10)
    hard = chunk_text(no_stops)
    assert [len(c.text) for c in hard] == [MAX, MAX, 10]
    assert chunk_text("") == [] and chunk_text("\n\n  \n") == []


# --- providers and the binding --------------------------------------------------------------


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


async def test_hashing_embeddings_are_deterministic_and_lexical():
    fake = HashingEmbeddings(64)
    out = await fake.embed(
        [
            "battery capacity fades",
            "Battery capacity fades!",
            "coffee prices",
            "微電網供電",
            "微電網停電",
        ],
        model_id="x",
        purpose="passage",
    )
    a, same, other, zh1, zh2 = out.vectors
    assert a == same and math.isclose(math.sqrt(sum(v * v for v in a)), 1.0)
    assert cosine(a, same) > cosine(a, other)
    assert cosine(zh1, zh2) > 0  # shares 微電 / 電網


async def test_openai_compatible_embeddings():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ],
                "usage": {"prompt_tokens": 7},
            },
        )

    provider = OpenAICompatibleEmbeddings(
        name="nvidia",
        base_url="https://api.test/v1/",
        api_key="k",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    out = await provider.embed(["a", "b"], model_id="m", purpose="query")
    assert out.vectors == [[1.0, 0.0], [0.0, 1.0]] and out.tokens == 7  # ordered by index
    assert seen["body"] == {
        "model": "m",
        "input": ["a", "b"],
        "encoding_format": "float",
        "input_type": "query",
    }
    assert seen["auth"] == "Bearer k"

    for status, kind in (
        (429, EmbeddingUnavailable),
        (503, EmbeddingUnavailable),
        (400, EmbeddingError),
    ):
        failing = OpenAICompatibleEmbeddings(
            name="nvidia",
            base_url="https://api.test/v1",
            api_key="k",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s, text="nope"))
            ),
        )
        with pytest.raises(kind):
            await failing.embed(["a"], model_id="m", purpose="passage")


class Scripted:
    name = "scripted"

    def __init__(self, dim, fail=False):
        self.dim, self.fail, self.calls = dim, fail, []

    async def embed(self, texts, *, model_id, purpose):
        self.calls.append((list(texts), purpose))
        if self.fail:
            raise EmbeddingUnavailable("service down")
        return EmbeddingOutput(vectors=[[0.5] * self.dim for _ in texts], tokens=10 * len(texts))


async def test_embedder_batches_records_calls_and_checks_dimensions(db_session):
    run = await running_agent_run(db_session, "embed")
    caller = EmbedCaller(
        company_id=run.company_id,
        run_id=run.id,
        task_id=run.task_id,
        agent_id=run.agent_id,
        role="researcher",
    )
    provider = Scripted(4)
    embedder = Embedder(
        provider=provider, model_id="m-embed", dim=4, price_per_mtok=Decimal("50"), batch_size=2
    )
    vectors = await embedder.embed(
        db_session, ["a", "b", "c", "d", "e"], purpose="passage", caller=caller
    )
    assert len(vectors) == 5 and [len(c[0]) for c in provider.calls] == [2, 2, 1]
    rows = (
        await db_session.scalars(
            select(ModelCall).where(ModelCall.run_id == run.id).order_by(ModelCall.created_at)
        )
    ).all()
    assert [(r.alias, r.capability, r.model_id, r.status, r.tokens_in) for r in rows] == [
        ("embed", "embedding", "m-embed", "ok", 20),
        ("embed", "embedding", "m-embed", "ok", 20),
        ("embed", "embedding", "m-embed", "ok", 10),
    ]
    assert [r.cost_usd for r in rows] == [Decimal("0.001"), Decimal("0.001"), Decimal("0.0005")]

    wrong = Embedder(provider=Scripted(3), model_id="m-embed", dim=4)
    with pytest.raises(EmbeddingError, match="3-dimensional vectors; storage expects 4"):
        await wrong.embed(db_session, ["a"], purpose="passage", caller=caller)
    down = Embedder(provider=Scripted(4, fail=True), model_id="m-embed", dim=4)
    with pytest.raises(EmbeddingUnavailable):
        await down.embed(db_session, ["a"], purpose="query", caller=caller)
    errors = (
        await db_session.scalars(
            select(ModelCall).where(ModelCall.run_id == run.id, ModelCall.status == "error")
        )
    ).all()
    assert [e.error["error_class"] for e in errors] == ["EmbeddingError", "EmbeddingUnavailable"]


def test_embedding_settings():
    fake = embedder_from_settings(None, dim=EMBED_DIM)
    assert fake.model_id == FAKE_EMBED_MODEL and fake.dim == EMBED_DIM
    with pytest.raises(SettingsError, match="EMBED_MODEL_ID is required"):
        load_settings(_env_file=None, database_url=DB, embed_provider="nvidia", nvidia_api_key="k")
    with pytest.raises(SettingsError, match="NVIDIA_API_KEY is required when EMBED_PROVIDER"):
        load_settings(
            _env_file=None,
            database_url=DB,
            embed_provider="nvidia",
            embed_model_id="e",
            nvidia_api_key="",
        )
    live = embedder_from_settings(
        load_settings(
            _env_file=None,
            database_url=DB,
            embed_provider="nvidia",
            embed_model_id="vendor/embed-x",
            nvidia_api_key="k",
            model_prices={"vendor/embed-x": {"input": 0.02}},
        ),
        dim=EMBED_DIM,
    )
    assert (live.model_id, live.provider.name, live.price_per_mtok) == (
        "vendor/embed-x",
        "nvidia",
        Decimal("0.02"),
    )


# --- storage ----------------------------------------------------------------------------------


async def test_halfvec_round_trip(db_session):
    company = await unique_company(db_session, "halfvec")
    evidence = Evidence(
        company_id=company.id,
        url="https://x.test/",
        final_url="https://x.test/",
        content_type="text/html",
        retrieved_at=datetime(2026, 9, 19, tzinfo=UTC),
        retrieved_on=date(2026, 9, 19),
        blob_key="k",
        extracted_text="t",
        text_hash="h",
    )
    db_session.add(evidence)
    await db_session.flush()
    vector = [0.25, -0.5, 1.0] + [0.0] * (EMBED_DIM - 3)
    chunk = EvidenceChunk(
        company_id=company.id,
        evidence_id=evidence.id,
        seq=0,
        start=0,
        end=1,
        text="t",
        embedding=vector,
        embedding_model="m",
    )
    db_session.add(chunk)
    await db_session.flush()
    stored = await db_session.scalar(
        select(EvidenceChunk.embedding).where(EvidenceChunk.id == chunk.id)
    )
    assert stored[:3] == [0.25, -0.5, 1.0] and len(stored) == EMBED_DIM  # exact in half precision


# --- fetch_url and search_evidence ------------------------------------------------------------


@pytest.fixture
async def office(committed, tmp_path):
    async with committed() as session:
        run = await running_agent_run(session, "search")
        company = await session.get(Company, run.company_id)
        await session.commit()
    fetcher = FixtureFetcher(FIXTURES, json.loads((FIXTURES / "routes.json").read_text()))
    blobs = LocalFSBlobStore(tmp_path)

    def registry(embedder=None):
        embedder = embedder or build_embedder(None)
        reg = ToolRegistry(committed)
        evidence_tools.register(reg, fetcher, blobs, embedder)
        return reg

    async def call(reg, tool, args, company_id=None):
        return await reg.invoke(
            tool,
            args,
            company_id=company_id or company.id,
            actor=Actor.system("test"),
            tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
            run_id=run.id,
            task_id=run.task_id,
            agent_id=run.agent_id,
            step_seq=uuid.uuid4().int % 100000,
        )

    return {
        "company": company,
        "run": run,
        "registry": registry,
        "call": call,
        "committed": committed,
    }


PAGES = [
    "https://news.fixtures.autora.test/lumen-city-microgrid-pilot",
    "https://city.fixtures.autora.test/press/2026-09-14-microgrid",
    "https://analysis.fixtures.autora.test/microgrid-costs",
    "https://blog.fixtures.autora.test/harbor-residents",
]


async def test_captured_evidence_is_chunked_and_embedded(office):
    reg = office["registry"]()
    result = await office["call"](reg, "fetch_url", {"url": PAGES[2]})
    assert result.ok, result.message
    async with office["committed"]() as session:
        chunks = (
            await session.scalars(
                select(EvidenceChunk)
                .where(EvidenceChunk.evidence_id == uuid.UUID(result.output["evidence_id"]))
                .order_by(EvidenceChunk.seq)
            )
        ).all()
        evidence = await session.get(Evidence, uuid.UUID(result.output["evidence_id"]))
        calls = (
            await session.scalars(
                select(ModelCall).where(
                    ModelCall.run_id == office["run"].id, ModelCall.alias == "embed"
                )
            )
        ).all()
    assert chunks and all(
        c.embedding_model == FAKE_EMBED_MODEL and len(c.embedding) == EMBED_DIM for c in chunks
    )
    for chunk in chunks:
        assert evidence.extracted_text[chunk.start : chunk.end].strip() == chunk.text
    assert (
        len(calls) == 1
        and calls[0].role == "researcher"
        and calls[0].task_id == office["run"].task_id
    )

    again = await office["call"](reg, "fetch_url", {"url": PAGES[2]})  # reused: nothing re-embedded
    assert again.output["reused"]
    async with office["committed"]() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ModelCall)
                .where(ModelCall.run_id == office["run"].id, ModelCall.alias == "embed")
            )
            == 1
        )


async def test_embedding_failure_still_captures_the_evidence(office):
    down = Embedder(provider=Scripted(EMBED_DIM, fail=True), model_id="down", dim=EMBED_DIM)
    reg = office["registry"](down)
    result = await office["call"](reg, "fetch_url", {"url": PAGES[3]})
    assert result.ok
    async with office["committed"]() as session:
        chunks = (
            await session.scalars(
                select(EvidenceChunk).where(
                    EvidenceChunk.evidence_id == uuid.UUID(result.output["evidence_id"])
                )
            )
        ).all()
        failed = await session.scalar(
            select(ModelCall).where(
                ModelCall.run_id == office["run"].id, ModelCall.status == "error"
            )
        )
    assert chunks and all(c.embedding is None and c.embedding_model is None for c in chunks)
    assert failed.error["error_class"] == "EmbeddingUnavailable"
    # and search falls back to keywords for them
    search = await office["call"](reg, "search_evidence", {"query": "fire safety inspected"})
    assert search.ok and search.output["method"] == "keyword"
    assert search.output["results"][0]["url"] == PAGES[3]


async def test_search_evidence_finds_passages(office):
    reg = office["registry"]()
    ids = {}
    for url in PAGES:
        result = await office["call"](reg, "fetch_url", {"url": url})
        ids[url] = result.output["evidence_id"]

    found = await office["call"](
        reg, "search_evidence", {"query": "battery capacity fades every year", "k": 3}
    )
    assert found.ok and found.output["method"] == "vector"
    top = found.output["results"][0]
    assert top["url"] == PAGES[2] and "fades by about 2% a year" in top["text"]
    assert {"evidence_id", "chunk", "start", "end", "score", "title"} <= set(top)
    assert "Quote evidence exactly" in found.output["note"]

    chinese = await office["call"](reg, "search_evidence", {"query": "微電網總經費", "k": 1})
    assert chinese.output["results"][0]["url"] == PAGES[1]

    only = await office["call"](
        reg, "search_evidence", {"query": "battery", "evidence_ids": [ids[PAGES[3]]]}
    )
    assert {r["evidence_id"] for r in only.output["results"]} == {ids[PAGES[3]]}

    async with office["committed"]() as session:
        other = await unique_company(session, "stranger")
        await session.commit()
    stranger = await office["call"](
        reg, "search_evidence", {"query": "battery"}, company_id=other.id
    )
    assert stranger.ok and stranger.output["results"] == []


def test_the_worker_has_search_evidence(committed, tmp_path):
    from autora.app import build_tools

    assert "search_evidence" in build_tools(committed, blobs=LocalFSBlobStore(tmp_path)).names()
