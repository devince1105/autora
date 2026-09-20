"""The newsroom's own settings (ARCHITECTURE_V2_1 §9).

The core parses the environment for what the core owns — the database, the model provider, the
blob store. A threshold for deciding when two news items are about the same event is not one of
those: it means nothing to a company that sells software, and the core should not carry it.

So the newsroom reads the same ``.env`` for its own keys, prefixed with its name, and validates
them itself. A second domain does the same and never touches this file.

    NEWSROOM_STORY_MATCH_THRESHOLD=0.65
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from autora.infra.settings import env_file

DEFAULT_STORY_MATCH_THRESHOLD = 0.65
"""Chosen by measurement, not by taste: at 0.65 two reports of one event in the same language
merge, and "related but different" stays apart (see stories.py, and the live-model check in
tests/newsroom/test_embed_live.py that re-runs the measurement when the model changes)."""


class NewsroomSettings(BaseSettings):
    """What the newsroom can be tuned with. Keys are ``NEWSROOM_*`` in the environment."""

    model_config = SettingsConfigDict(
        env_file=env_file(),
        env_file_encoding="utf-8",
        env_prefix="NEWSROOM_",
        extra="ignore",
        case_sensitive=False,
    )

    story_match_threshold: float = Field(default=DEFAULT_STORY_MATCH_THRESHOLD, gt=0, le=1)
    """Cosine similarity at which a source item joins an existing story (T-504). Tied to the
    embedding model: change ``EMBED_MODEL_ID`` and this has to be measured again."""


@lru_cache(maxsize=1)
def get_newsroom_settings() -> NewsroomSettings:
    return NewsroomSettings()
