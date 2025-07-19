# Enhanced Multi-Language Support Plan v2.5.0

## Overview
Extend the Claude Code Memory system to support multiple file types using tree-sitter parsers wherever beneficial, building on the existing v2.4.1 progressive disclosure architecture.

## Final File Type Strategy

### Tree-sitter Based Parsers
- **Python** (.py, .pyi) - Existing: tree-sitter-python + Jedi
- **JavaScript/TypeScript** (.js, .jsx, .ts, .tsx, .mjs, .cjs) - New: tree-sitter-javascript + TSServer (optional)
- **JSON** (.json) - New: tree-sitter-json
- **HTML** (.html, .htm) - New: tree-sitter-html
- **CSS** (.css, .scss, .sass) - New: tree-sitter-css
- **YAML** (.yaml, .yml) - New: tree-sitter-yaml
- **XML** (.xml) - New: tree-sitter-xml
- **Markdown** (.md, .markdown) - Upgrade to tree-sitter-markdown

### Simple Parsers (No AST benefit)
- **Plain Text** (.txt, .log) - Direct line/chunk processing
- **CSV** (.csv) - Python csv module
- **INI/Config** (.ini, .conf, .cfg) - ConfigParser

## Architecture Design

### 1. Base Parser Infrastructure

All parsers inherit from existing `CodeParser` ABC and integrate with `ParserRegistry`:

```python
# claude_indexer/analysis/base_parsers.py
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import hashlib
import time
from tree_sitter import Parser, Node
from .parser import CodeParser, ParserResult
from .entities import Entity, Relation, EntityChunk, EntityType, RelationType, EntityFactory

class TreeSitterParser(CodeParser):
    """Base class for all tree-sitter based parsers with common functionality."""

    def __init__(self, language_module, config: Dict[str, Any] = None):
        self.config = config or {}
        self.parser = Parser()
        self.parser.set_language(language_module.language)

    def parse_tree(self, content: str):
        """Parse content into tree-sitter AST."""
        return self.parser.parse(bytes(content, "utf8"))

    def extract_node_text(self, node: Node, content: str) -> str:
        """Extract text from tree-sitter node."""
        return content[node.start_byte:node.end_byte]

    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file contents (follows existing pattern)."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""

    def _find_nodes_by_type(self, root: Node, node_types: List[str]) -> List[Node]:
        """Recursively find all nodes matching given types."""
        nodes = []

        def walk(node: Node):
            if node.type in node_types:
                nodes.append(node)
            for child in node.children:
                walk(child)

        walk(root)
        return nodes

    def _create_chunk_id(self, file_path: Path, entity_name: str, chunk_type: str) -> str:
        """Create deterministic chunk ID following existing pattern."""
        # Pattern: {file_path}::{entity_name}::{chunk_type}
        return f"{str(file_path)}::{entity_name}::{chunk_type}"

    def _has_syntax_errors(self, tree) -> bool:
        """Check if the parse tree contains syntax errors."""
        def check_node_for_errors(node):
            if node.type == 'ERROR':
                return True
            for child in node.children:
                if check_node_for_errors(child):
                    return True
            return False

        return check_node_for_errors(tree.root_node)

    def _create_file_entity(self, file_path: Path, entity_count: int = 0,
                           content_type: str = "code") -> Entity:
        """Create file entity using EntityFactory pattern."""
        return EntityFactory.create_file_entity(
            file_path,
            entity_count=entity_count,
            content_type=content_type,
            parsing_method="tree-sitter"
        )
```

### 2. JavaScript/TypeScript Parser

