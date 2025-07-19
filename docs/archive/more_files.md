# Project-Level File Pattern Configuration Plan

## Current Version: v2.4.1 - Progressive Disclosure Architecture
**Status**: Plan ready for implementation alongside existing v2.4 features

## Overview
Enable per-project configuration of file types to watch and index, specifically adding support for JavaScript (.js) and text (.txt) files as requested. Each project can independently choose which file types to include, with all settings stored locally in `PROJECT_DIR/.claude-indexer/config.json` for complete project isolation and portability.

## Integration with v2.4 Architecture

### Current State (v2.4.1)
- **Progressive Disclosure**: Semantic scope control (minimal/logical/dependencies)
- **Dual Provider Support**: Voyage AI + OpenAI embeddings
- **Enhanced MCP Server**: Automatic provider detection
- **Service Management**: Multi-project automation via `~/.claude-indexer/config.json`

### Proposed Enhancement
- Move from global to project-local configuration
- Add JavaScript/TypeScript parser using tree-sitter
- Add configurable text file parser
- Remove ALL hardcoded file patterns

## Architecture: Project-Local Configuration

### New Architecture
- Project config: `PROJECT_DIR/.claude-indexer/config.json`
- State file: `PROJECT_DIR/.claude-indexer/state.json`
- Each project self-contained with its own settings
- No global configuration dependencies
- Integrates with existing VectorStore and EntityChunk architecture

### Benefits
1. **Version Control**: Configuration committed with code
2. **Team Collaboration**: Shared settings across team members
3. **Project Portability**: Move/copy projects with all settings intact
4. **Isolation**: No cross-project configuration conflicts
5. **Simplicity**: Settings next to code they configure
6. **Project Choice**: Each project independently decides which file types to index
7. **Parser Extensibility**: Easy to add new language parsers

## Current State Analysis

### Critical Change: Project-Local Settings
**ALL project settings will be stored INSIDE the project directory at:**
- `PROJECT_DIR/.claude-indexer/config.json` - Project-specific configuration
- `PROJECT_DIR/.claude-indexer/state.json` - Indexing state tracking
- `PROJECT_DIR/.claude-indexer/logs/` - Project-specific logs (optional)

**NOT in user home directory** - This ensures complete project portability and version control.

### Files to Update
1. **claude_indexer/config.py**
   - Remove hardcoded patterns
   - Remove global config references

2. **claude_indexer/watcher/handler.py**
   - Remove hardcoded watch patterns
   - Load from project config

3. **claude_indexer/service.py**
   - Remove global config path
   - Implement project discovery

4. **claude_indexer/indexer.py**
   - Change state file location
   - Remove centralized state management

5. **claude_indexer/analysis/parser.py**
   - Keep parser registry
   - Add new parsers

## Implementation Plan

### Phase 1: Project Configuration Structure

#### 1.1 Project Directory Structure
```
my-project/
├── src/                    # Project source code
├── tests/                  # Project tests
├── .claude-indexer/        # Claude indexer configuration
│   ├── config.json         # Project-specific settings
│   ├── state.json          # Indexing state
│   └── logs/               # Project-specific logs (optional)
└── README.md
```

#### 1.2 Project Configuration Schema
**Location**: `PROJECT_DIR/.claude-indexer/config.json`
```json
{
  "version": "2.0",
  "project": {
    "name": "my-project",
    "collection": "my-project-memory",
    "description": "Project description for context"
  },
  "indexing": {
    "enabled": true,
    "incremental": true,
    "file_patterns": {
      "include": ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.md", "*.txt"],
      "exclude": ["*.pyc", "__pycache__", "node_modules", ".git", "dist", "build", ".venv"]
    },
    "max_file_size": 1048576,
    "parser_config": {
      "javascript": {
        "ecma_version": "latest",
        "jsx": true,
        "typescript": true
      },
      "text": {
        "max_line_length": 1000,
        "encoding": "utf-8"
      }
    }
  },
  "watcher": {
    "enabled": true,
    "debounce_seconds": 2.0
  },
  "git_hooks": {
    "pre_commit": {
      "enabled": false,
      "fail_on_error": false
    }
  }
}
```

