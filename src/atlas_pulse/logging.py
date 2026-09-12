"""Structured JSON logging configuration."""

import logging

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure stdlib and structlog for machine-readable production logs."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=numeric_level)
    logging.getLogger().setLevel(numeric_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
