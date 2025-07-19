# 🏗️ Project-Level Configuration Enhancement Plan v2.6

## Executive Summary

This plan extends Claude Code Memory v2.5 to support **project-level configuration** through `.claude-indexer/config.json` files, allowing each project to have custom file patterns, parser settings, and indexing behavior. This enhancement maintains backward compatibility while eliminating hardcoded patterns and providing flexible per-project customization.

## 🎯 Goals & Requirements

1. **Project-Level Configuration**: Each project can have its own `.claude-indexer/config.json`
2. **Parser-Specific Settings**: Configure individual parsers per project (chunk sizes, special handling)
3. **File Pattern Control**: Include/exclude patterns at project level
4. **Backward Compatibility**: Fallback to global `settings.txt` when project config doesn't exist
5. **Clean Architecture**: No code duplication, centralized configuration management
6. **Zero Legacy Code**: No fallbacks to hardcoded patterns - explicit errors instead

## 🏛️ Architecture Overview

### Configuration Hierarchy (Priority Order)
1. **Project Config** (`.claude-indexer/config.json`) - Highest priority
2. **Environment Variables** - Override specific values
3. **Global Config** (`settings.txt`) - Default values
4. **System Defaults** - Minimal fallback

### Key Components

```
claude_indexer/
├── config/
│   ├── __init__.py
│   ├── project_config.py    # NEW: Project configuration manager
│   ├── config_schema.py     # NEW: Pydantic schemas for validation
│   └── config_loader.py     # NEW: Unified config loading logic
├── config.py                 # MODIFY: Integrate with project config
└── service.py               # MODIFY: Use project-specific settings
```

## 📋 Detailed Implementation Plan

### Phase 1: Core Infrastructure (Day 1)

#### 1.1 Create Project Configuration Schema

**File: `claude_indexer/config/config_schema.py`**

```python
"""Configuration schemas with validation."""

from pydantic import BaseModel, Field, validator
from typing import Dict, List, Any, Optional
from pathlib import Path


class ParserConfig(BaseModel):
    """Parser-specific configuration."""
    enabled: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)

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
    special_files: List[str] = Field(default_factory=lambda: [
        "package.json", "tsconfig.json", "composer.json"
    ])
    max_depth: int = 10


class TextParserConfig(ParserConfig):
    """Text parser configuration."""
    chunk_size: int = 50
    max_line_length: int = 1000
    encoding: str = "utf-8"


class YAMLParserConfig(ParserConfig):
    """YAML parser configuration."""
    detect_type: bool = True  # Auto-detect GitHub Actions, K8s, etc.
    max_depth: int = 10


class FilePatterns(BaseModel):
    """File inclusion/exclusion patterns."""
    include: List[str] = Field(default_factory=lambda: [
        "*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.json",
        "*.yaml", "*.yml", "*.html", "*.css", "*.md", "*.txt"
    ])
    exclude: List[str] = Field(default_factory=lambda: [
        "*.pyc", "__pycache__", ".git", ".venv", "node_modules",
        "dist", "build", "*.min.js", ".env", "*.log"
    ])

    @validator('include', 'exclude')
    def validate_patterns(cls, patterns):
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
    parser_config: Dict[str, ParserConfig] = Field(default_factory=dict)

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
```

#### 1.2 Create Project Config Manager

**File: `claude_indexer/config/project_config.py`**

