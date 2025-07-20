"""Centralized logging configuration for the indexer."""

import logging
from pathlib import Path


def get_default_log_file(
    collection_name: str | None = None, project_path: Path | None = None
) -> Path:
    """Get the default log file path, optionally per collection and project."""
    if project_path:
        # Use project directory for logs
        log_dir = project_path / "logs"
    else:
        # Fallback to home directory
        log_dir = Path.home() / ".claude-indexer" / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)

    if collection_name:
        return log_dir / f"{collection_name}.log"
    else:
        return log_dir / "claude-indexer.log"


def clear_log_file(
    collection_name: str | None = None, project_path: Path | None = None
) -> bool:
    """Clear the log file for a collection."""
    try:
        log_file = get_default_log_file(collection_name, project_path)
        if log_file.exists():
            log_file.unlink()
            return True
        return True  # File doesn't exist, consider it cleared
    except Exception:
        return False


def setup_logging(
    level: str = "INFO",
    quiet: bool = False,
    verbose: bool = False,
    log_file: Path | None = None,
    enable_file_logging: bool = True,
    collection_name: str | None = None,
    project_path: Path | None = None,
    **kwargs,
) -> "logging.Logger":
    """Setup global logging configuration."""
    import logging.config

    # Use collection-specific log file if none specified and file logging is enabled
    if log_file is None and enable_file_logging:
        log_file = get_default_log_file(collection_name, project_path)

    # Determine effective level
    if quiet:
        effective_level = "ERROR"
    elif verbose:
        effective_level = "DEBUG"
    else:
        effective_level = level

    # Build dictConfig
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "format": "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "simple": {"format": "%(levelname)s | %(message)s"},
        },
        "handlers": {},
        "loggers": {
            "claude_indexer": {"handlers": [], "level": "DEBUG", "propagate": False}
        },
    }

    # Console handler
    if not quiet:
        config["handlers"]["console"] = {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "level": effective_level,
            "stream": "ext://sys.stderr",
        }
        config["loggers"]["claude_indexer"]["handlers"].append("console")

    # File handler with rotation
    if log_file:
        config["handlers"]["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "detailed",
            "level": "DEBUG",
            "filename": str(log_file),
            "maxBytes": 10485760,  # 10MB
            "backupCount": 7,
        }
        config["loggers"]["claude_indexer"]["handlers"].append("file")

    logging.config.dictConfig(config)
    return logging.getLogger("claude_indexer")


def get_logger() -> "logging.Logger":
    """Get the global logger instance."""
    return logging.getLogger("claude_indexer")
