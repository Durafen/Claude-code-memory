#!/usr/bin/env python3
"""
Main entry point for claude_indexer package.
Allows running the indexer as: python -m claude_indexer
"""

from claude_indexer.cli_full import cli

if __name__ == "__main__":
    cli()