```python
# claude_indexer/analysis/javascript_parser.py
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import time
from tree_sitter import Node
from .base_parsers import TreeSitterParser
from .parser import ParserResult
from .entities import Entity, Relation, EntityChunk, EntityType, RelationType, EntityFactory, RelationFactory

class JavaScriptParser(TreeSitterParser):
    """Parse JS/TS files with tree-sitter, optional TSServer for semantics."""

    SUPPORTED_EXTENSIONS = ['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs']

    def __init__(self, config: Dict[str, Any] = None):
        import tree_sitter_javascript as tsjs
        super().__init__(tsjs, config)
        self.ts_server = self._init_ts_server() if config.get('use_ts_server') else None

    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        return file_path.suffix in self.SUPPORTED_EXTENSIONS

    def get_supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: Path) -> ParserResult:
        """Extract functions, classes, imports with progressive disclosure."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])

        try:
            # Read file and calculate hash
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            result.file_hash = self._get_file_hash(file_path)
            tree = self.parse_tree(content)

            # Check for syntax errors
            if self._has_syntax_errors(tree):
                result.errors.append(f"Syntax errors detected in {file_path.name}")

            # Extract entities and chunks
            entities = []
            chunks = []

            # Extract functions (including arrow functions)
            for node in self._find_nodes_by_type(tree.root_node,
                ['function_declaration', 'arrow_function', 'function_expression', 'method_definition']):
                entity, entity_chunks = self._create_function_entity(node, file_path, content)
                if entity:
                    entities.append(entity)
                    chunks.extend(entity_chunks)

            # Extract classes
            for node in self._find_nodes_by_type(tree.root_node, ['class_declaration', 'class_expression']):
                entity, entity_chunks = self._create_class_entity(node, file_path, content)
                if entity:
                    entities.append(entity)
                    chunks.extend(entity_chunks)

            # Extract TypeScript interfaces
            for node in self._find_nodes_by_type(tree.root_node, ['interface_declaration']):
                entity, entity_chunks = self._create_interface_entity(node, file_path, content)
                if entity:
                    entities.append(entity)
                    chunks.extend(entity_chunks)

            # Extract imports
            relations = []
            for node in self._find_nodes_by_type(tree.root_node, ['import_statement', 'import_from']):
                relation = self._create_import_relation(node, file_path, content)
                if relation:
                    relations.append(relation)

            # Create file entity
            file_entity = self._create_file_entity(file_path, len(entities), "javascript")
            entities.insert(0, file_entity)

            # Create containment relations
            file_name = str(file_path)
            for entity in entities[1:]:  # Skip file entity
                if entity.entity_type in [EntityType.FUNCTION, EntityType.CLASS]:
                    relation = RelationFactory.create_contains_relation(file_name, entity.name)
                    relations.append(relation)

            result.entities = entities
            result.relations = relations
            result.implementation_chunks = chunks

        except Exception as e:
            result.errors.append(f"JavaScript parsing failed: {e}")

        result.parsing_time = time.time() - start_time
        return result

    def _create_function_entity(self, node: Node, file_path: Path, content: str) -> Tuple[Optional[Entity], List[EntityChunk]]:
        """Create function entity with metadata and implementation chunks."""
        name = self._extract_function_name(node, content)
        if not name:
            return None, []

        # Create entity
        entity = EntityFactory.create_function_entity(
            name=name,
            file_path=file_path,
            line_number=node.start_point[0] + 1,
            metadata={
                "end_line": node.end_point[0] + 1,
                "source": "tree-sitter",
                "node_type": node.type
            }
        )

        # Create chunks
        chunks = []

        # Metadata chunk
        signature = self._extract_function_signature(node, content)
        chunks.append(EntityChunk(
            id=self._create_chunk_id(file_path, name, "metadata"),
            entity_name=name,
            chunk_type="metadata",
            content=signature,
            metadata={
                "entity_type": "function",
                "file_path": str(file_path),
                "line_number": node.start_point[0] + 1,
                "has_implementation": True
            }
        ))

        # Implementation chunk
        implementation = self.extract_node_text(node, content)
        chunks.append(EntityChunk(
            id=self._create_chunk_id(file_path, name, "implementation"),
            entity_name=name,
            chunk_type="implementation",
            content=implementation,
            metadata={
                "entity_type": "function",
                "file_path": str(file_path),
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "semantic_metadata": {
                    "calls": self._extract_function_calls(implementation),
                    "complexity": self._calculate_complexity(implementation)
                }
            }
        ))

        return entity, chunks

    def _extract_function_name(self, node: Node, content: str) -> Optional[str]:
        """Extract function name from various function node types."""
        # Handle different function types
        if node.type == 'method_definition':
            # For class methods
            name_node = node.child_by_field_name('name')
            if name_node:
                return self.extract_node_text(name_node, content)

        # For regular functions
        name_node = node.child_by_field_name('name')
        if name_node:
            return self.extract_node_text(name_node, content)

        # For arrow functions assigned to variables
        if node.type == 'arrow_function' and node.parent:
            if node.parent.type == 'variable_declarator':
                id_node = node.parent.child_by_field_name('name')
                if id_node:
                    return self.extract_node_text(id_node, content)

        return None

    def _extract_function_signature(self, node: Node, content: str) -> str:
        """Extract function signature with parameters and return type."""
        name = self._extract_function_name(node, content) or 'anonymous'

        # Get parameters
        params_node = node.child_by_field_name('parameters')
        params = "()"
        if params_node:
            params = self.extract_node_text(params_node, content)

        # Get return type for TypeScript
        return_type = ""
        type_node = node.child_by_field_name('return_type')
        if type_node:
            return_type = f": {self.extract_node_text(type_node, content)}"

        # Handle different function types
        if node.type == 'arrow_function':
            return f"const {name} = {params} => {...}{return_type}"
        else:
            return f"function {name}{params}{return_type}"

    def _extract_function_calls(self, implementation: str) -> List[str]:
        """Extract function calls from implementation (simple heuristic)."""
        import re
        # Simple regex to find function calls
        call_pattern = r'(\w+)\s*\('
        calls = re.findall(call_pattern, implementation)
        # Filter out keywords
        keywords = {'if', 'for', 'while', 'switch', 'catch', 'function', 'class', 'return'}
        return list(set([call for call in calls if call not in keywords]))

    def _calculate_complexity(self, implementation: str) -> int:
        """Calculate cyclomatic complexity (simplified)."""
        complexity = 1
        complexity_keywords = ['if', 'else if', 'for', 'while', 'case', 'catch', '?', '&&', '||']
        for keyword in complexity_keywords:
            complexity += implementation.count(keyword)
        return complexity

    def _create_class_entity(self, node: Node, file_path: Path, content: str) -> Tuple[Optional[Entity], List[EntityChunk]]:
        """Create class entity with chunks."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return None, []

        name = self.extract_node_text(name_node, content)

        # Create entity
        entity = EntityFactory.create_class_entity(
            name=name,
            file_path=file_path,
            line_number=node.start_point[0] + 1,
            metadata={
                "end_line": node.end_point[0] + 1,
                "source": "tree-sitter"
            }
        )

        # Create chunks
        chunks = []

        # Metadata chunk
        chunks.append(EntityChunk(
            id=self._create_chunk_id(file_path, name, "metadata"),
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
            id=self._create_chunk_id(file_path, name, "implementation"),
            entity_name=name,
            chunk_type="implementation",
            content=self.extract_node_text(node, content),
            metadata={
                "entity_type": "class",
                "file_path": str(file_path),
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1
            }
        ))

        return entity, chunks

    def _create_interface_entity(self, node: Node, file_path: Path, content: str) -> Tuple[Optional[Entity], List[EntityChunk]]:
        """Create TypeScript interface entity."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return None, []

        name = self.extract_node_text(name_node, content)

        # Interfaces are like classes but for TypeScript
        entity = Entity(
            name=name,
            entity_type=EntityType.CLASS,  # Reuse class type for interfaces
            observations=[f"TypeScript interface: {name}"],
            file_path=file_path,
            line_number=node.start_point[0] + 1,
            end_line_number=node.end_point[0] + 1
        )

        # Create metadata chunk only (interfaces have no implementation)
        chunks = [EntityChunk(
            id=self._create_chunk_id(file_path, name, "metadata"),
            entity_name=name,
            chunk_type="metadata",
            content=self.extract_node_text(node, content),
            metadata={
                "entity_type": "interface",
                "file_path": str(file_path),
                "line_number": node.start_point[0] + 1,
                "has_implementation": False
            }
        )]

        return entity, chunks

    def _create_import_relation(self, node: Node, file_path: Path, content: str) -> Optional[Relation]:
        """Create import relation from import statement."""
        # Find the source/module being imported
        source_node = None

        # Handle different import statement structures
        for child in node.children:
            if child.type == 'string' and child.parent.type in ['import_statement', 'import_from']:
                source_node = child
                break

        if not source_node:
            return None

        # Extract module name, removing quotes
        module_name = self.extract_node_text(source_node, content).strip('"\'')

        return RelationFactory.create_imports_relation(
            importer=str(file_path),
            imported=module_name,
            import_type="module"
        )

    def _init_ts_server(self):
        """Initialize TypeScript language server (stub for future implementation)."""
        # Future: Could integrate with tsserver for advanced type inference
        return None
```