```python
"""Project-level configuration management."""

import json
from pathlib import Path
from typing import Optional, Dict, Any
from .config_schema import ProjectConfig, IndexingConfig
from ..indexer_logging import get_logger

logger = get_logger()


class ProjectConfigManager:
    """Manages project-specific configuration."""

    CONFIG_DIR = ".claude-indexer"
    CONFIG_FILE = "config.json"

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path).resolve()
        self.config_path = self.project_path / self.CONFIG_DIR / self.CONFIG_FILE
        self._config: Optional[ProjectConfig] = None
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
            with open(self.config_path, 'r') as f:
                data = json.load(f)

            self._config = ProjectConfig(**data)
            self._loaded = True
            logger.info(f"Loaded project config from {self.config_path}")
            return self._config

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in project config: {e}")
            raise ValueError(f"Invalid project config: {e}")
        except Exception as e:
            logger.error(f"Failed to load project config: {e}")
            raise

    def save(self, config: ProjectConfig) -> None:
        """Save project configuration."""
        try:
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            # Write config
            with open(self.config_path, 'w') as f:
                json.dump(config.dict(), f, indent=2)

            self._config = config
            self._loaded = True
            logger.info(f"Saved project config to {self.config_path}")

        except Exception as e:
            logger.error(f"Failed to save project config: {e}")
            raise

    def create_default(self, project_name: str, collection_name: str) -> ProjectConfig:
        """Create default project configuration."""
        from .config_schema import ProjectInfo, FilePatterns

        # Detect project type based on files
        project_files = list(self.project_path.rglob("*"))
        has_js = any(f.suffix in ['.js', '.ts', '.jsx', '.tsx'] for f in project_files)
        has_py = any(f.suffix == '.py' for f in project_files)

        # Build appropriate file patterns
        include_patterns = []
        if has_py:
            include_patterns.extend(['*.py', '*.pyi'])
        if has_js:
            include_patterns.extend(['*.js', '*.ts', '*.jsx', '*.tsx', '*.mjs', '*.cjs'])

        # Always include common formats
        include_patterns.extend(['*.json', '*.yaml', '*.yml', '*.html', '*.css', '*.md', '*.txt'])

        config = ProjectConfig(
            project=ProjectInfo(
                name=project_name,
                collection=collection_name,
                description=f"Configuration for {project_name}"
            ),
            indexing=IndexingConfig(
                file_patterns=FilePatterns(include=include_patterns)
            )
        )

        # Add parser-specific configs if relevant
        if has_js:
            from .config_schema import JavaScriptParserConfig
            config.indexing.parser_config["javascript"] = JavaScriptParserConfig()

        return config

    def get_include_patterns(self) -> List[str]:
        """Get file inclusion patterns."""
        config = self.load()
        return config.indexing.file_patterns.include

    def get_exclude_patterns(self) -> List[str]:
        """Get file exclusion patterns."""
        config = self.load()
        return config.indexing.file_patterns.exclude

    def get_parser_config(self, parser_name: str) -> Dict[str, Any]:
        """Get parser-specific configuration."""
        config = self.load()
        parser_config = config.indexing.get_parser_config(parser_name)
        return parser_config.dict()
```

#### 1.3 Create Unified Config Loader

**File: `claude_indexer/config/config_loader.py`**

```python
"""Unified configuration loading with project support."""

import os
from pathlib import Path
from typing import Optional, Dict, Any
from ..config import IndexerConfig, load_config as load_global_config
from .project_config import ProjectConfigManager
from ..indexer_logging import get_logger

logger = get_logger()


class ConfigLoader:
    """Unified configuration loader with project-level support."""

    def __init__(self, project_path: Optional[Path] = None):
        self.project_path = Path(project_path) if project_path else Path.cwd()
        self.project_manager = ProjectConfigManager(self.project_path)
        self._merged_config: Optional[IndexerConfig] = None

    def load(self, **overrides) -> IndexerConfig:
        """Load configuration with project-level overrides."""
        # Start with global config
        global_config = load_global_config(**overrides)

        # Try to load project config
        try:
            if self.project_manager.exists:
                project_config = self.project_manager.load()
                return self._merge_configs(global_config, project_config, **overrides)
            else:
                logger.debug("No project config found, using global config")
                return global_config

        except Exception as e:
            logger.warning(f"Failed to load project config: {e}, using global config")
            return global_config

    def _merge_configs(self, global_config: IndexerConfig,
                      project_config: 'ProjectConfig', **overrides) -> IndexerConfig:
        """Merge project config into global config."""
        # Create dict from global config
        merged = global_config.dict()

        # Apply project-level overrides
        if project_config.indexing.file_patterns:
            merged['include_patterns'] = project_config.indexing.file_patterns.include
            merged['exclude_patterns'] = project_config.indexing.file_patterns.exclude

        if project_config.indexing.max_file_size:
            merged['max_file_size'] = project_config.indexing.max_file_size

        if project_config.watcher.debounce_seconds:
            merged['debounce_seconds'] = project_config.watcher.debounce_seconds

        # Apply explicit overrides (highest priority)
        merged.update(overrides)

        # Create new config with merged values
        return IndexerConfig(**merged)

    def get_parser_config(self, parser_name: str) -> Dict[str, Any]:
        """Get parser-specific configuration."""
        if self.project_manager.exists:
            try:
                return self.project_manager.get_parser_config(parser_name)
            except:
                pass
        return {}
```

