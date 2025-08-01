"""CLI interface for the Claude Code indexer with graceful dependency handling."""

import sys

from .indexer_logging import get_logger

logger = get_logger()


def cli():
    """Claude Code Memory Indexer - Universal semantic indexing for codebases."""
    try:
        # Try to import Click and the full CLI
        import click  # noqa: F401

        from . import cli_full

        # Backward compatibility: auto-add 'index' if first arg isn't a subcommand
        if len(sys.argv) > 1 and sys.argv[1] not in [
            "hooks",
            "watch",
            "service",
            "search",
            "file",
            "add-mcp",
            "chat",
            "index",
            "init",
            "--help",
            "-h",
            "--version",
        ]:
            sys.argv.insert(1, "index")

        return cli_full.cli()
    except ImportError:
        logger.error("❌ Missing dependencies for CLI functionality")
        logger.error("   Install with: pip install click watchdog")
        logger.error("   Or install all dependencies: pip install -r requirements.txt")
        sys.exit(1)


# For direct module execution
if __name__ == "__main__":
    cli()

# Export the main CLI function for package use
__all__ = ["cli"]