### 3. JSON Parser

```python
# claude_indexer/analysis/json_parser.py
from pathlib import Path
from typing import List, Dict, Any, Optional
import time
from tree_sitter import Node
from .base_parsers import TreeSitterParser
from .parser import ParserResult
from .entities import Entity, Relation, EntityChunk, EntityType, RelationType, EntityFactory, RelationFactory

class JSONParser(TreeSitterParser):
    """Parse JSON with tree-sitter for structural relation extraction."""

    SUPPORTED_EXTENSIONS = ['.json']

    def __init__(self, config: Dict[str, Any] = None):
        import tree_sitter_json as tsjson
        super().__init__(tsjson, config)
        self.special_files = config.get('special_files', ['package.json', 'tsconfig.json', 'composer.json'])

    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        return file_path.suffix in self.SUPPORTED_EXTENSIONS

    def get_supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: Path) -> ParserResult:
        """Extract JSON structure as entities and relations."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])

        try:
            # Read and parse JSON
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            result.file_hash = self._get_file_hash(file_path)
            tree = self.parse_tree(content)

            # Check for syntax errors
            if self._has_syntax_errors(tree):
                result.errors.append(f"JSON syntax errors in {file_path.name}")

            entities = []
            relations = []
            chunks = []

            # Create file entity
            file_entity = self._create_file_entity(file_path, content_type="configuration")
            entities.append(file_entity)

            # Special handling for known JSON types
            if file_path.name in self.special_files:
                special_entities, special_relations = self._handle_special_json(
                    file_path, tree.root_node, content
                )
                entities.extend(special_entities)
                relations.extend(special_relations)
            else:
                # Generic JSON structure extraction
                root_obj = self._find_first_object(tree.root_node)
                if root_obj:
                    obj_entities, obj_relations = self._extract_object_structure(
                        root_obj, content, file_path, parent_path=""
                    )
                    entities.extend(obj_entities)
                    relations.extend(obj_relations)

            # Create chunks for searchability
            chunks = self._create_json_chunks(file_path, tree.root_node, content)

            result.entities = entities
            result.relations = relations
            result.implementation_chunks = chunks

        except Exception as e:
            result.errors.append(f"JSON parsing failed: {e}")

        result.parsing_time = time.time() - start_time
        return result

    def _find_first_object(self, node: Node) -> Optional[Node]:
        """Find the first object node in the tree."""
        if node.type == 'object':
            return node
        for child in node.children:
            obj = self._find_first_object(child)
            if obj:
                return obj
        return None

    def _extract_object_structure(self, node: Node, content: str, file_path: Path,
                                parent_path: str) -> tuple[List[Entity], List[Relation]]:
        """Extract entities and relations from JSON object structure."""
        entities = []
        relations = []

        # Process object pairs (key-value)
        for child in node.children:
            if child.type == 'pair':
                key_node = child.child_by_field_name('key')
                value_node = child.child_by_field_name('value')

                if key_node and value_node:
                    key = self.extract_node_text(key_node, content).strip('"')
                    current_path = f"{parent_path}.{key}" if parent_path else key

                    # Create entity for this key
                    entity = Entity(
                        name=current_path,
                        entity_type=EntityType.DOCUMENTATION,  # JSON keys as documentation
                        observations=[f"JSON key: {key}"],
                        file_path=file_path,
                        line_number=key_node.start_point[0] + 1
                    )
                    entities.append(entity)

                    # Create containment relation
                    parent = str(file_path) if not parent_path else parent_path
                    relation = RelationFactory.create_contains_relation(parent, current_path)
                    relations.append(relation)

                    # Recursively process nested objects
                    if value_node.type == 'object':
                        nested_entities, nested_relations = self._extract_object_structure(
                            value_node, content, file_path, current_path
                        )
                        entities.extend(nested_entities)
                        relations.extend(nested_relations)

                    # Process arrays
                    elif value_node.type == 'array':
                        # Create collection entity
                        array_entity = Entity(
                            name=f"{current_path}[]",
                            entity_type=EntityType.DOCUMENTATION,
                            observations=[f"JSON array: {key}"],
                            file_path=file_path,
                            line_number=value_node.start_point[0] + 1
                        )
                        entities.append(array_entity)

                        # Array contains relation
                        relation = RelationFactory.create_contains_relation(
                            current_path, f"{current_path}[]"
                        )
                        relations.append(relation)

        return entities, relations

    def _handle_special_json(self, file_path: Path, root: Node, content: str) -> tuple[List[Entity], List[Relation]]:
        """Special handling for known JSON file types."""
        entities = []
        relations = []

        if file_path.name == 'package.json':
            # Extract dependencies as import relations
            deps_entities, deps_relations = self._extract_package_dependencies(root, content, file_path)
            entities.extend(deps_entities)
            relations.extend(deps_relations)

        elif file_path.name == 'tsconfig.json':
            # Extract TypeScript configuration
            config_entities = self._extract_tsconfig_info(root, content, file_path)
            entities.extend(config_entities)

        return entities, relations

    def _extract_package_dependencies(self, root: Node, content: str, file_path: Path) -> tuple[List[Entity], List[Relation]]:
        """Extract dependencies from package.json."""
        entities = []
        relations = []

        # Find dependencies and devDependencies objects
        for node in self._find_nodes_by_type(root, ['pair']):
            key_node = node.child_by_field_name('key')
            if key_node:
                key = self.extract_node_text(key_node, content).strip('"')
                if key in ['dependencies', 'devDependencies']:
                    value_node = node.child_by_field_name('value')
                    if value_node and value_node.type == 'object':
                        # Each dependency is an import relation
                        for pair in value_node.children:
                            if pair.type == 'pair':
                                dep_key = pair.child_by_field_name('key')
                                if dep_key:
                                    dep_name = self.extract_node_text(dep_key, content).strip('"')
                                    # Create import relation
                                    relation = RelationFactory.create_imports_relation(
                                        importer=str(file_path),
                                        imported=dep_name,
                                        import_type="npm_dependency"
                                    )
                                    relations.append(relation)

        return entities, relations

    def _extract_tsconfig_info(self, root: Node, content: str, file_path: Path) -> List[Entity]:
        """Extract TypeScript configuration info."""
        entities = []

        # Create entity for compiler options
        for node in self._find_nodes_by_type(root, ['pair']):
            key_node = node.child_by_field_name('key')
            if key_node:
                key = self.extract_node_text(key_node, content).strip('"')
                if key == 'compilerOptions':
                    entity = Entity(
                        name="TypeScript Compiler Options",
                        entity_type=EntityType.DOCUMENTATION,
                        observations=["TypeScript compiler configuration"],
                        file_path=file_path,
                        line_number=node.start_point[0] + 1
                    )
                    entities.append(entity)

        return entities

    def _create_json_chunks(self, file_path: Path, root: Node, content: str) -> List[EntityChunk]:
        """Create searchable chunks from JSON content."""
        chunks = []

        # For now, create a single chunk with the entire JSON
        # Future: Could chunk by top-level keys for large files
        chunks.append(EntityChunk(
            id=self._create_chunk_id(file_path, "content", "metadata"),
            entity_name=file_path.name,
            chunk_type="metadata",
            content=content[:1000],  # First 1000 chars for search
            metadata={
                "entity_type": "json_file",
                "file_path": str(file_path),
                "has_implementation": False
            }
        ))

        return chunks
```