### Phase 2: Integration Updates (Day 1-2)

#### 2.1 Update Core Indexer

**Modifications to `claude_indexer/indexer.py`:**

```python
# Add imports
from .config.config_loader import ConfigLoader
from .config.project_config import ProjectConfigManager

class CoreIndexer:
    def __init__(self, project_path: Path, collection_name: str, config: Dict[str, Any] = None):
        """Initialize with project-aware configuration."""
        self.project_path = Path(project_path).resolve()
        self.collection_name = collection_name

        # Load configuration with project support
        self.config_loader = ConfigLoader(self.project_path)
        self.config = self.config_loader.load(**(config or {}))

        # Initialize parser registry with project config
        self.parser_registry = ParserRegistry(self.project_path)
        self._inject_parser_configs()

        # ... rest of initialization

    def _inject_parser_configs(self):
        """Inject project-specific parser configurations."""
        for parser in self.parser_registry._parsers:
            parser_name = parser.__class__.__name__.lower().replace('parser', '')
            parser_config = self.config_loader.get_parser_config(parser_name)
            if parser_config and hasattr(parser, 'update_config'):
                parser.update_config(parser_config)

    def _find_all_files(self) -> List[Path]:
        """Find all files matching project patterns."""
        all_files = []

        # Use project-specific patterns
        include_patterns = self.config.include_patterns
        exclude_patterns = self.config.exclude_patterns

        # No fallback patterns - use what's configured
        if not include_patterns:
            raise ValueError("No include patterns configured")

        # ... rest of implementation
```

#### 2.2 Update Parser Base Classes

**Modifications to parsers to accept configuration:**

```python
# In base_parsers.py
class TreeSitterParser(CodeParser):
    def __init__(self, language_module, config: Dict[str, Any] = None):
        self.config = config or {}
        self.parser = Parser()
        # ... rest of init

    def update_config(self, config: Dict[str, Any]) -> None:
        """Update parser configuration."""
        self.config.update(config)

# In javascript_parser.py
class JavaScriptParser(TreeSitterParser):
    def __init__(self, config: Dict[str, Any] = None):
        import tree_sitter_javascript as tsjs
        super().__init__(tsjs, config)

        # Use config values
        self.use_ts_server = self.config.get('use_ts_server', False)
        self.jsx_enabled = self.config.get('jsx', True)
        self.typescript_enabled = self.config.get('typescript', True)
```

#### 2.3 Update Service Integration

**Modifications to `claude_indexer/service.py`:**

```python
def _start_project_watcher(self, project: Dict[str, Any], global_settings: Dict[str, Any]):
    """Start watching a project with project-specific config."""
    project_path = project["path"]
    collection_name = project["collection"]

    # Load project config if available
    config_loader = ConfigLoader(project_path)
    config = config_loader.load()

    # Create handler with merged config
    handler = IndexingEventHandler(
        project_path=project_path,
        collection_name=collection_name,
        debounce_seconds=config.debounce_seconds,
        settings={
            "watch_patterns": config.include_patterns,
            "ignore_patterns": config.exclude_patterns,
            "max_file_size": config.max_file_size
        }
    )

    # ... rest of implementation
```

### Phase 3: Markdown Parser Upgrade (Day 2)

#### 3.1 Tree-sitter Markdown Parser

**File: `claude_indexer/analysis/markdown_parser_v2.py`**

