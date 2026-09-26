"""WS /ws/companies/{company_id}?token=...&since=... (T-303): the realtime stream.

The protocol lives in ``autora.realtime.gateway``; this module only adapts a Starlette
WebSocket to it and checks access. Browsers cannot set headers on a WebSocket, so the operator
token comes in the query string (MVP, logs/3d-office/05 §6). Errors are sent as an ERROR message
before closing, because a browser cannot read the reason of a refused handshake.
"""

from __future__ import annotations

import json
import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from autora.db.models import Company
from autora.infra.settings import Settings
from autora.realtime.gateway import CloseCode, EventHub
from autora_api.deps import ADMIN_COOKIE, admin_for, settings_dep

router = APIRouter(tags=["realtime"])


class _Socket:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def send_json(self, message: dict[str, Any]) -> None:
        await self.websocket.send_text(json.dumps(message, ensure_ascii=False))

    async def close(self, code: int = 1000) -> None:
        await self.websocket.close(code)


@router.websocket("/ws/companies/{company_id}")
async def company_stream(
    websocket: WebSocket,
    company_id: uuid.UUID,
    settings: Annotated[Settings, Depends(settings_dep)],
    token: Annotated[str | None, Query()] = None,
    since: Annotated[int | None, Query()] = None,
) -> None:
    await websocket.accept()
    socket = _Socket(websocket)
    hub: EventHub = websocket.app.state.hub

    expected = settings.api_bearer_token.get_secret_value()
    allowed = token is not None and secrets.compare_digest(token, expected)
    if not allowed:  # or an admin signed in with an emailed link: the cookie comes along (D-055)
        async with hub.session_factory() as session:
            allowed = (
                await admin_for(session, websocket.cookies.get(ADMIN_COOKIE), settings)
            ) is not None
            await session.commit()
    if not allowed:
        await socket.send_json(
            {"type": "ERROR", "code": "unauthorized", "message": "missing or invalid token"}
        )
        await socket.close(CloseCode.UNAUTHORIZED)
        return
    async with hub.session_factory() as session:
        exists = await session.get(Company, company_id) is not None
    if not exists:
        await socket.send_json(
            {"type": "ERROR", "code": "not_found", "message": f"company {company_id} not found"}
        )
        await socket.close(CloseCode.NOT_FOUND)
        return

    connection = await hub.connect(company_id, socket, since)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                message = json.loads(text)
            except ValueError:
                continue
            if isinstance(message, dict):
                await hub.receive(connection, message)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(connection)
