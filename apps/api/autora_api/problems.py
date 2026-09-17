"""RFC 7807 problem+json errors for every failure (logs/platform/12_API_SPEC.md §慣例)."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

MEDIA_TYPE = "application/problem+json"


def problem(status: int, detail: str | None = None, **extra: object) -> JSONResponse:
    body = {"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status}
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=MEDIA_TYPE)


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        response = problem(exc.status_code, str(exc.detail) if exc.detail else None)
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        return problem(422, "request validation failed", errors=errors)