```python
"""Markdown parser using tree-sitter for consistent AST parsing."""

from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import time
from tree_sitter import Node
from .base_parsers import TreeSitterParser
from .parser import ParserResult
from .entities import Entity, Relation, EntityChunk, EntityType, EntityFactory, RelationFactory


class MarkdownParserV2(TreeSitterParser):
    """Parse Markdown with tree-sitter for consistent AST-based extraction."""

    SUPPORTED_EXTENSIONS = ['.md', '.markdown']

    def __init__(self, config: Dict[str, Any] = None):
        import tree_sitter_markdown as tsmd
        super().__init__(tsmd, config)

        # Configuration
        self.extract_links = self.config.get('extract_links', True)
        self.extract_code_blocks = self.config.get('extract_code_blocks', True)
        self.max_header_depth = self.config.get('max_header_depth', 6)

    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def get_supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: Path) -> ParserResult:
        """Extract documentation structure with tree-sitter."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            result.file_hash = self._get_file_hash(file_path)
            tree = self.parse_tree(content)

            # Check for syntax errors
            if self._has_syntax_errors(tree):
                result.errors.append(f"Markdown syntax errors in {file_path.name}")

            entities = []
            relations = []
            chunks = []

            # Create file entity
            file_entity = self._create_file_entity(file_path, content_type="documentation")
            entities.append(file_entity)

            # Extract headers
            headers = self._extract_headers(tree.root_node, content, file_path)
            entities.extend(headers)

            # Extract links if configured
            if self.extract_links:
                links, link_relations = self._extract_links(tree.root_node, content, file_path)
                entities.extend(links)
                relations.extend(link_relations)

            # Extract code blocks if configured
            if self.extract_code_blocks:
                code_blocks = self._extract_code_blocks(tree.root_node, content, file_path)
                entities.extend(code_blocks)

            # Create containment relations
            file_name = str(file_path)
            for entity in entities[1:]:  # Skip file entity
                relation = RelationFactory.create_contains_relation(file_name, entity.name)
                relations.append(relation)

            # Create chunks for progressive disclosure
            chunks = self._create_markdown_chunks(file_path, entities, content)

            result.entities = entities
            result.relations = relations
            result.implementation_chunks = chunks

        except Exception as e:
            result.errors.append(f"Markdown parsing failed: {e}")

        result.parsing_time = time.time() - start_time
        return result

    def _extract_headers(self, root: Node, content: str, file_path: Path) -> List[Entity]:
        """Extract headers using tree-sitter AST."""
        headers = []

        for node in self._find_nodes_by_type(root, ['atx_heading', 'setext_heading']):
            # Extract header level
            if node.type == 'atx_heading':
                # Count # symbols
                marker_node = node.child_by_field_name('marker')
                level = len(self.extract_node_text(marker_node, content)) if marker_node else 1
            else:  # setext_heading
                # Level 1 for === and level 2 for ---
                underline = node.child_by_field_name('underline')
                level = 1 if '=' in self.extract_node_text(underline, content) else 2

            # Skip headers beyond max depth
            if level > self.max_header_depth:
                continue

            # Extract header text
            content_nodes = [child for child in node.children
                           if child.type not in ['atx_h1_marker', 'atx_h2_marker', 'atx_h3_marker',
                                                'atx_h4_marker', 'atx_h5_marker', 'atx_h6_marker']]

            header_text = ' '.join(self.extract_node_text(n, content).strip()
                                 for n in content_nodes).strip()

            if header_text:
                entity = Entity(
                    name=header_text,
                    entity_type=EntityType.DOCUMENTATION,
                    observations=[
                        f"Header level {level}: {header_text}",
                        f"Line {node.start_point[0] + 1} in {file_path.name}"
                    ],
                    file_path=file_path,
                    line_number=node.start_point[0] + 1,
                    end_line_number=node.end_point[0] + 1,
                    metadata={
                        "header_level": level,
                        "type": "header",
                        "source": "tree-sitter"
                    }
                )
                headers.append(entity)

        return headers

    def _extract_links(self, root: Node, content: str, file_path: Path) -> Tuple[List[Entity], List[Relation]]:
        """Extract links and create relations."""
        links = []
        relations = []

        for node in self._find_nodes_by_type(root, ['link']):
            # Extract link text and destination
            text_node = node.child_by_field_name('text')
            dest_node = node.child_by_field_name('destination')

            if text_node and dest_node:
                link_text = self.extract_node_text(text_node, content)
                link_dest = self.extract_node_text(dest_node, content)

                entity = Entity(
                    name=f"Link: {link_text}",
                    entity_type=EntityType.DOCUMENTATION,
                    observations=[
                        f"Link text: {link_text}",
                        f"URL: {link_dest}"
                    ],
                    file_path=file_path,
                    line_number=node.start_point[0] + 1,
                    metadata={
                        "type": "link",
                        "url": link_dest,
                        "source": "tree-sitter"
                    }
                )
                links.append(entity)

                # Create relation if internal link
                if not link_dest.startswith(('http://', 'https://', '#')):
                    relation = RelationFactory.create_imports_relation(
                        importer=str(file_path),
                        imported=link_dest,
                        import_type="markdown_link"
                    )
                    relations.append(relation)

        return links, relations

    def _extract_code_blocks(self, root: Node, content: str, file_path: Path) -> List[Entity]:
        """Extract code blocks with language info."""
        code_blocks = []

        for node in self._find_nodes_by_type(root, ['fenced_code_block']):
            # Extract language
            info_node = node.child_by_field_name('info_string')
            language = self.extract_node_text(info_node, content).strip() if info_node else "unknown"

            # Extract code content
            code_node = node.child_by_field_name('code')
            code_content = self.extract_node_text(code_node, content) if code_node else ""

            # Create entity
            entity = Entity(
                name=f"Code Block ({language})",
                entity_type=EntityType.DOCUMENTATION,
                observations=[
                    f"Language: {language}",
                    f"Code: {code_content[:100]}...",
                    f"Line {node.start_point[0] + 1} in {file_path.name}",
                    f"Full code length: {len(code_content)} characters"
                ],
                file_path=file_path,
                line_number=node.start_point[0] + 1,
                end_line_number=node.end_point[0] + 1,
                metadata={
                    "type": "code_block",
                    "language": language,
                    "source": "tree-sitter"
                }
            )
            code_blocks.append(entity)

        return code_blocks

    def _create_markdown_chunks(self, file_path: Path, entities: List[Entity],
                               content: str) -> List[EntityChunk]:
        """Create searchable chunks for markdown content."""
        chunks = []

        # Create a single metadata chunk for the whole document
        chunks.append(EntityChunk(
            id=self._create_chunk_id(file_path, "content", "metadata"),
            entity_name=file_path.name,
            chunk_type="metadata",
            content=content[:2000],  # First 2000 chars for search
            metadata={
                "entity_type": "markdown_file",
                "file_path": str(file_path),
                "entity_count": len(entities),
                "has_implementation": False
            }
        ))

        return chunks
```

