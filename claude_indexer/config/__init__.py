"""Unified configuration package."""

# Main configuration exports
from .config_loader import ConfigLoader, load_config

# Project configuration exports
from .config_schema import (
    FilePatterns,
    IndexingConfig,
    JavaScriptParserConfig,
    JSONParserConfig,
    MarkdownParserConfig,
    ParserConfig,
    ProjectConfig,
    ProjectInfo,
    TextParserConfig,
    WatcherConfig,
    YAMLParserConfig,
)
from .legacy import create_default_settings_file, load_legacy_settings
from .models import IndexerConfig
from .project_config import ProjectConfigManager

__all__ = [
    # Main configuration
    "IndexerConfig",
    "load_config",
    "load_legacy_settings",
    "create_default_settings_file",
    "ConfigLoader",
    # Project configuration
    "ProjectConfig",
    "ProjectInfo",
    "IndexingConfig",
    "WatcherConfig",
    "FilePatterns",
    "ParserConfig",
    "JavaScriptParserConfig",
    "JSONParserConfig",
    "TextParserConfig",
    "YAMLParserConfig",
    "MarkdownParserConfig",
    "ProjectConfigManager",
]
