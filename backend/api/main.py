"""ASGI entry point: `uvicorn --app-dir backend/api main:app`."""

from autora_api.app import create_app

app = create_app()
