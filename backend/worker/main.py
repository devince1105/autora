"""Worker entry point. Scheduler + event dispatcher + task loop are wired here (T-213).

Until the runtime exists this only proves the process boots with valid configuration.
"""

import logging
import sys
import time

import autora
from autora.infra.settings import SettingsError, get_settings

log = logging.getLogger("autora.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        settings = get_settings()
    except SettingsError as exc:
        log.error("%s", exc)
        sys.exit(2)
    log.info(
        "autora worker %s starting (env=%s, model_provider=%s; no runtime wired yet)",
        autora.__version__,
        settings.autora_env,
        settings.model_provider,
    )
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
