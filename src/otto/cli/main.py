"""otto entry point."""

from __future__ import annotations

import logging
import signal
import sys

from ..core import agent_config as provider
from ..core.session import SessionManager
from .ui import AgentCliApp

logger = logging.getLogger("otto.main")


def main() -> int:
    try:
        logger.debug("Starting otto")
        mgr = SessionManager()
    except provider.ProviderConfigError as e:
        logger.error("Provider configuration error: %s", e)
        print(f"otto: {e}", file=sys.stderr)
        return 2

    def _sigint_handler(sig: int, frame: object) -> None:
        """Flush index on SIGINT before Textual handles the exit."""
        logger.debug("SIGINT received, flushing session index")
        mgr.flush_index()
        # Restore default handler and re-raise
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        import os
        os.kill(os.getpid(), signal.SIGINT)

    signal.signal(signal.SIGINT, _sigint_handler)

    app = AgentCliApp(mgr)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
