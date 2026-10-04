"""Structured logging.

Dynamic values are structured fields, never interpolated into the message, so an
operator can filter on client, aggregate, and revision without parsing prose.
"""

import logging
from typing import Final

import structlog
from structlog.typing import FilteringBoundLogger

__all__ = ["configure_logging", "get_logger"]

_CONFIGURED: Final[dict[str, bool]] = {"done": False}


def configure_logging(level: int) -> None:
    """Install the JSON renderer and level filter for this process."""
    if _CONFIGURED["done"]:
        return
    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED["done"] = True


def get_logger(name: str) -> FilteringBoundLogger:
    """Return a bound logger for one module."""
    logger: FilteringBoundLogger = structlog.get_logger(name)
    return logger