### Phase 4: CLI Extensions (Day 2)

#### 4.1 Add Project Initialization Command

**Modifications to `claude_indexer/cli.py`:**

```python
@cli.command()
@click.option('-p', '--project', 'project_path', required=True,
              type=click.Path(exists=True), help='Project directory path')
@click.option('-n', '--name', required=True, help='Project name')
@click.option('-c', '--collection', required=True, help='Collection name')
@click.option('--force', is_flag=True, help='Overwrite existing config')
def init(project_path: str, name: str, collection: str, force: bool):
    """Initialize project configuration."""
    from .config.project_config import ProjectConfigManager

    manager = ProjectConfigManager(Path(project_path))

    # Check if already exists
    if manager.exists and not force:
        click.echo(f"❌ Project config already exists at {manager.config_path}")
        click.echo("Use --force to overwrite")
        return

    # Create default config
    config = manager.create_default(name, collection)

    # Save it
    manager.save(config)

    click.echo(f"✅ Created project config at {manager.config_path}")
    click.echo(f"📁 Project: {name}")
    click.echo(f"🗄️  Collection: {collection}")
    click.echo(f"📝 Include patterns: {', '.join(config.indexing.file_patterns.include)}")

@cli.command()
@click.option('-p', '--project', 'project_path',
              type=click.Path(exists=True), help='Project directory path')
def show_config(project_path: str):
    """Show effective configuration for project."""
    from .config.config_loader import ConfigLoader

    path = Path(project_path) if project_path else Path.cwd()
    loader = ConfigLoader(path)
    config = loader.load()

    click.echo("📋 Effective Configuration:")
    click.echo(f"📁 Project Path: {path}")
    click.echo(f"🗄️  Collection: {config.collection_name}")
    click.echo(f"📝 Include Patterns: {', '.join(config.include_patterns)}")
    click.echo(f"🚫 Exclude Patterns: {', '.join(config.exclude_patterns)}")
    click.echo(f"📏 Max File Size: {config.max_file_size:,} bytes")
    click.echo(f"⏱️  Debounce: {config.debounce_seconds}s")
```

### Phase 5: Testing Strategy (Day 3)

#### 5.1 Unit Tests

**File: `tests/unit/test_project_config.py`**