#### 1.3 Example Project Configurations

**Python Project** (.claude-indexer/config.json):
```json
{
  "project": {
    "name": "my-python-app",
    "collection": "python-app-memory"
  },
  "indexing": {
    "file_patterns": {
      "include": ["*.py", "*.pyi", "*.md"],
      "exclude": ["*.pyc", "__pycache__", ".venv"]
    }
  }
}
```

**JavaScript Project** (.claude-indexer/config.json):
```json
{
  "project": {
    "name": "my-js-app",
    "collection": "js-app-memory"
  },
  "indexing": {
    "file_patterns": {
      "include": ["*.js", "*.jsx", "*.ts", "*.tsx", "*.json", "*.md"],
      "exclude": ["node_modules", "dist", "build", "*.min.js"]
    }
  }
}
```

**Mixed Project with Logs** (.claude-indexer/config.json):
```json
{
  "project": {
    "name": "full-stack-app",
    "collection": "fullstack-memory"
  },
  "indexing": {
    "file_patterns": {
      "include": ["*.py", "*.js", "*.txt", "*.log", "*.md", "*.yaml"],
      "exclude": ["*.pyc", "node_modules", "logs/archive", ".git"]
    }
  }
}
```

#### 1.4 Project Registry for Service (Minimal)
**Location**: `~/.claude-indexer/projects.json`
**Purpose**: Only stores project paths for service discovery. All configuration lives in each project.
```json
{
  "version": "2.5",
  "projects": [
    {
      "path": "/absolute/path/to/project1",
      "active": true
    },
    {
      "path": "/absolute/path/to/project2",
      "active": false
    }
  ]
}
```
**Note**: All project settings are read from each project's local `.claude-indexer/config.json`

### Phase 2: Core Components

#### 2.1 Project Configuration Manager
**File**: `claude_indexer/project_config.py`
```python
from pathlib import Path
import json
from typing import Dict, Any, Optional

class ProjectConfig:
    """Manages project-local configuration."""

    CONFIG_DIR = '.claude-indexer'
    CONFIG_FILE = 'config.json'
    STATE_FILE = 'state.json'

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path).resolve()
        self.config_dir = self.project_path / self.CONFIG_DIR
        self.config_file = self.config_dir / self.CONFIG_FILE
        self.state_file = self.config_dir / self.STATE_FILE

    def exists(self) -> bool:
        """Check if project config exists."""
        return self.config_file.exists()

    def load(self) -> Dict[str, Any]:
        """Load project configuration."""
        if not self.config_file.exists():
            raise FileNotFoundError(f"No config found at {self.config_file}")

        with open(self.config_file) as f:
            return json.load(f)

    def save(self, config: Dict[str, Any]) -> None:
        """Save project configuration."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)

    def initialize(self, name: Optional[str] = None,
                  collection: Optional[str] = None) -> None:
        """Initialize new project with config."""
        if self.config_file.exists():
            raise ValueError("Project already initialized")

        project_name = name or self.project_path.name
        collection_name = collection or f"{project_name}-memory"

        default_config = {
            "version": "2.0",
            "project": {
                "name": project_name,
                "collection": collection_name,
                "description": ""
            },
            "indexing": {
                "enabled": True,
                "incremental": True,
                "file_patterns": {
                    "include": ["*.py", "*.md"],
                    "exclude": ["*.pyc", "__pycache__", ".git", ".venv", "node_modules"]
                },
                "max_file_size": 1048576
            },
            "watcher": {
                "enabled": True,
                "debounce_seconds": 2.0
            }
        }

        self.save(default_config)

    def get_file_patterns(self) -> Dict[str, list]:
        """Get file patterns from config."""
        config = self.load()
        return config['indexing']['file_patterns']

    def get_parser_config(self, parser_type: str) -> Dict[str, Any]:
        """Get parser-specific configuration."""
        config = self.load()
        parser_configs = config['indexing'].get('parser_config', {})
        return parser_configs.get(parser_type, {})
```

