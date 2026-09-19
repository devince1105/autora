"""Shared newsroom fixtures: a company with an agent run, a selected story, captured evidence and
two supported claims, and a way to call the newsroom tools as that run (real registry, real
transactions, fixture pages, fake embeddings)."""

import itertools
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

import autora.domains.newsroom as newsroom
from autora.app import build_embedder
from autora.db.models import Company
from autora.domains.newsroom.models import Story
from autora.domains.newsroom.tools import register_tools
from autora.infra.blobstore import LocalFSBlobStore
from autora.infra.http import FixtureFetcher
from autora.infra.search.fixture import FixtureSearchProvider
from autora.runtime.actor import Actor
from autora.runtime.tools import ToolRegistry
from tests.conftest import running_agent_run

FIXTURES = Path(newsroom.__file__).parent / "fixtures"
PILOT = "https://news.fixtures.autora.test/lumen-city-microgrid-pilot"
PRESS = "https://city.fixtures.autora.test/press/2026-09-14-microgrid"


@dataclass
class Newsroom:
    company: Company
    story: Story
    claims: dict[str, str]
    evidence: dict[str, str]
    committed: object
    call: object

    def draft(self, claim_ids: list[str], langs=("zh-TW", "en")) -> dict:
        titles = {
            "zh-TW": "流明市首座社區微電網啟用",
            "en": "Lumen City switches on its first microgrid",
        }
        return {
            "story_id": str(self.story.id),
            "versions": [
                {
                    "lang": lang,
                    "title": titles[lang],
                    "blocks": [
                        {"type": "paragraph", "text": f"{lang} paragraph {i}", "claim_ids": [c]}
                        for i, c in enumerate(claim_ids)
                    ],
                }
                for lang in langs
            ],
        }


@pytest.fixture
async def newsroom_room(committed, tmp_path) -> Newsroom:
    async with committed() as session:
        run = await running_agent_run(session, "newsroom")
        company = await session.get(Company, run.company_id)
        story = Story(company_id=company.id, title="Lumen City microgrid", state="SELECTED")
        session.add(story)
        await session.commit()
    registry = ToolRegistry(committed)
    register_tools(
        registry,
        search_provider=FixtureSearchProvider([]),
        fetcher=FixtureFetcher(FIXTURES, json.loads((FIXTURES / "routes.json").read_text())),
        blobs=LocalFSBlobStore(tmp_path),
        embedder=build_embedder(None),
    )
    steps = itertools.count(1)

    async def call(tool, args, *, company_id=None, step=None):
        return await registry.invoke(
            tool,
            args,
            company_id=company_id or company.id,
            actor=Actor.system("test"),
            tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
            run_id=run.id,
            task_id=run.task_id,
            agent_id=run.agent_id,
            step_seq=step if step is not None else next(steps),
        )

    evidence = {
        url: (await call("fetch_url", {"url": url})).output["evidence_id"] for url in (PILOT, PRESS)
    }
    claims = {}
    for name, text, url, quote in (
        (
            "panels",
            "The microgrid links 1,200 rooftop panels.",
            PILOT,
            "links 1,200 rooftop solar panels",
        ),
        (
            "cost",
            "The city spent NT$420 million building it.",
            PRESS,
            "本計畫總經費新台幣 4.2 億元",
        ),
    ):
        made = await call(
            "create_claim",
            {
                "story_id": str(story.id),
                "text": text,
                "claim_type": "number",
                "evidence": [{"evidence_id": evidence[url], "quote": quote}],
            },
        )
        assert made.ok, made.message
        claims[name] = made.output["claim_id"]
    return Newsroom(
        company=company,
        story=story,
        claims=claims,
        evidence=evidence,
        committed=committed,
        call=call,
    )