### 4. HTML Parser

```python
# claude_indexer/analysis/html_parser.py
class HTMLParser(TreeSitterParser):
    """Parse HTML with tree-sitter for structure and components."""

    SUPPORTED_EXTENSIONS = ['.html', '.htm']

    def parse(self, file_path: Path) -> ParserResult:
        """Extract HTML structure, IDs, classes, components."""
        # Implementation extracts:
        # - Elements with IDs as entities
        # - Script/style blocks for further parsing
        # - Component-like structures
        # - Link relations
```

### 5. CSS Parser

```python
# claude_indexer/analysis/css_parser.py
class CSSParser(TreeSitterParser):
    """Parse CSS/SCSS with tree-sitter."""

    SUPPORTED_EXTENSIONS = ['.css', '.scss', '.sass']

    def parse(self, file_path: Path) -> ParserResult:
        """Extract CSS rules, classes, IDs."""
        # Implementation extracts:
        # - Class definitions
        # - ID selectors
        # - CSS variables
        # - @import relations
```

### 6. YAML Parser

```python
# claude_indexer/analysis/yaml_parser.py
class YAMLParser(TreeSitterParser):
    """Parse YAML configuration files."""

    SUPPORTED_EXTENSIONS = ['.yaml', '.yml']

    def parse(self, file_path: Path) -> ParserResult:
        """Extract YAML structure and configuration."""
        # Special handling for:
        # - GitHub Actions workflows
        # - Docker Compose files
        # - Kubernetes manifests
        # - CI/CD configurations
```