#### 2.2 JavaScript Parser
**File**: `claude_indexer/analysis/javascript_parser.py`
```python
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from tree_sitter import Parser, Node
import tree_sitter_javascript as tjs
from .entities import Entity, Relation, EntityChunk, EntityType, RelationType
from .parser import CodeParser, ParserResult

class JavaScriptParser(CodeParser):
    """Parse JavaScript/TypeScript files using tree-sitter with v2.4 progressive disclosure."""

    SUPPORTED_EXTENSIONS = ['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs']

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.parser = Parser()
        self.parser.set_language(tjs.language)

    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        return file_path.suffix in self.SUPPORTED_EXTENSIONS

    def get_supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: Path) -> ParserResult:
        """Parse JavaScript/TypeScript file content with progressive disclosure."""
        # Read file content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        tree = self.parser.parse(bytes(content, "utf8"))

        entities = []
        relations = []
        chunks = []

        # Extract functions with progressive disclosure
        for node in self._find_nodes(tree.root_node, ['function_declaration',
                                                      'arrow_function',
                                                      'function_expression']):
            entity, entity_chunks = self._create_function_entity(node, file_path, content)
            if entity:
                entities.append(entity)
                chunks.extend(entity_chunks)

        # Extract classes with progressive disclosure
        for node in self._find_nodes(tree.root_node, ['class_declaration']):
            entity, entity_chunks = self._create_class_entity(node, file_path, content)
            if entity:
                entities.append(entity)
                chunks.extend(entity_chunks)

        # Extract imports
        for node in self._find_nodes(tree.root_node, ['import_statement']):
            relation = self._create_import_relation(node, file_path)
            if relation:
                relations.append(relation)

        return ParserResult(
            file_path=file_path,
            entities=entities,
            relations=relations,
            implementation_chunks=chunks
        )

    def _create_function_entity(self, node: Node, file_path: Path, content: str) -> Tuple[Optional[Entity], List[EntityChunk]]:
        """Create function entity with metadata and implementation chunks."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return None, []

        name = name_node.text.decode('utf8')

        # Create entity (matches current system)
        entity = Entity(
            name=name,
            entity_type=EntityType.FUNCTION,
            observations=[f"Function: {name}"],
            file_path=file_path,
            line_number=node.start_point[0] + 1,
            end_line_number=node.end_point[0] + 1,
            signature=self._extract_function_signature(node, content)
        )

        # Create chunks for progressive disclosure
        chunks = []

        # Metadata chunk (using same ID format as current system)
        metadata_content = self._extract_function_signature(node, content)
        chunks.append(EntityChunk(
            id=f"{str(file_path)}::{name}::metadata",
            entity_name=name,
            chunk_type="metadata",
            content=metadata_content,
            metadata={
                "entity_type": "function",
                "file_path": str(file_path),
                "line_number": node.start_point[0] + 1,
                "has_implementation": True
            }
        ))

        # Implementation chunk
        implementation = self._extract_implementation(node, content)
        chunks.append(EntityChunk(
            id=f"{str(file_path)}::{name}::implementation",
            entity_name=name,
            chunk_type="implementation",
            content=implementation,
            metadata={
                "entity_type": "function",
                "file_path": str(file_path),
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1
            }
        ))

        return entity, chunks

    def _find_nodes(self, root: Node, node_types: List[str]) -> List[Node]:
        """Find all nodes of given types."""
        nodes = []

        def walk(node: Node):
            if node.type in node_types:
                nodes.append(node)
            for child in node.children:
                walk(child)

        walk(root)
        return nodes


    def _extract_function_signature(self, node: Node, content: str) -> str:
        """Extract function signature from node."""
        # Get function name
        name_node = node.child_by_field_name('name')
        name = name_node.text.decode('utf8') if name_node else 'anonymous'

        # Get parameters
        params_node = node.child_by_field_name('parameters')
        if params_node:
            params = params_node.text.decode('utf8')
        else:
            params = '(...)'

        # Get return type for TypeScript
        return_type = ''
        type_node = node.child_by_field_name('return_type')
        if type_node:
            return_type = f": {type_node.text.decode('utf8')}"

        return f"function {name}{params}{return_type}"

    def _extract_implementation(self, node: Node, content: str) -> str:
        """Extract full implementation from node."""
        start_byte = node.start_byte
        end_byte = node.end_byte
        return content[start_byte:end_byte]

    def _create_class_entity(self, node: Node, file_path: Path, content: str) -> Tuple[Optional[Entity], List[EntityChunk]]:
        """Create class entity with metadata and implementation chunks."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return None, []

        name = name_node.text.decode('utf8')

        # Create entity (matches current system)
        entity = Entity(
            name=name,
            entity_type=EntityType.CLASS,
            observations=[f"Class: {name}"],
            file_path=file_path,
            line_number=node.start_point[0] + 1,
            end_line_number=node.end_point[0] + 1
        )

        # Create chunks
        chunks = []

        # Metadata chunk (using same ID format as current system)
        chunks.append(EntityChunk(
            id=f"{str(file_path)}::{name}::metadata",
            entity_name=name,
            chunk_type="metadata",
            content=f"class {name}",
            metadata={
                "entity_type": "class",
                "file_path": str(file_path),
                "line_number": node.start_point[0] + 1,
                "has_implementation": True
            }
        ))

        # Implementation chunk
        chunks.append(EntityChunk(
            id=f"{str(file_path)}::{name}::implementation",
            entity_name=name,
            chunk_type="implementation",
            content=self._extract_implementation(node, content),
            metadata={
                "entity_type": "class",
                "file_path": str(file_path),
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1
            }
        ))

        return entity, chunks

    def _create_import_relation(self, node: Node, file_path: Path) -> Optional[Relation]:
        """Create import relation from node."""
        # Extract module name from import statement
        source_node = node.child_by_field_name('source')
        if not source_node:
            return None

        module_name = source_node.text.decode('utf8').strip('"\'')

        return Relation(
            from_entity=str(file_path),
            to_entity=module_name,
            relation_type=RelationType.IMPORTS,
            context=f"Line {node.start_point[0] + 1}"
        )
```

