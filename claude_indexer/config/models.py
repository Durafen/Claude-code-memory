"""Configuration models for both global and project settings."""

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, validator

from ..indexer_logging import get_logger

logger = get_logger()


class IndexerConfig(BaseModel):
    """Global configuration model with validation."""

    # API Keys
    openai_api_key: str = Field(default="")
    voyage_api_key: str = Field(default="")
    qdrant_api_key: str = Field(default="default-key")

    # URLs and Endpoints
    qdrant_url: str = Field(default="http://localhost:6333")

    # Collection Management
    collection_name: str = Field(default="default")

    # Component Types
    embedder_type: str = Field(default="openai")
    embedding_provider: str = Field(default="openai")  # For compatibility
    voyage_model: str = Field(default="voyage-3-lite")
    storage_type: str = Field(default="qdrant")

    # Indexing Behavior
    indexer_debug: bool = Field(default=False)
    indexer_verbose: bool = Field(default=True)
    debounce_seconds: float = Field(default=2.0)

    # Watcher Settings
    include_patterns: list = Field(default_factory=lambda: ["*.py", "*.md"])
    exclude_patterns: list = Field(
        default_factory=lambda: [
            "*.pyc",
            "__pycache__/",
            ".git/",
            ".venv/",
            "node_modules/",
            ".env",
            "*.log",
            ".DS_Store",
            "qdrant_storage/",
            "package-lock.json",
            ".claude-indexer/",
        ]
    )

    # File Processing
    include_markdown: bool = Field(default=True)
    include_tests: bool = Field(default=False)
    max_file_size: int = Field(default=1048576, ge=1024)  # 1MB default, min 1KB

    # Performance Settings
    batch_size: int = Field(default=50, ge=1, le=1000)
    max_concurrent_files: int = Field(default=10, ge=1, le=100)
    cleanup_interval_minutes: int = Field(
        default=1, ge=0, le=10080
    )  # 0=disabled, max=1 week

    # State Management
    state_directory: Path | None = Field(default=None)

    @classmethod
    def from_env(cls) -> "IndexerConfig":
        """Create config with environment variable overrides."""
        return cls(
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            voyage_api_key=os.environ.get("VOYAGE_API_KEY", ""),
            qdrant_api_key=os.environ.get("QDRANT_API_KEY", "default-key"),
            qdrant_url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
            embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "openai"),
            voyage_model=os.environ.get("VOYAGE_MODEL", "voyage-3-lite"),
        )


# Project configuration models (existing ones)
class FilePatterns(BaseModel):
    """File patterns for include/exclude."""

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=lambda: ["debug/", "debug/*"])

    @validator("include", "exclude")
    def validate_patterns(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("Patterns must be a list")
        for pattern in v:
            if not isinstance(pattern, str):
                raise ValueError("All patterns must be strings")
        return v


class JavaScriptParserConfig(BaseModel):
    """JavaScript parser specific configuration."""

    use_ts_server: bool = Field(default=False)
    jsx: bool = Field(default=True)
    include_node_modules: bool = Field(default=False)


class PythonParserConfig(BaseModel):
    """Python parser specific configuration."""

    include_docstrings: bool = Field(default=True)
    include_type_hints: bool = Field(default=True)
    jedi_environment: str | None = Field(default=None)


class IndexingConfig(BaseModel):
    """Indexing behavior configuration."""

    file_patterns: FilePatterns | None = Field(default=None)
    max_file_size: int | None = Field(default=None)
    include_hidden: bool = Field(default=False)

    parser_config: dict[
        str, JavaScriptParserConfig | PythonParserConfig | dict[str, Any]
    ] = Field(default_factory=dict)


class WatcherConfig(BaseModel):
    """File watcher configuration."""

    enabled: bool = Field(default=True)
    debounce_seconds: float | None = Field(default=None)
    ignore_patterns: list[str] = Field(
        default_factory=lambda: [".git/", "__pycache__/", "node_modules/"]
    )


class ProjectInfo(BaseModel):
    """Project metadata."""

    name: str
    collection: str
    description: str | None = Field(default="")
    created_at: str | None = Field(default=None)

    @validator("name", "collection")
    def validate_names(cls, v: Any) -> str:
        if not v or not v.strip():
            raise ValueError("Name and collection cannot be empty")
        return str(v).strip()


# ProjectConfig is imported from config_schema to avoid duplication