### 7. Simple Text Parser

```python
# claude_indexer/analysis/text_parser.py
class TextParser(CodeParser):
    """Parse plain text files with configurable chunking."""

    SUPPORTED_EXTENSIONS = ['.txt', '.log']

    def parse(self, file_path: Path) -> ParserResult:
        """Split text into searchable chunks."""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Chunk by lines (50 lines default)
        chunks = self._create_chunks(content, chunk_size=50)

        # Return chunks as documentation entities
        # No implementation chunks needed (text is the content)
```

### 8. Updated Parser Registry

```python
# claude_indexer/analysis/parser.py
class ParserRegistry:
    def _register_default_parsers(self):
        """Register all available parsers."""
        # Core language parsers
        self.register(PythonParser(self.project_path))
        self.register(JavaScriptParser())

        # Data format parsers
        self.register(JSONParser())
        self.register(YAMLParser())
        self.register(XMLParser())

        # Web parsers
        self.register(HTMLParser())
        self.register(CSSParser())

        # Documentation parsers
        self.register(MarkdownParser())  # Upgrade to tree-sitter
        self.register(TextParser())

        # Config parsers
        self.register(CSVParser())
        self.register(INIParser())
```

### Important Integration Notes

#### Error Handling Pattern
All parsers follow the existing error handling pattern:
- Syntax errors are detected but don't stop parsing
- Errors are collected in `result.errors` list
- Parser continues to extract what it can
- `result.success` property checks if `len(errors) == 0`

