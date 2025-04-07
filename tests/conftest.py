from typing import Optional, Any, AsyncGenerator, Coroutine

import pytest
import logging

from setup_logging import setup_logging, LoggingConfig

logger = logging.getLogger(__name__)

# Configure logging with custom levels
console_handler = setup_logging(LoggingConfig(
    root_level=logging.DEBUG,
    namespace_levels={
        "root": logging.DEBUG,
        "conftest": logging.DEBUG,
        "asyncio": logging.CRITICAL + 1,  # trick to disable logger
        "tests": logging.INFO,
        "httpx": logging.WARNING,
    },
))
