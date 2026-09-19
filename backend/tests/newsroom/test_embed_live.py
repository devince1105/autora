"""T-503 live smoke: the configured embedding model (EMBED_PROVIDER=nvidia, EMBED_MODEL_ID).

Run with ``pytest backend -m integration``. Skipped unless a real embed binding is configured.
Checks the dimension the database stores, that one call is recorded in model_calls, and that
retrieval works across languages (a Chinese question ranks the matching English passage above an
unrelated one), which the bilingual newsroom relies on.
"""

import math

import pytest
from sqlalchemy import select

from autora.db.models import ModelCall
from autora.domains.newsroom.models import EMBED_DIM
from autora.infra.settings import SettingsError, load_settings
from autora.runtime.models.embeddings import EmbedCaller
from autora.runtime.models.factory import embedder_from_settings
from tests.conftest import running_agent_run


def _live():
    try:
        settings = load_settings()
    except SettingsError:
        return None
    return settings if settings.embed_provider != "fake" else None


SETTINGS = _live()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(SETTINGS is None, reason="needs EMBED_PROVIDER / EMBED_MODEL_ID"),
]


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


async def test_real_embeddings(db_session):
    run = await running_agent_run(db_session, "embed-live")
    embedder = embedder_from_settings(SETTINGS, dim=EMBED_DIM)
    caller = EmbedCaller(company_id=run.company_id, run_id=run.id, role="researcher")
    [question] = await embedder.embed(
        db_session, ["停電時微電網可以供電多久？"], purpose="query", caller=caller
    )
    relevant, unrelated = await embedder.embed(
        db_session,
        [
            "The microgrid can keep about 3,000 homes powered for six hours during an outage.",
            "Wholesale arabica coffee prices rose 18% this quarter.",
        ],
        purpose="passage",
        caller=caller,
    )
    assert len(question) == EMBED_DIM
    assert _cos(question, relevant) > _cos(question, unrelated) + 0.2
    calls = (await db_session.scalars(select(ModelCall).where(ModelCall.run_id == run.id))).all()
    assert [(c.alias, c.status, c.model_id) for c in calls] == [
        ("embed", "ok", SETTINGS.embed_model_id)
    ] * 2
