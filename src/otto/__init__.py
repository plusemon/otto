__version__ = "0.1.0"

import logging
import os
import sys


def _configure_logging() -> None:
    level_name = os.environ.get("OTTO_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger("otto")
    root.setLevel(level)
    root.addHandler(handler)


_configure_logging()
