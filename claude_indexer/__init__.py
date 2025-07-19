"""Claude Code Memory Indexer - Modular Universal Indexer Package

A modular, scalable solution for indexing Python codebases with semantic search capabilities.
Supports multiple automation modes including file watching, service management, and git hooks.
"""

__version__ = "0.2.0"
__author__ = "Claude Code Memory Project"
__description__ = "Universal semantic indexer for Python codebases with vector search"

from claude_indexer.config import IndexerConfig, load_config

from .analysis.entities import Entity, Relation
from .main import main as cli_main

__all__ = [
    "IndexerConfig",
    "load_config",
    "Entity",
    "Relation",
    "cli_main",
]