```python
"""Test project configuration functionality."""

import pytest
import json
from pathlib import Path
from claude_indexer.config.project_config import ProjectConfigManager
from claude_indexer.config.config_schema import ProjectConfig, ProjectInfo


class TestProjectConfig:
    """Test project configuration management."""

    def test_create_default_config(self, tmp_path):
        """Test creating default project configuration."""
        manager = ProjectConfigManager(tmp_path)
        config = manager.create_default("test-project", "test-collection")

        assert config.project.name == "test-project"
        assert config.project.collection == "test-collection"
        assert len(config.indexing.file_patterns.include) > 0
        assert "*.py" in config.indexing.file_patterns.include

    def test_save_and_load_config(self, tmp_path):
        """Test saving and loading configuration."""
        manager = ProjectConfigManager(tmp_path)

        # Create and save config
        config = manager.create_default("test", "test-col")
        manager.save(config)

        # Load it back
        loaded = manager.load()
        assert loaded.project.name == "test"
        assert loaded.project.collection == "test-col"

    def test_parser_specific_config(self, tmp_path):
        """Test parser-specific configuration."""
        from claude_indexer.config.config_schema import JavaScriptParserConfig

        manager = ProjectConfigManager(tmp_path)
        config = manager.create_default("js-project", "js-collection")

        # Add JS parser config
        config.indexing.parser_config["javascript"] = JavaScriptParserConfig(
            use_ts_server=True,
            jsx=False
        )

        manager.save(config)

        # Get parser config
        js_config = manager.get_parser_config("javascript")
        assert js_config["use_ts_server"] is True
        assert js_config["jsx"] is False

    def test_invalid_config_handling(self, tmp_path):
        """Test handling of invalid configuration."""
        config_path = tmp_path / ".claude-indexer" / "config.json"
        config_path.parent.mkdir(parents=True)

        # Write invalid JSON
        config_path.write_text("{invalid json}")

        manager = ProjectConfigManager(tmp_path)
        with pytest.raises(ValueError, match="Invalid project config"):
            manager.load()

    def test_config_validation(self):
        """Test configuration validation."""
        from claude_indexer.config.config_schema import FilePatterns

        # Valid patterns
        patterns = FilePatterns(
            include=["*.py", "*.js"],
            exclude=["node_modules", "__pycache__"]
        )
        assert len(patterns.include) == 2

        # Invalid patterns
        with pytest.raises(ValueError):
            FilePatterns(include=[123, "*.py"])  # Non-string pattern
```

#### 5.2 Integration Tests

**File: `tests/integration/test_project_config_integration.py`**

```python
"""Integration tests for project configuration."""

import pytest
from pathlib import Path
from claude_indexer.indexer import CoreIndexer
from claude_indexer.config.project_config import ProjectConfigManager


class TestProjectConfigIntegration:
    """Test project config integration with indexer."""

    def test_indexer_uses_project_config(self, tmp_path):
        """Test that indexer respects project configuration."""
        # Create test files
        (tmp_path / "test.py").write_text("def test(): pass")
        (tmp_path / "test.js").write_text("function test() {}")
        (tmp_path / "test.txt").write_text("test content")

        # Create project config excluding .txt files
        manager = ProjectConfigManager(tmp_path)
        config = manager.create_default("test", "test-collection")
        config.indexing.file_patterns.include = ["*.py", "*.js"]
        config.indexing.file_patterns.exclude = ["*.txt"]
        manager.save(config)

        # Create indexer
        indexer = CoreIndexer(tmp_path, "test-collection")

        # Find files
        files = indexer._find_all_files()
        file_names = {f.name for f in files}

        assert "test.py" in file_names
        assert "test.js" in file_names
        assert "test.txt" not in file_names

    def test_parser_config_injection(self, tmp_path):
        """Test parser configuration injection."""
        from claude_indexer.config.config_schema import TextParserConfig

        # Create project config with custom text parser settings
        manager = ProjectConfigManager(tmp_path)
        config = manager.create_default("test", "test-collection")
        config.indexing.parser_config["text"] = TextParserConfig(
            chunk_size=100,
            max_line_length=500
        )
        manager.save(config)

        # Create indexer
        indexer = CoreIndexer(tmp_path, "test-collection")

        # Get text parser
        text_parser = indexer.parser_registry.get_parser_for_file(Path("test.txt"))

        # Check config was injected
        if hasattr(text_parser, 'config'):
            assert text_parser.config.get('chunk_size') == 100
            assert text_parser.config.get('max_line_length') == 500

    def test_config_hierarchy(self, tmp_path):
        """Test configuration hierarchy (project > global > defaults)."""
        # Create project config
        manager = ProjectConfigManager(tmp_path)
        config = manager.create_default("test", "test-collection")
        config.indexing.max_file_size = 500000  # 500KB
        manager.save(config)

        # Load with overrides
        from claude_indexer.config.config_loader import ConfigLoader
        loader = ConfigLoader(tmp_path)

        # Project config should override global
        merged_config = loader.load()
        assert merged_config.max_file_size == 500000

        # Explicit override should win
        merged_config = loader.load(max_file_size=250000)
        assert merged_config.max_file_size == 250000
```

