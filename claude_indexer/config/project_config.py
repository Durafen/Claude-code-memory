"""Project-level configuration management."""

import json
from pathlib import Path
from typing import Any

from ..indexer_logging import get_logger
from .config_schema import FilePatterns, IndexingConfig, ProjectConfig, ProjectInfo

logger = get_logger()


class ProjectConfigManager:
    """Manages project-specific configuration."""

    CONFIG_DIR = ".claude-indexer"
    CONFIG_FILE = "config.json"

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path).resolve()
        self.config_path = self.project_path / self.CONFIG_DIR / self.CONFIG_FILE
        self._config: ProjectConfig | None = None
        self._loaded = False

    @property
    def exists(self) -> bool:
        """Check if project config exists."""
        return self.config_path.exists()

    def load(self) -> ProjectConfig:
        """Load project configuration."""
        if self._loaded and self._config:
            return self._config

        if not self.exists:
            logger.debug(f"No project config at {self.config_path}")
            raise FileNotFoundError(f"Project config not found: {self.config_path}")

        try:
            with open(self.config_path) as f:
                data = json.load(f)

            self._config = ProjectConfig(**data)
            self._loaded = True
            logger.info(f"Loaded project config from {self.config_path}")
            return self._config

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in project config: {e}")
            raise ValueError(f"Invalid project config: {e}") from None
        except Exception as e:
            logger.error(f"Failed to load project config: {e}")
            raise

    def save(self, config: ProjectConfig) -> None:
        """Save project configuration."""
        try:
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            # Write config
            with open(self.config_path, "w") as f:
                json.dump(config.dict(), f, indent=2)

            self._config = config
            self._loaded = True
            logger.info(f"Saved project config to {self.config_path}")

        except Exception as e:
            logger.error(f"Failed to save project config: {e}")
            raise

    def create_default(self, project_name: str, collection_name: str) -> ProjectConfig:
        """Create default project configuration."""

        # Detect project type based on files
        project_files = list(self.project_path.rglob("*"))
        has_js = any(f.suffix in [".js", ".ts", ".jsx", ".tsx"] for f in project_files)
        has_py = any(f.suffix == ".py" for f in project_files)
        has_html = any(f.suffix in [".html", ".htm"] for f in project_files)
        has_css = any(f.suffix == ".css" for f in project_files)

        # Build appropriate file patterns
        include_patterns = []
        if has_py:
            include_patterns.extend(["*.py", "*.pyi"])
        if has_js:
            include_patterns.extend(
                ["*.js", "*.ts", "*.jsx", "*.tsx", "*.mjs", "*.cjs"]
            )
        if has_html:
            include_patterns.extend(["*.html", "*.htm"])
        if has_css:
            include_patterns.extend(["*.css"])

        # Always include common formats
        include_patterns.extend(["*.json", "*.yaml", "*.yml", "*.md", "*.txt"])

        # Standard exclude patterns for all projects
        exclude_patterns = [
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

        config = ProjectConfig(
            project=ProjectInfo(
                name=project_name,
                collection=collection_name,
                description=f"Configuration for {project_name}",
            ),
            indexing=IndexingConfig(
                file_patterns=FilePatterns(
                    include=include_patterns, exclude=exclude_patterns
                )
            ),
        )

        # Add parser-specific configs if relevant
        if has_js:
            from .config_schema import JavaScriptParserConfig

            config.indexing.parser_config["javascript"] = JavaScriptParserConfig()

        if has_html or has_css:
            from .config_schema import MarkdownParserConfig

            config.indexing.parser_config["markdown"] = MarkdownParserConfig()

        return config

    def get_include_patterns(self) -> list[str]:
        """Get file inclusion patterns."""
        config = self.load()
        return config.indexing.file_patterns.include

    def get_exclude_patterns(self) -> list[str]:
        """Get file exclusion patterns."""
        config = self.load()
        return config.indexing.file_patterns.exclude

    def get_parser_config(self, parser_name: str) -> dict[str, Any]:
        """Get parser-specific configuration."""
        config = self.load()
        parser_config = config.indexing.get_parser_config(parser_name)
        return parser_config.dict()
