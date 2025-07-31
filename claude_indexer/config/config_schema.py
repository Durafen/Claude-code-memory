"""Configuration schemas with validation."""

from typing import Any

from pydantic import BaseModel, Field, validator


class ParserConfig(BaseModel):
    """Parser-specific configuration."""

    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"  # Allow additional parser-specific fields


class JavaScriptParserConfig(ParserConfig):
    """JavaScript/TypeScript parser configuration."""

    use_ts_server: bool = False
    jsx: bool = True
    typescript: bool = True
    ecma_version: str = "latest"


class JSONParserConfig(ParserConfig):
    """JSON parser configuration."""

    extract_schema: bool = True
    special_files: list[str] = Field(
        default_factory=lambda: ["package.json", "tsconfig.json", "composer.json"]
    )
    max_depth: int = 10
    content_only: bool = (
        False  # Extract individual content items (posts/articles) when True
    )
    max_content_items: int = 0  # Maximum items to extract per file (0 = no limit)


class TextParserConfig(ParserConfig):
    """Text parser configuration."""

    chunk_size: int = 50
    max_line_length: int = 1000
    encoding: str = "utf-8"


class YAMLParserConfig(ParserConfig):
    """YAML parser configuration."""

    detect_type: bool = True  # Auto-detect GitHub Actions, K8s, etc.
    max_depth: int = 10


class MarkdownParserConfig(ParserConfig):
    """Markdown parser configuration."""

    extract_links: bool = True
    extract_code_blocks: bool = True
    max_header_depth: int = 6


class FilePatterns(BaseModel):
    """File inclusion/exclusion patterns."""

    include: list[str] = Field(
        default_factory=lambda: [
            "*.py",
            "*.js",
            "*.ts",
            "*.jsx",
            "*.tsx",
            "*.json",
            "*.yaml",
            "*.yml",
            "*.html",
            "*.css",
            "*.md",
            "*.txt",
        ]
    )
    exclude: list[str] = Field(
        default_factory=lambda: [
            "*.pyc",
            "__pycache__/",
            ".git/",
            ".venv/",
            "node_modules/",
            "dist/",
            "build/",
            "*.min.js",
            ".env",
            "*.log",
            "logs/",
            ".mypy_cache/",
            ".pytest_cache/",
            ".tox/",
            ".coverage",
            "htmlcov/",
            "coverage/",
            ".cache/",
            "test-results/",
            "playwright-report/",
            ".idea/",
            ".vscode/",
            ".zed/",
            ".DS_Store",
            "Thumbs.db",
            "Desktop.ini",
            ".npm/",
            ".next/",
            ".parcel-cache/",
            "*.tsbuildinfo",
            "*.map",
            "*.db",
            "*.sqlite3",
            "chroma_db/",
            "*.tmp",
            "*.bak",
            "*.old",
            "debug/",
            "qdrant_storage/",
            "backups/",
            "*.egg-info",
            "settings.txt",
            ".claude-indexer/",
            ".claude/",
            "package-lock.json",
            "memory_guard_debug.txt",
            "memory_guard_debug_*.txt",
        ]
    )

    @validator("include", "exclude")
    def validate_patterns(cls, patterns: list[str]) -> list[str]:
        """Ensure patterns are valid."""
        for pattern in patterns:
            if not isinstance(pattern, str):
                raise ValueError(f"Pattern must be string: {pattern}")
        return patterns


class IndexingConfig(BaseModel):
    """Indexing behavior configuration."""

    enabled: bool = True
    incremental: bool = True
    file_patterns: FilePatterns = Field(default_factory=FilePatterns)
    max_file_size: int = Field(default=1048576, ge=1024)  # 1MB default
    parser_config: dict[str, ParserConfig] = Field(default_factory=dict)

    def get_parser_config(self, parser_name: str) -> ParserConfig:
        """Get parser-specific configuration."""
        return self.parser_config.get(parser_name, ParserConfig())


class WatcherConfig(BaseModel):
    """File watcher configuration."""

    enabled: bool = True
    debounce_seconds: float = Field(default=2.0, ge=0.1, le=60.0)
    use_gitignore: bool = True


class ProjectInfo(BaseModel):
    """Project metadata."""

    name: str
    collection: str
    description: str = ""
    version: str = "1.0.0"


class ProjectConfig(BaseModel):
    """Complete project configuration."""

    version: str = "2.6"
    project: ProjectInfo
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)

    class Config:
        extra = "forbid"  # Strict validation