#### CoreIndexer Integration
The CoreIndexer automatically:
1. Calls `parser_registry.parse_file(path)` for each file
2. Handles the returned `ParserResult`
3. Stores entities and relations in VectorStore
4. Creates dual embeddings for progressive disclosure chunks

#### File Pattern Matching
Files are matched by existing logic in `_should_process_file()`:
- Project config defines include/exclude patterns
- ParserRegistry finds parser based on file extension
- No parser = file skipped with warning

#### State Management
All parsers integrate with existing incremental indexing:
- File hash stored in `result.file_hash`
- CoreIndexer checks hash against state
- Changed/new files trigger reprocessing

## Project Configuration Integration

Update project configuration to support parser-specific settings:

```json
{
  "indexing": {
    "file_patterns": {
      "include": ["*.py", "*.js", "*.ts", "*.json", "*.yaml", "*.html", "*.css", "*.md", "*.txt"],
      "exclude": ["node_modules", ".git", "dist", "build", "*.min.js"]
    },
    "parser_config": {
      "javascript": {
        "use_ts_server": false,
        "jsx": true,
        "typescript": true
      },
      "json": {
        "extract_schema": true,
        "special_files": ["package.json", "tsconfig.json"]
      },
      "text": {
        "chunk_size": 50,
        "max_line_length": 1000
      },
      "yaml": {
        "detect_type": true  // Auto-detect GitHub Actions, K8s, etc.
      }
    }
  }
}
```

## Implementation Tasks

### Phase 1: Core Parser Development (Day 1)

1. **Base Infrastructure**
   - Create `TreeSitterParser` base class
   - Install tree-sitter language modules
   - Update dependencies in requirements.txt

2. **JavaScript/TypeScript Parser**
   - Implement full JS/TS parsing with tree-sitter-javascript
   - Extract functions, classes, imports
   - Create progressive disclosure chunks
   - Optional TSServer integration stub

