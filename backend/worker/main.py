"""Worker entry point (T-213): task loop + agent runner + maintenance + scheduler.

Run: ``python backend/worker/main.py`` (or the worker container). SIGTERM / SIGINT stop claiming
and let in-flight runs finish within the grace period; a hard kill is also safe, because
leases expire and another worker re-runs the task (T-215).
"""

import asyncio
import logging
import signal
import sys

import autora
from autora.app import build_worker
from autora.db.session import dispose_engine
from autora.infra.settings import SettingsError, get_settings

log = logging.getLogger("autora.worker")


async def _serve() -> None:
    settings = get_settings()
    worker = build_worker(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info(
        "autora worker %s starting (id=%s, env=%s, model_provider=%s)",
        autora.__version__,
        settings.worker_id,
        settings.autora_env,
        settings.model_provider,
    )
    try:
        await worker.run_forever(stop)
    finally:
        await dispose_engine()
    log.info("worker %s stopped", settings.worker_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        get_settings()
    except SettingsError as exc:
        log.error("%s", exc)
        sys.exit(2)
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