#### 5.3 End-to-End Tests

**File: `tests/e2e/test_project_config_e2e.py`**

```python
"""End-to-end tests for project configuration."""

import pytest
import subprocess
from pathlib import Path


class TestProjectConfigE2E:
    """Test complete project configuration workflow."""

    def test_cli_init_and_index(self, tmp_path):
        """Test CLI project init and indexing."""
        # Create test project
        (tmp_path / "app.js").write_text("console.log('test');")
        (tmp_path / "test.py").write_text("print('test')")

        # Initialize project
        result = subprocess.run([
            'claude-indexer', 'init',
            '-p', str(tmp_path),
            '-n', 'test-project',
            '-c', 'test-collection'
        ], capture_output=True, text=True)

        assert result.returncode == 0
        assert "Created project config" in result.stdout

        # Verify config was created
        config_path = tmp_path / ".claude-indexer" / "config.json"
        assert config_path.exists()

        # Run indexing
        result = subprocess.run([
            'claude-indexer',
            '-p', str(tmp_path),
            '-c', 'test-collection'
        ], capture_output=True, text=True)

        assert result.returncode == 0
        assert "app.js" in result.stdout
        assert "test.py" in result.stdout

    def test_markdown_parser_upgrade(self, tmp_path):
        """Test upgraded markdown parser with tree-sitter."""
        # Create markdown file
        md_content = """# Test Document

## Section 1
This is a test section.

### Subsection 1.1
With some content.

[Link to docs](https://example.com)

```python
def test():
    pass
```
"""
        (tmp_path / "README.md").write_text(md_content)

        # Create project config
        subprocess.run([
            'claude-indexer', 'init',
            '-p', str(tmp_path),
            '-n', 'test',
            '-c', 'test'
        ])

        # Index with new parser
        from claude_indexer.indexer import CoreIndexer
        indexer = CoreIndexer(tmp_path, "test")
        result = indexer.index_project()

        # Verify entities extracted
        assert result.files_processed == 1
        assert result.entities_created >= 4  # File + headers + code block
```

### Phase 6: Migration & Documentation (Day 3)

#### 6.1 Migration Script

**File: `scripts/migrate_to_project_config.py`**

```python
#!/usr/bin/env python3
"""Migrate existing projects to project-level configuration."""

import json
import sys
from pathlib import Path
from claude_indexer.config.project_config import ProjectConfigManager
from claude_indexer.service import IndexingService


def migrate_service_config():
    """Migrate service config to include project configs."""
    service = IndexingService()
    config = service.load_config()

    migrated = 0
    for project in config.get("projects", []):
        project_path = Path(project["path"])
        if not project_path.exists():
            print(f"⚠️  Skipping {project_path} (not found)")
            continue

        # Create project config
        manager = ProjectConfigManager(project_path)
        if not manager.exists:
            project_config = manager.create_default(
                project.get("name", project_path.name),
                project["collection"]
            )

            # Apply any custom settings
            if "settings" in project:
                if "watch_patterns" in project["settings"]:
                    project_config.indexing.file_patterns.include = project["settings"]["watch_patterns"]
                if "ignore_patterns" in project["settings"]:
                    project_config.indexing.file_patterns.exclude = project["settings"]["ignore_patterns"]

            manager.save(project_config)
            print(f"✅ Migrated {project_path}")
            migrated += 1
        else:
            print(f"ℹ️  {project_path} already has project config")

    print(f"\n✨ Migrated {migrated} projects")


if __name__ == "__main__":
    migrate_service_config()
```

#### 6.2 Documentation Updates

**Updates to README.md:**

```markdown
## 🆕 v2.6 - Project-Level Configuration

Configure each project individually with `.claude-indexer/config.json`:

### Quick Start

```bash
# Initialize project configuration
claude-indexer init -p /path/to/project -n "My Project" -c my-project-collection

