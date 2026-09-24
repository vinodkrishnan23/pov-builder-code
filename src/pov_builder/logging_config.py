"""Structured logging setup — same convention as the sibling Magenta agents
in this workspace (chat-agent, spec-architect, coding-orchestrator), kept
consistent even though this project doesn't depend on the Magenta SDK.
"""

from __future__ import annotations

import logging
import os
import sys


def configure_logging() -> logging.Logger:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    return logging.getLogger("pov_builder")
