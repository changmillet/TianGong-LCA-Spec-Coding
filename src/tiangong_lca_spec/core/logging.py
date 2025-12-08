"""Logging configuration helpers."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TextIO

import structlog

from .config import Settings, get_settings

_LOG_FILE_HANDLE: TextIO | None = None


def configure_logging(
    level: str | None = None,
    *,
    settings: Settings | None = None,
    log_path: Path | None = None,
) -> None:
    """Configure standard logging and structlog loggers."""
    resolved_settings = settings or get_settings()
    effective_level = level or resolved_settings.log_level
    numeric_level = getattr(logging, effective_level.upper(), logging.INFO)

    root_logger = logging.getLogger()

    global _LOG_FILE_HANDLE  # pylint: disable=global-statement
    if _LOG_FILE_HANDLE:
        try:
            _LOG_FILE_HANDLE.close()
        except Exception:  # pragma: no cover - best effort
            pass
        _LOG_FILE_HANDLE = None

    handler_stream: TextIO | None = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _LOG_FILE_HANDLE = log_path.open("a", encoding="utf-8")
        handler_stream = _LOG_FILE_HANDLE
        handler = logging.StreamHandler(handler_stream)
        handlers = [handler]
    else:
        handlers = None

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=numeric_level,
        handlers=handlers,
        force=True,
    )
    root_logger.setLevel(numeric_level)

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        cache_logger_on_first_use=True,
        logger_factory=structlog.PrintLoggerFactory(file=handler_stream or sys.stdout),
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