# Project config is automatically used when indexing
claude-indexer -p /path/to/project -c my-project-collection
```

### Project Configuration

Each project can have its own `.claude-indexer/config.json`:

```json
{
  "version": "2.6",
  "project": {
    "name": "my-project",
    "collection": "my-project-memory",
    "description": "My awesome project"
  },
  "indexing": {
    "file_patterns": {
      "include": ["*.py", "*.js", "*.ts", "*.json"],
      "exclude": ["node_modules", "*.pyc", "dist"]
    },
    "parser_config": {
      "javascript": {
        "use_ts_server": false,
        "jsx": true
      },
      "text": {
        "chunk_size": 100
      }
    }
  }
}
```

### Configuration Hierarchy

1. **Project Config** (`.claude-indexer/config.json`) - Highest priority
2. **Environment Variables** - Override specific values
3. **Global Config** (`settings.txt`) - Default values
4. **System Defaults** - Minimal fallback

### Parser Configuration

Configure individual parsers per project:

```json
{
  "indexing": {
    "parser_config": {
      "javascript": {
        "use_ts_server": true,
        "jsx": false,
        "typescript": true
      },
      "json": {
        "special_files": ["package.json", "composer.json"],
        "extract_schema": true
      },
      "text": {
        "chunk_size": 50,
        "encoding": "utf-8"
      },
      "yaml": {
        "detect_type": true
      }
    }
  }
}
```
```

## 🔧 Implementation Checklist

### Day 1: Core Infrastructure
- [ ] Create `claude_indexer/config/` directory structure
- [ ] Implement `config_schema.py` with Pydantic models
- [ ] Implement `project_config.py` manager
- [ ] Implement `config_loader.py` unified loader
- [ ] Update `CoreIndexer` to use project config
- [ ] Update parser base classes for config injection

### Day 2: Integration & Parser
- [ ] Update service.py for project config support
- [ ] Implement tree-sitter markdown parser
- [ ] Add CLI commands (init, show-config)
- [ ] Update watcher to use project patterns
- [ ] Remove all hardcoded patterns

### Day 3: Testing & Documentation
- [ ] Write unit tests for project config
- [ ] Write integration tests
- [ ] Write e2e tests
- [ ] Create migration script
- [ ] Update README.md
- [ ] Update CLAUDE.md

## 🎯 Success Metrics

1. **Zero Hardcoded Patterns**: All file patterns from configuration
2. **Full Parser Customization**: Each parser configurable per project
3. **Backward Compatible**: Existing projects continue to work
4. **Clean Architecture**: No code duplication
5. **100% Test Coverage**: All new code fully tested

## 📝 Testing Scenarios

### Scenario 1: New Project Setup
```bash
# Create new project with custom patterns
claude-indexer init -p ./my-app -n "My App" -c my-app
# Edit .claude-indexer/config.json to customize
# Run indexing - should use project config
claude-indexer -p ./my-app -c my-app
```

### Scenario 2: Mixed Language Project
```json
{
  "indexing": {
    "file_patterns": {
      "include": ["*.py", "*.js", "*.ts", "*.vue", "*.json"],
      "exclude": ["node_modules", "__pycache__", "*.min.js"]
    },
    "parser_config": {
      "javascript": {"jsx": true, "typescript": true},
      "text": {"chunk_size": 100}
    }
  }
}
```

### Scenario 3: Documentation-Only Project
```json
{
  "indexing": {
    "file_patterns": {
      "include": ["*.md", "*.rst", "*.txt"],
      "exclude": ["_build", ".tox"]
    },
    "parser_config": {
      "markdown": {
        "extract_links": true,
        "extract_code_blocks": true,
        "max_header_depth": 4
      }
    }
  }
}
```

## 🚀 Next Steps & Future Enhancements

1. **Auto-detection**: Detect project type and suggest configurations
2. **Template Library**: Pre-built configs for common project types
3. **Config Inheritance**: Base configs that projects can extend
4. **Dynamic Reloading**: Hot-reload config changes without restart
5. **Config Validation CLI**: Command to validate project configs

## Summary

This plan provides a complete project-level configuration system for Claude Code Memory v2.6. It maintains backward compatibility while eliminating hardcoded patterns and providing flexible per-project customization. The implementation focuses on clean architecture with no code duplication, comprehensive testing, and a smooth migration path for existing users.

Key benefits:
- Each project controls its own file patterns and parser settings
- No more hardcoded patterns - everything is configurable
- Parser-specific settings allow fine-tuning per project
- Markdown parser upgraded to tree-sitter for consistency
- Clean configuration hierarchy with clear precedence
- Comprehensive testing ensures reliability