3. **JSON Parser**
   - Implement tree-sitter-json parser
   - Extract nested structure as relations
   - Special handling for common JSON types

4. **Update Parser Registry**
   - Register new parsers in `_register_default_parsers()`
   - Ensure backward compatibility

### Phase 2: Additional Parsers (Day 2)

1. **Web Parsers**
   - HTML parser with component detection
   - CSS parser with selector extraction
   - Cross-file relation detection

2. **Config/Data Parsers**
   - YAML parser with type detection
   - XML parser for structured data
   - Simple text chunking parser
   - CSV/INI parsers

3. **Markdown Upgrade**
   - Replace regex parsing with tree-sitter-markdown
   - Maintain backward compatibility

### Phase 3: Integration & Testing (Day 3)

1. **Integration Points**
   - Verify all parsers work with CoreIndexer
   - Test progressive disclosure for new file types
   - Ensure VectorStore handles all entity types

2. **Cross-Language Relations**
   - HTML → CSS (class/ID references)
   - JS → JSON (config imports)
   - YAML → other files (workflow references)

3. **Testing Suite**
   - Unit tests for each parser
   - Integration tests with sample projects
   - Performance benchmarks

### Phase 4: Polish & Documentation (Day 4)

1. **Configuration & CLI**
   - Update project config schema
   - Add parser list command
   - Parser-specific options

2. **Documentation**
   - Update README with supported file types
   - Add examples for each language
   - Migration guide

## Testing Strategy

### Unit Tests

```python
# tests/test_javascript_parser.py
def test_parse_functions():
    """Test function extraction from JS."""

def test_parse_classes():
    """Test class extraction from JS/TS."""

def test_arrow_functions():
    """Test arrow function detection."""

def test_imports_exports():
    """Test ES6 module relations."""

# tests/test_json_parser.py
def test_nested_objects():
    """Test nested object relation extraction."""

def test_package_json():
    """Test special package.json handling."""

def test_large_json():
    """Test performance with large JSON."""
```

### Integration Tests

```python
# tests/integration/test_multi_language.py
def test_full_stack_project():
    """Test project with Python backend, JS frontend, JSON config."""

def test_cross_file_relations():
    """Test relations between different file types."""

def test_progressive_disclosure_all_types():
    """Verify chunks work for all parsers."""
```

### Performance Tests

```python
# tests/performance/test_parser_performance.py
def test_tree_sitter_overhead():
    """Measure tree-sitter parsing performance."""

def test_large_file_handling():
    """Test with large files of each type."""
```

## Critical Implementation Notes

### Avoiding Code Duplication

1. **Use Base Classes**
   - TreeSitterParser for all tree-sitter languages
   - Shared utility functions for common patterns
   - Reuse entity/relation factories

2. **Parser Registry Pattern**
   - Single registration point
   - Dynamic parser discovery
   - No hardcoded file lists

3. **Configuration Driven**
   - All patterns from project config
   - No fallback patterns
   - Parser-specific options

### Clean Architecture

1. **No Legacy Code**
   - Remove all hardcoded patterns
   - No dual-mode support
   - Clean parser interfaces

2. **Consistent Patterns**
   - All parsers follow same structure
   - Uniform error handling
   - Standard logging

3. **Testability**
   - Each parser independently testable
   - Mock tree-sitter for tests
   - Fixture management

## Success Metrics

1. **Language Coverage**: Support for 10+ file types
2. **Relation Extraction**: Cross-language relation detection
3. **Performance**: <100ms per file for most types
4. **Test Coverage**: >90% for all parsers
5. **Memory Efficiency**: Streaming parsing for large files

## Summary

This plan extends Claude Code Memory to support multiple programming languages and data formats using tree-sitter parsers where beneficial. The implementation:

- Leverages tree-sitter for consistent AST parsing across languages
- Maintains progressive disclosure architecture from v2.4
- Enables cross-language relation extraction
- Provides simple parsing for non-code files
- Integrates seamlessly with existing infrastructure

The modular design allows easy addition of new languages while maintaining clean architecture and avoiding code duplication. All parsers follow the same pattern, making the codebase maintainable and extensible.

**Next Steps**: Begin with Phase 1 core parser development, focusing on JavaScript and JSON parsers as the highest priority additions.