#### 2.3 Text Parser
**File**: `claude_indexer/analysis/text_parser.py`
```python
from pathlib import Path
from typing import List, Dict, Any
from .entities import Entity, EntityChunk, EntityType
from .parser import CodeParser, ParserResult

class TextParser(CodeParser):
    """Parse plain text files with configurable chunking for v2.4 progressive disclosure."""

    SUPPORTED_EXTENSIONS = ['.txt', '.log', '.csv', '.dat', '.conf', '.ini']

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        return file_path.suffix in self.SUPPORTED_EXTENSIONS

    def get_supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: Path) -> ParserResult:
        """Parse text file into searchable chunks with progressive disclosure."""
        # Read file content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        max_line_length = self.config.get('max_line_length', 500)
        chunk_size = self.config.get('chunk_size', 50)  # lines per chunk

        lines = content.splitlines()
        entities = []
        chunks = []

        for i in range(0, len(lines), chunk_size):
            chunk_lines = lines[i:i + chunk_size]
            chunk_text = '\n'.join(line[:max_line_length] for line in chunk_lines)

            if chunk_text.strip():
                chunk_name = f"{file_path.stem}_chunk_{i//chunk_size + 1}"

                # Create entity (matches current system)
                entity = Entity(
                    name=chunk_name,
                    entity_type=EntityType.DOCUMENTATION,  # Text files as documentation
                    observations=[f"Text chunk from {file_path.name}"],
                    file_path=file_path,
                    line_number=i + 1,
                    end_line_number=min(i + chunk_size, len(lines))
                )
                entities.append(entity)

                # Create metadata chunk (using same ID format as current system)
                chunks.append(EntityChunk(
                    id=f"{str(file_path)}::{chunk_name}::metadata",
                    entity_name=chunk_name,
                    chunk_type="metadata",
                    content=chunk_text,
                    metadata={
                        "entity_type": "text_chunk",
                        "file_path": str(file_path),
                        "line_start": i + 1,
                        "line_end": min(i + chunk_size, len(lines)),
                        "has_implementation": False  # Text chunks don't have separate implementation
                    }
                ))

        return ParserResult(
            file_path=file_path,
            entities=entities,
            relations=[],
            implementation_chunks=chunks
        )

```

### Phase 3: Update Core Services

