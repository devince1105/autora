"""What the runtime can do, for forms that must not offer the impossible (T-517 follow-up).

``GET /api/roles``: the roles that have a behavior, so hiring an agent that would never pick up a
task is not offered in the first place.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from autora.app import build_behaviors
from autora_api.deps import Operator

router = APIRouter(prefix="/api", tags=["meta"])


class Roles(BaseModel):
    roles: list[str]


@router.get("/roles")
async def list_roles(_: Operator) -> Roles:
    return Roles(roles=build_behaviors().roles())
