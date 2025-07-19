"""Unified configuration loading with project support."""

import os
from pathlib import Path
from typing import Any

from ..indexer_logging import get_logger
from .legacy import load_legacy_settings
from .models import IndexerConfig
from .project_config import ProjectConfigManager

logger = get_logger()


class ConfigLoader:
    """Unified configuration loader with project-level support."""

    def __init__(self, project_path: Path | None = None):
        self.project_path = Path(project_path) if project_path else Path.cwd()
        self.project_manager = ProjectConfigManager(self.project_path)
        self._merged_config: Any | None = None

    def load(self, **overrides: Any) -> IndexerConfig:
        """Load unified configuration from all sources.

        Precedence (highest to lowest):
        1. Explicit overrides
        2. Environment variables
        3. Project config (.claude-indexer/config.json)
        4. Global settings.txt
        5. Defaults
        """
        config_dict = {}

        # 1. Load global settings.txt
        settings_file = Path(__file__).parent.parent.parent / "settings.txt"
        if settings_file.exists():
            legacy_settings = load_legacy_settings(settings_file)
            config_dict.update(legacy_settings)
            logger.debug(f"Loaded {len(legacy_settings)} settings from {settings_file}")

        # 2. Apply environment variables
        env_vars = {
            "openai_api_key": os.environ.get("OPENAI_API_KEY"),
            "voyage_api_key": os.environ.get("VOYAGE_API_KEY"),
            "qdrant_api_key": os.environ.get("QDRANT_API_KEY"),
            "qdrant_url": os.environ.get("QDRANT_URL"),
            "embedding_provider": os.environ.get("EMBEDDING_PROVIDER"),
            "voyage_model": os.environ.get("VOYAGE_MODEL"),
        }
        env_count = 0
        for key, value in env_vars.items():
            if value is not None:
                config_dict[key] = value
                env_count += 1
        if env_count > 0:
            logger.debug(f"Applied {env_count} environment variables")

        # 3. Apply project config overrides
        try:
            # Auto-create config on first run (like init command)
            if not self.project_manager.exists:
                project_name = self.project_path.name
                collection_name = config_dict.get("collection_name", project_name)
                project_config = self.project_manager.create_default(
                    project_name, collection_name
                )
                self.project_manager.save(project_config)
                logger.info(
                    f"Created default project config at {self.project_manager.config_path}"
                )

            if self.project_manager.exists:
                project_config = self.project_manager.load()
                project_overrides = self._extract_overrides(project_config)
                config_dict.update(project_overrides)
                logger.debug(
                    f"Applied {len(project_overrides)} project config settings"
                )
        except Exception as e:
            logger.warning(f"Failed to load project config: {e}")

        # 4. Apply explicit overrides (highest priority)
        config_dict.update(overrides)
        if overrides:
            logger.debug(f"Applied {len(overrides)} explicit overrides")

        # 5. Create IndexerConfig with merged settings
        try:
            return IndexerConfig(**config_dict)
        except Exception as e:
            logger.warning(f"Configuration validation failed: {e}, using defaults")
            # Create with defaults and apply valid overrides
            config = IndexerConfig()
            for key, value in config_dict.items():
                if hasattr(config, key):
                    try:
                        setattr(config, key, value)
                    except (ValueError, TypeError, AttributeError) as e:
                        logger.warning(f"Ignoring invalid setting {key}={value}: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error setting {key}={value}: {e}")
            return config

    def _extract_overrides(self, project_config: Any) -> dict[str, Any]:
        """Extract IndexerConfig-compatible overrides from project config."""
        overrides = {}

        # File patterns
        if project_config.indexing.file_patterns:
            overrides["include_patterns"] = (
                project_config.indexing.file_patterns.include
            )
            overrides["exclude_patterns"] = (
                project_config.indexing.file_patterns.exclude
            )

        # File size limit
        if project_config.indexing.max_file_size:
            overrides["max_file_size"] = project_config.indexing.max_file_size

        # Watcher settings
        if project_config.watcher.debounce_seconds:
            overrides["debounce_seconds"] = project_config.watcher.debounce_seconds

        # Collection name from project
        if project_config.project.collection:
            overrides["collection_name"] = project_config.project.collection

        return overrides

    def get_parser_config(self, parser_name: str) -> dict[str, Any]:
        """Get parser-specific configuration."""
        if self.project_manager.exists:
            try:
                return self.project_manager.get_parser_config(parser_name)
            except (FileNotFoundError, KeyError) as e:
                logger = get_logger()
                logger.warning(f"Parser config not found for {parser_name}: {e}")
            except Exception as e:
                logger = get_logger()
                logger.error(
                    f"Unexpected error getting parser config for {parser_name}: {e}"
                )
        return {}


def load_config(settings_file: Path | None = None, **overrides: Any) -> IndexerConfig:
    """Load configuration from multiple sources with precedence.

    Maintains backward compatibility with old signature.

    Args:
        settings_file: Path to settings.txt file OR project directory (auto-detected)
        **overrides: Explicit configuration overrides

    Returns:
        Configured IndexerConfig instance
    """
    # Auto-detect if settings_file is actually a project directory
    project_path = None
    explicit_settings_file = None

    if settings_file is not None:
        if settings_file.is_dir():
            # It's a project directory
            project_path = settings_file
        elif settings_file.name == "settings.txt" and settings_file.exists():
            # It's an explicit settings file path
            explicit_settings_file = settings_file
            project_path = (
                settings_file.parent
                if settings_file.parent != Path(__file__).parent.parent.parent
                else None
            )
        else:
            # Default behavior - treat as project directory
            project_path = settings_file

    loader = ConfigLoader(project_path)

    # If explicit settings file provided, override the default path
    if explicit_settings_file:
        # Temporarily modify the loader to use explicit settings file
        original_load = loader.load

        def custom_load(**overrides: Any) -> IndexerConfig:
            config_dict = {}

            # Load from explicit settings file
            if explicit_settings_file.exists():
                legacy_settings = load_legacy_settings(explicit_settings_file)
                config_dict.update(legacy_settings)

            # Continue with normal project + env + overrides flow
            config = original_load(**overrides)

            # Apply settings file to base config
            for key, value in config_dict.items():
                if hasattr(config, key):
                    setattr(config, key, value)

            return config

        return custom_load(**overrides)

    return loader.load(**overrides)