#### 3.1 Update CoreIndexer Integration
**File**: `claude_indexer/index_project.py` (key changes to work with CoreIndexer)
```python
class ProjectIndexer:
    """High-level indexer that uses CoreIndexer with project-local config."""

    def __init__(self, project_path: str, collection_name: str = None, config: IndexerConfig = None):
        self.project_path = Path(project_path).resolve()
        self.project_config = ProjectConfig(self.project_path)

        if not self.project_config.exists():
            raise ValueError(f"Project not initialized. Run 'claude-indexer init' in {project_path}")

        # Load project-local configuration
        project_cfg = self.project_config.load()
        self.collection = collection_name or project_cfg['project']['collection']
        self.file_patterns = project_cfg['indexing']['file_patterns']

        # Initialize CoreIndexer with project config
        self.config = config or load_config()
        self.core_indexer = CoreIndexer(
            config=self.config,
            parser_registry=self._create_parser_registry(project_cfg),
            embedder=self._create_embedder(),
            vector_store=self._create_vector_store()
        )

    def _create_parser_registry(self, project_cfg: Dict[str, Any]) -> ParserRegistry:
        """Create parser registry with all parsers."""
        # ParserRegistry automatically registers all parsers
        return ParserRegistry(self.project_path)

    def _get_state_file(self) -> Path:
        """Get project-local state file."""
        return self.project_config.state_file

    def _should_process_file(self, file_path: Path) -> bool:
        """Check if file matches project patterns."""
        relative_path = file_path.relative_to(self.project_path)
        path_str = str(relative_path)

        # Check excludes first
        for pattern in self.file_patterns['exclude']:
            if fnmatch.fnmatch(path_str, pattern):
                return False

        # Check includes
        for pattern in self.file_patterns['include']:
            if fnmatch.fnmatch(path_str, pattern):
                return True

        return False

    def index_directory(self, incremental: bool = True) -> IndexingResult:
        """Index directory using project-local configuration."""
        # Load state from project-local state file
        state = self._load_state()

        # Discover files matching project patterns
        files_to_process = []
        for pattern in self.file_patterns['include']:
            files_to_process.extend(self.project_path.glob(f"**/{pattern}"))

        # Filter by should_process and state
        filtered_files = [f for f in files_to_process if self._should_process_file(f)]

        # Use CoreIndexer to process files
        return self.core_indexer.index_files(
            file_paths=filtered_files,
            collection_name=self.collection,
            state=state if incremental else None
        )
```

#### 3.2 Update Service
**File**: `claude_indexer/service.py` (key changes)
```python
class IndexingService:
    """Service to manage multiple project watchers."""

    PROJECTS_REGISTRY = Path.home() / '.claude-indexer' / 'projects.json'

    def __init__(self):
        self.projects = {}
        self.observers = {}

    def load_registry(self) -> Dict[str, Any]:
        """Load projects registry."""
        if not self.PROJECTS_REGISTRY.exists():
            return {"version": "2.0", "projects": []}

        with open(self.PROJECTS_REGISTRY) as f:
            return json.load(f)

    def save_registry(self, registry: Dict[str, Any]) -> None:
        """Save projects registry."""
        self.PROJECTS_REGISTRY.parent.mkdir(parents=True, exist_ok=True)

        with open(self.PROJECTS_REGISTRY, 'w') as f:
            json.dump(registry, f, indent=2)

    def register_project(self, project_path: Path) -> None:
        """Register a project for service management."""
        registry = self.load_registry()

        project_entry = {
            "path": str(project_path.resolve()),
            "active": True
        }

        # Check if already exists
        for i, proj in enumerate(registry['projects']):
            if proj['path'] == project_entry['path']:
                registry['projects'][i] = project_entry
                self.save_registry(registry)
                return

        registry['projects'].append(project_entry)
        self.save_registry(registry)

    def start_project_watcher(self, project_path: Path) -> None:
        """Start watching a specific project."""
        project_config = ProjectConfig(project_path)
        if not project_config.exists():
            logger.warning(f"Project {project_path} not initialized, skipping")
            return

        config = project_config.load()
        if not config['watcher']['enabled']:
            logger.info(f"Watcher disabled for {project_path}")
            return

        # Start file watcher for this project
        # Implementation continues...
```

