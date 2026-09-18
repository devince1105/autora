from __future__ import annotations

from typing import Protocol

from autora.runtime.models.router import ModelBinding
from autora.runtime.models.types import ModelRequest, ProviderResponse


class ProviderError(Exception):
    """A model call that did not produce a response.

    ``fallback_allowed``: the failure is about this provider/model (outage, overload, rate limit),
    so trying the alias's fallback makes sense. Invalid requests and auth errors are not.
    """

    def __init__(self, error_class: str, message: str, *, fallback_allowed: bool):
        self.error_class = error_class
        self.message = message
        self.fallback_allowed = fallback_allowed
        super().__init__(f"{error_class}: {message}")


class ModelProvider(Protocol):
    name: str

    async def complete(self, request: ModelRequest, binding: ModelBinding) -> ProviderResponse: ...
