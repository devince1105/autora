"""Seed the demo newsroom and (optionally) start a story through the whole line (T-518).

    python backend/scripts/seed_newsroom.py                      # company, project, desks, sources
    python backend/scripts/seed_newsroom.py --gather             # + poll and cluster (no start)
    python backend/scripts/seed_newsroom.py --start              # + poll, cluster, start a story
    python backend/scripts/seed_newsroom.py --start --pace 4 --revise

Meant for simulation mode (``MODEL_PROVIDER=fake``, ``TOOLS_PROFILE=fixture``, and
``EMBED_PROVIDER=fake`` to stay offline): the sources are
the fixture feeds of fictional Lumen City, which exist only in fixture mode. Idempotent: re-running
reuses the ``newsroom-demo`` company. ``--start`` polls the sources now, clusters the new items
into stories, selects the best story whose title mentions ``--story`` (default ``microgrid``) and
starts its workflow; a running worker (``backend/worker/main.py``) then works it. The line
stops at the approval inbox: approve it there to publish.

``--pace`` makes every simulated reply take that many seconds (to watch the office); ``--revise``
makes the editor send the first draft back once.
"""

import argparse
import asyncio
import json
import sys

from autora.app import (
    build_embedder,
    build_page_fetcher,
    build_runtime,
    build_search_provider,
)
from autora.db.session import dispose_engine, get_sessionmaker
from autora.domains.newsroom.demo import gather_stories, pick, seed_demo, start_demo_story
from autora.domains.newsroom.sources import SourcePoller
from autora.domains.newsroom.stories import StoryDesk
from autora.infra.settings import load_settings
from autora.runtime.actor import Actor

ACTOR = Actor.human("seed_newsroom")


async def seed(
    start: bool, story_keyword: str, pace: float, revise: bool, gather: bool = False
) -> dict[str, object]:
    settings = load_settings()
    now = (settings.model_provider, settings.tools_profile, settings.embed_provider)
    if now != ("fake", "fixture", "fake"):
        print(
            "note: the demo sources exist only with TOOLS_PROFILE=fixture, and the demo is meant "
            "to run offline: MODEL_PROVIDER=fake TOOLS_PROFILE=fixture EMBED_PROVIDER=fake "
            f"(now {' / '.join(now)})",
            file=sys.stderr,
        )
    runtime = build_runtime(settings)
    async with get_sessionmaker()() as session:
        demo = await seed_demo(session, actor=ACTOR)
        out: dict[str, object] = {
            "company_id": str(demo.company.id),
            "project_id": str(demo.project.id),
            "sources": [s.name for s in demo.sources],
        }
        if start or gather:
            poller = SourcePoller(
                fetcher=build_page_fetcher(settings), search=build_search_provider(settings)
            )
            desk = StoryDesk(build_embedder(settings), threshold=settings.story_match_threshold)
            stories = await gather_stories(session, demo.company.id, poller=poller, desk=desk)
            out["stories"] = [s.title for s in stories]
        if start:
            story = pick(stories, story_keyword)
            if story is None:
                titles = [s.title for s in stories]
                raise SystemExit(f"no open story mentions {story_keyword!r}; open: {titles}")
            run = await start_demo_story(
                session,
                policy=runtime.policy,
                workflows=runtime.workflows,
                desk=desk,
                story=story,
                project_id=demo.project.id,
                actor=ACTOR,
                pace_seconds=pace,
                revise_first_review=revise,
            )
            out |= {"story": story.title, "story_id": str(story.id), "workflow_run_id": str(run.id)}
        await session.commit()
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gather", action="store_true", help="poll and cluster now (no start)")
    parser.add_argument("--start", action="store_true", help="poll, cluster and start a story")
    parser.add_argument("--story", default="microgrid", help="a word of the story's title")
    parser.add_argument("--pace", type=float, default=0, help="seconds per simulated reply")
    parser.add_argument("--revise", action="store_true", help="the first review asks a revision")
    args = parser.parse_args()
    try:
        out = await seed(args.start, args.story, args.pace, args.revise, args.gather)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