#### 3.3 Update Parser Registry
**File**: `claude_indexer/analysis/parser.py` (modifications to _register_default_parsers)
```python
# At the top of the file, add imports:
# from .javascript_parser import JavaScriptParser
# from .text_parser import TextParser

class ParserRegistry:
    """Registry for managing multiple code parsers."""

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self._parsers: List[CodeParser] = []
        self._register_default_parsers()

    def _register_default_parsers(self):
        """Register default parsers."""
        # Always register Python and Markdown
        self.register(PythonParser(self.project_path))
        self.register(MarkdownParser())

        # Always register new parsers - they check file extensions internally
        self.register(JavaScriptParser())
        self.register(TextParser())
```

### Phase 4: CLI Commands

#### 4.1 Initialize Command
```python
@app.command()
def init(
    name: Optional[str] = typer.Option(None, help="Project name"),
    collection: Optional[str] = typer.Option(None, help="Collection name")
):
    """Initialize Claude indexer in current directory."""
    project_path = Path.cwd()
    project_config = ProjectConfig(project_path)

    if project_config.exists():
        typer.echo("Project already initialized")
        raise typer.Exit(1)

    project_config.initialize(name=name, collection=collection)
    typer.echo(f"✅ Initialized project at {project_config.config_file}")
```

#### 4.2 Index Command
```python
@app.command()
def index(
    project_path: Optional[str] = typer.Argument(None, help="Project path"),
    incremental: bool = typer.Option(True, help="Use incremental indexing"),
    clear: bool = typer.Option(False, help="Clear collection first")
):
    """Index project files."""
    path = Path(project_path) if project_path else Path.cwd()
    project_config = ProjectConfig(path)

    if not project_config.exists():
        typer.echo(f"Project not initialized. Run 'claude-indexer init' first")
        raise typer.Exit(1)

    indexer = Indexer(str(path))

    if clear:
        indexer.clear_collection()

    indexer.index_directory(incremental=incremental)
```

#### 4.3 Project Commands
```python
@project_app.command("register")
def register_project(project_path: str):
    """Register project with service."""
    path = Path(project_path).resolve()
    project_config = ProjectConfig(path)

    if not project_config.exists():
        typer.echo(f"Project not initialized at {path}")
        raise typer.Exit(1)

    service = IndexingService()
    service.register_project(path)
    typer.echo(f"✅ Registered project: {path}")

@project_app.command("set-patterns")
def set_patterns(
    include: str = typer.Option(None, help="Include patterns (comma-separated)"),
    exclude: str = typer.Option(None, help="Exclude patterns (comma-separated)"),
    project_path: Optional[str] = typer.Argument(None)
):
    """Update project file patterns."""
    path = Path(project_path) if project_path else Path.cwd()
    project_config = ProjectConfig(path)

    config = project_config.load()

    if include:
        config['indexing']['file_patterns']['include'] = include.split(',')
    if exclude:
        config['indexing']['file_patterns']['exclude'] = exclude.split(',')

    project_config.save(config)
    typer.echo("✅ Updated file patterns")
```

### Phase 5: Remove Legacy Code and Hardcoded Patterns

#### 5.1 Complete Removal of Hardcoded Patterns
**Audit all files for orphaned hardcoded settings:**

1. **claude_indexer/config.py**
   - Remove: `include_patterns = ['*.py', '*.md']`
   - Remove: `exclude_patterns = ['*.pyc', '__pycache__', '.git', '.venv', 'node_modules']`
   - Replace with dynamic loading from project config

2. **claude_indexer/watcher/handler.py**
   - Remove: Line 35 `self.watch_patterns = ["*.py", "*.md"]`
   - Remove: Line 340 fallback patterns
   - Remove: Line 457 constructor defaults
   - Replace with project config loading

3. **claude_indexer/service.py**
   - Remove: Line 24 `CONFIG_FILE = Path.home() / '.claude-indexer' / 'config.json'`
   - Remove: Lines 32-67 global config loading
   - Remove: Lines 68-81 global config saving
   - Replace with project registry only

