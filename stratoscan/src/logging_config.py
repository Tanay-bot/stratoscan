"""Logging configuration for Stratoscan.

Provides structured logging with two modes:
  - text (default): Colored, human-readable console output for development.
  - json: Machine-parseable JSON lines for production (Datadog, CloudWatch, etc.).

Usage:
    from src.logging_config import configure_logging
    configure_logging()
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from src.config import settings

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "text"  # "text" or "json"

# ANSI color codes for console output
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"

_LEVEL_COLORS = {
    "DEBUG": _DIM,
    "INFO": _GREEN,
    "WARNING": _YELLOW,
    "ERROR": _RED,
    "CRITICAL": _RED + _BOLD,
}


# ═══════════════════════════════════════════════════════════════════════════════
# JSON Formatter (Production)
# ═══════════════════════════════════════════════════════════════════════════════

class JSONFormatter(logging.Formatter):
    """Outputs log records as JSON lines for machine parsing.

    Each line is a valid JSON object with keys:
      timestamp, level, logger, message, module, function, line,
      and optionally exc_info, extra fields.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include any extra fields passed via extra={}
        for key, value in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "levelname", "levelno", "lineno",
                "module", "msecs", "message", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName",
            ):
                log_entry[key] = value

        return json.dumps(log_entry, default=str)


# ═══════════════════════════════════════════════════════════════════════════════
# Colored Console Formatter (Development)
# ═══════════════════════════════════════════════════════════════════════════════

class ColoredConsoleFormatter(logging.Formatter):
    """Human-readable console output with ANSI colors.

    Example:
        2026-07-30 19:30:00  INFO     src.api          Screen request: 'tech stocks' (limit=50)
        2026-07-30 19:30:01  WARNING  src.config        DATABASE_URL is missing.
        2026-07-30 19:30:02  ERROR    src.screener      Screening query failed: connection refused
    """

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")

        level = record.levelname
        level_color = _LEVEL_COLORS.get(level, "")
        level_padded = f"{level:<8}"

        logger_name = record.name
        # Shorten common prefixes
        if logger_name.startswith("src."):
            logger_name = logger_name[4:]

        message = record.getMessage()

        # Build the line
        parts = [
            f"{_DIM}{timestamp}{_RESET}",
            f"{level_color}{level_padded}{_RESET}",
            f"{_CYAN}{logger_name:<20}{_RESET}",
            message,
        ]

        formatted = "  ".join(parts)

        # Append exception traceback if present
        if record.exc_info and record.exc_info[0] is not None:
            formatted += "\n" + self.formatException(record.exc_info)

        return formatted


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

_logging_configured: bool = False


def get_log_level() -> str:
    """Resolve the log level from env var or fallback to default.

    Priority:
      1. LOG_LEVEL environment variable
      2. settings.log_level (from .env)
      3. DEFAULT_LOG_LEVEL ("INFO")
    """
    # Check settings first (loaded from .env via pydantic)
    level = settings.log_level or os.environ.get("LOG_LEVEL") or DEFAULT_LOG_LEVEL
    return level.upper()


def get_log_format() -> str:
    """Resolve the log format from env var or fallback to default.

    Priority:
      1. LOG_FORMAT environment variable
      2. settings.log_format (from .env)
      3. DEFAULT_LOG_FORMAT ("text")
    """
    fmt = settings.log_format or os.environ.get("LOG_FORMAT") or DEFAULT_LOG_FORMAT
    return fmt.lower()


def configure_logging(
    level: Optional[str] = None,
    log_format: Optional[str] = None,
    log_file: Optional[str] = None,
    *,
    force: bool = False,
) -> None:
    """Configure logging for the entire Stratoscan application.

    Call this once at application startup. Subsequent calls are no-ops
    unless ``force=True``.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               Defaults to ``LOG_LEVEL`` env var or ``INFO``.
        log_format: Output format (``text`` or ``json``).
                    Defaults to ``LOG_FORMAT`` env var or ``text``.
        log_file: Optional path to a log file. If set, logs are written
                  to both stdout and the file (with rotation).
        force: Reconfigure even if already configured.
    """
    global _logging_configured

    if _logging_configured and not force:
        return

    resolved_level = (level or get_log_level()).upper()
    resolved_format = (log_format or get_log_format()).lower()
    resolved_file = log_file or settings.log_file or None

    # Validate
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if resolved_level not in valid_levels:
        print(
            f"⚠️  Invalid LOG_LEVEL '{resolved_level}', falling back to INFO",
            file=sys.stderr,
        )
        resolved_level = "INFO"

    if resolved_format not in ("text", "json"):
        print(
            f"⚠️  Invalid LOG_FORMAT '{resolved_format}', falling back to text",
            file=sys.stderr,
        )
        resolved_format = "text"

    # Create the appropriate formatter
    if resolved_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = ColoredConsoleFormatter()

    # Root logger — catch everything
    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_level)

    # Remove existing handlers to avoid duplicates on reconfig
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Optional file handler with rotation
    if resolved_file:
        log_path = Path(resolved_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            max_bytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
        )
        # Use JSON format for files regardless of console format
        file_formatter = JSONFormatter()
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for noisy_logger in ("finnhub", "urllib3", "yfinance", "httpx"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    _logging_configured = True

    logging.getLogger(__name__).debug(
        "Logging configured: level=%s, format=%s, file=%s",
        resolved_level,
        resolved_format,
        resolved_file or "none",
    )


def configure_uvicorn_logging() -> None:
    """Patch uvicorn's loggers to use Stratoscan's logging config.

    Call after ``configure_logging()`` to make uvicorn's access logs
    use the same format as the rest of the application.
    """
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_error = logging.getLogger("uvicorn.error")

    # Remove uvicorn's default handlers
    for logger in (uvicorn_access, uvicorn_error):
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    # These loggers will propagate to the root logger which has our formatter
    uvicorn_access.propagate = True
    uvicorn_error.propagate = True

    # Set reasonable levels
    uvicorn_access.setLevel(logging.INFO)
    uvicorn_error.setLevel(logging.WARNING)


# Quick test when run directly
if __name__ == "__main__":
    configure_logging(level="DEBUG", force=True)

    log = logging.getLogger(__name__)
    log.debug("This is a debug message")
    log.info("This is an info message")
    log.warning("This is a warning message")
    log.error("This is an error message")

    try:
        1 / 0
    except ZeroDivisionError:
        log.exception("Caught an exception")

    # Test JSON mode
    print("\n--- JSON mode ---")
    configure_logging(level="DEBUG", log_format="json", force=True)
    log.info("JSON formatted log entry", extra={"request_id": "abc-123", "user": "demo"})
