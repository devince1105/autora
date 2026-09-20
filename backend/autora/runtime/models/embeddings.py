"""Embeddings: the ``embed`` binding (platform/09 §2-3, T-503).

Separate from chat completions: an embedding model is its own binding (``EMBED_PROVIDER``,
``EMBED_MODEL_ID``) and may come from a different provider than the agents' model. Callers ask an
``Embedder`` for vectors; it batches the texts, checks every vector has the dimension the caller's
storage expects, and records one ``model_calls`` row per provider call (alias ``embed``,
capability ``embedding``) in the caller's session, so embedding cost is attributed like any other
model cost.

Providers:
- ``OpenAICompatibleEmbeddings``: ``POST {base_url}/embeddings`` (NVIDIA Build and similar).
  NVIDIA's retrieval models distinguish queries from passages (``input_type``).
- ``HashingEmbeddings``: deterministic feature hashing (words, and character pairs for CJK), for
  ``EMBED_PROVIDER=fake``: no network, and texts that share words land close together, so
  retrieval in simulations and tests still means something.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import ModelCall, ModelCallStatus

Purpose = Literal["query", "passage"]
ALIAS = "embed"
CAPABILITY = "embedding"


class EmbeddingError(Exception):
    retryable = False


class EmbeddingUnavailable(EmbeddingError):
    retryable = True


@dataclass(frozen=True)
class EmbeddingOutput:
    vectors: list[list[float]]
    tokens: int


class EmbeddingProvider(Protocol):
    name: str

    async def embed(
        self, texts: Sequence[str], *, model_id: str, purpose: Purpose
    ) -> EmbeddingOutput: ...


# --- providers -----------------------------------------------------------------------------


class OpenAICompatibleEmbeddings:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        timeout_s: float = 60.0,
        input_type: bool = True,
        client: httpx.AsyncClient | None = None,
    ):
        self.name = name
        self._url = base_url.rstrip("/") + "/embeddings"
        self._key = api_key
        self._timeout = timeout_s
        self._input_type = input_type
        self._client = client

    async def embed(
        self, texts: Sequence[str], *, model_id: str, purpose: Purpose
    ) -> EmbeddingOutput:
        body: dict = {"model": model_id, "input": list(texts), "encoding_format": "float"}
        if self._input_type:
            body["input_type"] = purpose
        headers = {"Authorization": f"Bearer {self._key}"}
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._url, json=body, headers=headers, timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self._url, json=body, headers=headers, timeout=self._timeout
                    )
        except httpx.TimeoutException:
            raise EmbeddingUnavailable(
                f"{self.name} embeddings did not answer within {self._timeout}s"
            ) from None
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailable(f"{self.name} embeddings: {type(exc).__name__}") from None
        if response.status_code != 200:
            detail = response.text[:300]
            error = (
                EmbeddingUnavailable
                if response.status_code == 429 or response.status_code >= 500
                else EmbeddingError
            )
            raise error(f"{self.name} embeddings {response.status_code}: {detail}")
        data = response.json()
        rows = sorted(data.get("data", []), key=lambda row: row.get("index", 0))
        vectors = [list(map(float, row["embedding"])) for row in rows]
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"{self.name} returned {len(vectors)} vectors for {len(texts)} texts"
            )
        tokens = int((data.get("usage") or {}).get("prompt_tokens") or 0)
        return EmbeddingOutput(vectors=vectors, tokens=tokens)


_LATIN = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[㐀-鿿]+")


class HashingEmbeddings:
    """Feature hashing into ``dim`` buckets with signs, L2-normalised. Not semantic: shared words
    (or shared CJK character pairs) make texts similar, nothing else does."""

    name = "fake"

    def __init__(self, dim: int):
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        features = _LATIN.findall(lowered)
        for run in _CJK.findall(lowered):
            features += [run[i : i + 2] for i in range(max(1, len(run) - 1))]
        vector = [0.0] * self.dim
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            vector[bucket] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def embed(
        self, texts: Sequence[str], *, model_id: str, purpose: Purpose
    ) -> EmbeddingOutput:
        return EmbeddingOutput(
            vectors=[self._vector(t) for t in texts], tokens=sum(len(t) // 4 for t in texts)
        )


# --- the binding ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbedCaller:
    """Who the embedding is for (the model_calls row's attribution)."""

    company_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    cycle_id: uuid.UUID | None = None
    role: str = "system"


@dataclass(frozen=True)
class Embedder:
    provider: EmbeddingProvider
    model_id: str
    dim: int
    price_per_mtok: Decimal = Decimal(0)
    """USD per million input tokens (MODEL_PRICES[model]["input"]); 0 when unpriced (free)."""
    batch_size: int = 16

    async def embed(
        self, session: AsyncSession, texts: Sequence[str], *, purpose: Purpose, caller: EmbedCaller
    ) -> list[list[float]]:
        """Vectors for ``texts`` (in order). Raises EmbeddingError / EmbeddingUnavailable; every
        provider call, failed or not, is recorded in ``model_calls``."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            began = time.monotonic()
            try:
                output = await self.provider.embed(batch, model_id=self.model_id, purpose=purpose)
                wrong = next((len(v) for v in output.vectors if len(v) != self.dim), None)
                if wrong is not None:
                    raise EmbeddingError(
                        f"{self.model_id} returned {wrong}-dimensional vectors; "
                        f"storage expects {self.dim}"
                    )
            except EmbeddingError as exc:
                error = {"error_class": type(exc).__name__, "message": str(exc)[:2000]}
                await self._record(session, caller, began, tokens=0, error=error)
                raise
            await self._record(session, caller, began, tokens=output.tokens, error=None)
            vectors.extend(output.vectors)
        return vectors

    async def _record(
        self,
        session: AsyncSession,
        caller: EmbedCaller,
        began: float,
        *,
        tokens: int,
        error: dict | None,
    ) -> None:
        session.add(
            ModelCall(
                company_id=caller.company_id,
                project_id=caller.project_id,
                agent_id=caller.agent_id,
                task_id=caller.task_id,
                run_id=caller.run_id,
                workflow_run_id=caller.workflow_run_id,
                cycle_id=caller.cycle_id,
                role=caller.role,
                capability=CAPABILITY,
                alias=ALIAS,
                provider=self.provider.name,
                model_id=self.model_id,
                status=(ModelCallStatus.ERROR if error else ModelCallStatus.OK).value,
                stop_reason=None,
                tokens_in=tokens,
                tokens_out=0,
                cost_usd=(self.price_per_mtok * tokens / Decimal(1_000_000)).quantize(
                    Decimal("0.000001")
                ),
                latency_ms=int((time.monotonic() - began) * 1000),
                error=error,
            )
        )
        await session.flush()