4. **claude_indexer/indexer.py**
   - Remove: Line 97 centralized state directory
   - Remove: Line 339 hardcoded ignore patterns
   - Remove: Lines 102-141 centralized state management
   - Replace with project-local state

5. **Ensure NO orphaned patterns remain**
   - Search entire codebase for string literals: `"*.py"`, `"*.md"`, `"*.pyc"`
   - Replace ALL with configuration-driven patterns
   - No fallback patterns anywhere in code

#### 5.2 Simplified Config Loading
```python
# No fallbacks, no migrations, just clean project-local config
def load_project_config(project_path: Path) -> Dict[str, Any]:
    """Load project configuration."""
    project_config = ProjectConfig(project_path)
    if not project_config.exists():
        raise ValueError(f"Project not initialized at {project_path}")
    return project_config.load()
```

## Comprehensive Testing Strategy

### Unit Tests

1. **test_project_config.py**
   ```python
   def test_initialize_project():
       """Test project initialization creates correct structure."""
   def test_custom_file_patterns():
       """Test each project can set custom include/exclude patterns."""
   def test_js_txt_patterns():
       """Verify JS and TXT files are properly configured."""
   def test_no_hardcoded_defaults():
       """Ensure no hardcoded patterns exist in config."""
   ```

2. **test_javascript_parser.py**
   ```python
   def test_parse_js_functions():
       """Test parsing of JavaScript functions."""
   def test_parse_typescript_classes():
       """Test TypeScript class extraction."""
   def test_jsx_components():
       """Test React/JSX component parsing."""
   def test_import_export_relations():
       """Test ES6 import/export detection."""
   ```

3. **test_text_parser.py**
   ```python
   def test_text_file_chunking():
       """Test .txt file splitting into chunks."""
   def test_large_text_files():
       """Test handling of large text files."""
   def test_various_text_formats():
       """Test .log, .csv, .conf file parsing."""
   def test_encoding_handling():
       """Test UTF-8 and other encodings."""
   ```

### Integration Tests

1. **test_project_patterns_integration.py**
   ```python
   def test_project_specific_js_indexing():
       """Test project with JS files only."""
   def test_project_specific_txt_indexing():
       """Test project with TXT files only."""
   def test_mixed_file_types():
       """Test project with PY, JS, TXT files."""
   def test_pattern_exclusion():
       """Test exclude patterns work correctly."""
   ```

2. **test_no_hardcoded_patterns.py**
   ```python
   def test_no_fallback_patterns():
       """Verify no hardcoded patterns in any module."""
   def test_config_required():
       """Test that missing config raises error."""
   def test_dynamic_pattern_loading():
       """Test all patterns come from config."""
   ```

### End-to-End Tests

1. **test_js_txt_workflow.py**
   ```python
   def test_init_with_js_txt_patterns():
       """Test: init → add JS/TXT patterns → index → verify."""
   def test_change_patterns_reindex():
       """Test: change patterns → reindex → verify new files."""
   def test_per_project_isolation():
       """Test: two projects with different patterns."""
   ```

2. **test_cli_pattern_commands.py**
   ```python
   def test_set_patterns_command():
       """Test: claude-indexer project set-patterns --include "*.js,*.txt"."""
   def test_show_patterns():
       """Test: claude-indexer project show displays patterns."""
   def test_parser_list():
       """Test: claude-indexer parsers list shows JS/TXT."""
   ```

### Verification Tests

1. **test_complete_removal_hardcoded.py**
   ```python
   def test_grep_no_hardcoded_patterns():
       """Grep codebase for "*.py", "*.md" literals - should find none."""
   def test_all_patterns_configurable():
       """Verify every file pattern is configuration-driven."""
   ```

## Streamlined Implementation Timeline (3-4 Days)

### Day 1: Core Infrastructure & Parser Development
1. Create `ProjectConfig` class for project-local configuration
2. Implement JavaScript parser using tree-sitter-javascript
3. Implement configurable text parser with chunking
4. Update parser registry to include new parsers
5. Remove ALL hardcoded patterns from codebase

### Day 2: Integration & Migration
1. Update CoreIndexer to use project-local config
2. Modify state management to use `PROJECT_DIR/.claude-indexer/state.json`
3. Update WatcherBridgeHandler to load patterns from ProjectConfig
4. Update service to read project-local configs
5. Ensure backward compatibility during transition

### Day 3: CLI & Testing
1. Add `claude-indexer init` command for project initialization
2. Update existing commands to work with project-local config
3. Add `claude-indexer project set-patterns` command
4. Write comprehensive tests for JS/TXT parsing
5. Test project isolation and pattern configuration

### Day 4: Documentation & Deployment
1. Update README with new project-local approach
2. Update CLAUDE.md with examples
3. Create migration guide for existing users
4. Final testing and bug fixes
5. Release as v2.5.0

## Key Requirements Summary

### Core Requirements from Discussion
1. **Add JS and TXT file support** - Primary request
2. **Project-level settings** - Each project chooses its own file types
3. **Settings location** - PROJECT_DIR/.claude-indexer/config.json (NOT user directory)
4. **Clean implementation** - No dual mode, no legacy support
5. **Remove ALL hardcoded patterns** - No orphaned settings anywhere

### Implementation Verification Checklist
- [ ] JavaScript parser implemented with tree-sitter
- [ ] Text parser implemented with chunking
- [ ] All hardcoded patterns removed from codebase
- [ ] Project config in PROJECT_DIR/.claude-indexer/config.json
- [ ] Each project can independently configure file patterns
- [ ] State file moved to project-local directory
- [ ] No fallback patterns in any module
- [ ] Comprehensive tests for JS/TXT parsing
- [ ] CLI commands for pattern management
- [ ] Service supports multiple projects with different patterns

## Success Criteria

1. ✅ **JavaScript & Text Support**: Full parsing support for .js, .txt files as requested
2. ✅ **Project Independence**: Each project configures its own file patterns
3. ✅ **Clean Architecture**: No legacy code, dual modes, or hardcoded patterns
4. ✅ **Project-Local Everything**: Config and state in PROJECT_DIR/.claude-indexer/
5. ✅ **No Orphaned Settings**: Complete removal of all hardcoded file patterns
6. ✅ **Multi-Language Support**: Python, JS, TS, TXT, and extensible for more
7. ✅ **Simple CLI**: Intuitive commands (init, index, set-patterns)
8. ✅ **Service Compatible**: Multi-project watching with isolation
9. ✅ **Comprehensive Tests**: Full coverage of new functionality
10. ✅ **Documentation**: Clear examples and migration guide

## Integration with v2.4.1 Architecture

### How This Enhancement Builds on v2.4.1
1. **Progressive Disclosure**: New parsers generate EntityChunk objects with metadata/implementation separation
2. **CoreIndexer**: ProjectIndexer wraps CoreIndexer, providing project-local configuration layer
3. **VectorStore**: Unchanged - continues to store EntityChunks with dual vectors
4. **Parser Registry**: Extended with JavaScript and Text parsers following existing CodeParser interface
5. **Dual Embeddings**: Works seamlessly with Voyage AI / OpenAI provider selection

### Key Architecture Alignments
- EntityChunk and ChunkType from v2.4 used by new parsers
- ParserResult includes chunks list for progressive disclosure
- CoreIndexer remains stateless - ProjectIndexer handles state management
- Existing embedding and storage infrastructure unchanged
- MCP server integration continues to work with project-local collections

## v2.5.0 Release Summary

### Breaking Changes
- Configuration moves from `~/.claude-indexer/config.json` to `PROJECT_DIR/.claude-indexer/config.json`
- State files move to project-local directories
- Service registry simplified to only store project paths

### New Features
- JavaScript/TypeScript parsing with tree-sitter (with progressive disclosure)
- Configurable text file parsing with chunking
- Per-project file pattern configuration
- `claude-indexer init` command for project setup
- Complete removal of hardcoded patterns
- Full integration with v2.4 progressive disclosure architecture

### Migration Path
1. Run `claude-indexer init` in each project directory
2. Customize file patterns with `claude-indexer project set-patterns`
3. Service automatically discovers project-local configs
4. Old global configs can be deleted after migration
5. Existing v2.4 collections remain compatible
