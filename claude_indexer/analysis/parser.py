"""Code parsing abstractions with Tree-sitter and Jedi integration."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import hashlib
import re

try:
    import tree_sitter
    import tree_sitter_python as tspython
    import jedi
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

# Import entities at module level to avoid scope issues
try:
    from .entities import Entity, Relation, EntityFactory, RelationFactory, EntityType, RelationType, EntityChunk
    ENTITIES_AVAILABLE = True
except ImportError:
    ENTITIES_AVAILABLE = False


@dataclass
class ParserResult:
    """Result of parsing a code file."""
    
    file_path: Path
    entities: List['Entity']
    relations: List['Relation']
    
    # Progressive disclosure: implementation chunks for dual storage
    implementation_chunks: List['EntityChunk'] = None
    
    # Metadata
    parsing_time: float = 0.0
    file_hash: str = ""
    errors: List[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
        if self.implementation_chunks is None:
            self.implementation_chunks = []
    
    @property
    def success(self) -> bool:
        """Check if parsing was successful."""
        return len(self.errors) == 0
    
    @property
    def entity_count(self) -> int:
        """Number of entities found."""
        return len(self.entities)
    
    @property
    def relation_count(self) -> int:
        """Number of relations found."""
        return len(self.relations)


class CodeParser(ABC):
    """Abstract base class for code parsers."""
    
    @abstractmethod
    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the given file."""
        pass
    
    @abstractmethod
    def parse(self, file_path: Path) -> ParserResult:
        """Parse the file and extract entities and relations."""
        pass
    
    @abstractmethod
    def get_supported_extensions(self) -> List[str]:
        """Get list of supported file extensions."""
        pass


class PythonParser(CodeParser):
    """Parser for Python files using Tree-sitter and Jedi."""
    
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self._parser = None
        self._project = None
        
        if TREE_SITTER_AVAILABLE:
            self._initialize_parsers()
    
    def _initialize_parsers(self):
        """Initialize Tree-sitter and Jedi parsers."""
        try:
            # Initialize Tree-sitter
            language = tree_sitter.Language(tspython.language())
            self._parser = tree_sitter.Parser(language)
            
            # Initialize Jedi project
            self._project = jedi.Project(str(self.project_path))
            
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Python parser: {e}")
    
    def can_parse(self, file_path: Path) -> bool:
        """Check if this is a Python file."""
        return file_path.suffix == '.py' and TREE_SITTER_AVAILABLE
    
    def get_supported_extensions(self) -> List[str]:
        """Get supported extensions."""
        return ['.py']
    
    def parse(self, file_path: Path) -> ParserResult:
        """Parse Python file using Tree-sitter and Jedi."""
        import time
        
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        
        try:
            # Calculate file hash
            result.file_hash = self._get_file_hash(file_path)
            
            # Parse with Tree-sitter
            tree = self._parse_with_tree_sitter(file_path)
            if tree:
                # Check for syntax errors in the parse tree
                if self._has_syntax_errors(tree):
                    result.errors.append(f"Syntax errors detected in {file_path.name}")
                
                ts_entities = self._extract_tree_sitter_entities(tree, file_path)
                result.entities.extend(ts_entities)
            
            # Analyze with Jedi for semantic information
            jedi_analysis = self._analyze_with_jedi(file_path)
            jedi_entities, jedi_relations = self._process_jedi_analysis(jedi_analysis, file_path)
            
            result.entities.extend(jedi_entities)
            result.relations.extend(jedi_relations)
            
            # Progressive disclosure: Extract implementation chunks for v2.4
            implementation_chunks = self._extract_implementation_chunks(file_path, tree)
            result.implementation_chunks.extend(implementation_chunks)
            
            # Create CALLS relations from extracted function calls
            calls_relations = self._create_calls_relations_from_chunks(implementation_chunks, file_path)
            result.relations.extend(calls_relations)
            
            # Create file entity
            file_entity = EntityFactory.create_file_entity(file_path, 
                                                         entity_count=len(result.entities),
                                                         parsing_method="tree-sitter+jedi")
            result.entities.insert(0, file_entity)  # File first
            
            # Create containment relations
            file_name = str(file_path)
            for entity in result.entities[1:]:  # Skip file entity itself
                if entity.entity_type in [EntityType.FUNCTION, EntityType.CLASS]:
                    relation = RelationFactory.create_contains_relation(file_name, entity.name)
                    result.relations.append(relation)
            
        except Exception as e:
            result.errors.append(f"Parsing failed: {e}")
        
        result.parsing_time = time.time() - start_time
        return result
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file contents."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""
    
    def _parse_with_tree_sitter(self, file_path: Path) -> Optional['tree_sitter.Tree']:
        """Parse file with Tree-sitter."""
        try:
            with open(file_path, 'rb') as f:
                source_code = f.read()
            return self._parser.parse(source_code)
        except Exception as e:
            return None
    
    def _has_syntax_errors(self, tree: 'tree_sitter.Tree') -> bool:
        """Check if the parse tree contains syntax errors."""
        def check_node_for_errors(node):
            # Tree-sitter marks syntax errors with 'ERROR' node type
            if node.type == 'ERROR':
                return True
            # Recursively check children
            for child in node.children:
                if check_node_for_errors(child):
                    return True
            return False
        
        return check_node_for_errors(tree.root_node)
    
    def _extract_tree_sitter_entities(self, tree: 'tree_sitter.Tree', file_path: Path) -> List['Entity']:
        """Extract entities from Tree-sitter AST."""
        
        entities = []
        
        def traverse_node(node, depth=0):
            entity_mapping = {
                'function_definition': EntityType.FUNCTION,
                'class_definition': EntityType.CLASS
            }
            
            if node.type in entity_mapping:
                entity = self._extract_named_entity(node, entity_mapping[node.type], file_path)
                if entity:
                    entities.append(entity)
            
            # Recursively traverse children
            for child in node.children:
                traverse_node(child, depth + 1)
        
        traverse_node(tree.root_node)
        return entities
    
    def _extract_named_entity(self, node: 'tree_sitter.Node', entity_type: 'EntityType', 
                             file_path: Path) -> Optional['Entity']:
        """Extract named entity from Tree-sitter node."""
        
        # Find identifier child
        entity_name = None
        for child in node.children:
            if child.type == 'identifier':
                entity_name = child.text.decode('utf-8')
                break
        
        if not entity_name:
            return None
        
        line_number = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        
        if entity_type == EntityType.FUNCTION:
            return EntityFactory.create_function_entity(
                name=entity_name,
                file_path=file_path,
                line_number=line_number,
                metadata={"end_line": end_line, "source": "tree-sitter"}
            )
        elif entity_type == EntityType.CLASS:
            return EntityFactory.create_class_entity(
                name=entity_name,
                file_path=file_path,
                line_number=line_number,
                metadata={"end_line": end_line, "source": "tree-sitter"}
            )
        
        return None
    
    def _analyze_with_jedi(self, file_path: Path) -> Dict[str, Any]:
        """Analyze file with Jedi for semantic information."""
        try:
            script = jedi.Script(path=str(file_path), project=self._project)
            names = script.get_names(all_scopes=True, definitions=True)
            
            analysis = {
                'functions': [],
                'classes': [],
                'imports': [],
                'variables': []
            }
            
            for name in names:
                if name.type == 'function':
                    analysis['functions'].append({
                        'name': name.name,
                        'line': name.line,
                        'docstring': name.docstring() if hasattr(name, 'docstring') else None,
                        'full_name': name.full_name
                    })
                elif name.type == 'class':
                    analysis['classes'].append({
                        'name': name.name,
                        'line': name.line,
                        'docstring': name.docstring() if hasattr(name, 'docstring') else None,
                        'full_name': name.full_name
                    })
                elif name.type == 'module':
                    analysis['imports'].append({
                        'name': name.name,
                        'full_name': name.full_name
                    })
            
            return analysis
        except Exception as e:
            return {'functions': [], 'classes': [], 'imports': [], 'variables': []}
    
    def _process_jedi_analysis(self, analysis: Dict[str, Any], 
                              file_path: Path) -> tuple[List['Entity'], List['Relation']]:
        """Process Jedi analysis results into entities and relations."""
        
        entities = []
        relations = []
        
        # Process functions with enhanced semantic information
        for func in analysis['functions']:
            if func['docstring']:
                entity = EntityFactory.create_function_entity(
                    name=func['name'],
                    file_path=file_path,
                    line_number=func['line'],
                    docstring=func['docstring'],
                    metadata={"source": "jedi", "full_name": func['full_name']}
                )
                entities.append(entity)
        
        # Process classes with enhanced semantic information
        for cls in analysis['classes']:
            if cls['docstring']:
                entity = EntityFactory.create_class_entity(
                    name=cls['name'],
                    file_path=file_path,
                    line_number=cls['line'],
                    docstring=cls['docstring'],
                    metadata={"source": "jedi", "full_name": cls['full_name']}
                )
                entities.append(entity)
        
        # Process imports
        file_name = str(file_path)
        for imp in analysis['imports']:
            relation = RelationFactory.create_imports_relation(
                importer=file_name,
                imported=imp['name'],
                import_type="module"
            )
            relations.append(relation)
        
        return entities, relations
    
    def _extract_implementation_chunks(self, file_path: Path, tree: 'tree_sitter.Tree') -> List['EntityChunk']:
        """Extract full implementation chunks using AST + Jedi for progressive disclosure."""
        if not tree:
            return []
        
        chunks = []
        
        try:
            # Read source code
            with open(file_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            # Create Jedi script for semantic analysis
            script = jedi.Script(source_code, path=str(file_path), project=self._project)
            source_lines = source_code.split('\n')
            
            # Extract function and class implementations
            def traverse_for_implementations(node):
                if node.type in ['function_definition', 'class_definition']:
                    chunk = self._extract_implementation_chunk(node, source_lines, script, file_path)
                    if chunk:
                        chunks.append(chunk)
                
                # Recursively traverse children
                for child in node.children:
                    traverse_for_implementations(child)
            
            traverse_for_implementations(tree.root_node)
            
        except Exception as e:
            # Graceful fallback - implementation chunks are optional
            pass
        
        return chunks
    
    def _extract_implementation_chunk(self, node: 'tree_sitter.Node', source_lines: List[str], 
                                    script: 'jedi.Script', file_path: Path) -> Optional['EntityChunk']:
        """Extract implementation chunk for function or class with semantic metadata."""
        try:
            # Get entity name
            entity_name = None
            for child in node.children:
                if child.type == 'identifier':
                    entity_name = child.text.decode('utf-8')
                    break
            
            if not entity_name:
                return None
            
            # Extract source code lines
            start_line = node.start_point[0]
            end_line = node.end_point[0]
            implementation_lines = source_lines[start_line:end_line + 1]
            implementation = '\n'.join(implementation_lines)
            
            # Extract semantic metadata using Jedi
            semantic_metadata = {}
            try:
                # Get Jedi definition at the entity location
                definitions = script.goto(start_line + 1, node.start_point[1])
                if definitions:
                    definition = definitions[0]
                    semantic_metadata = {
                        "inferred_types": self._get_type_hints(definition),
                        "calls": self._extract_function_calls_from_source(implementation),
                        "imports_used": self._extract_imports_used_in_source(implementation),
                        "exceptions_handled": self._extract_exceptions_from_source(implementation),
                        "complexity": self._calculate_complexity_from_source(implementation)
                    }
            except:
                # Fallback to basic analysis
                semantic_metadata = {
                    "calls": self._extract_function_calls_from_source(implementation),
                    "imports_used": [],
                    "exceptions_handled": [],
                    "complexity": implementation.count('\n') + 1  # Simple line count
                }
            
            return EntityChunk(
                id=f"{hash(str(file_path))}::{entity_name}::implementation",
                entity_name=entity_name,
                chunk_type="implementation",
                content=implementation,
                metadata={
                    "entity_type": "function" if node.type == 'function_definition' else "class",
                    "file_path": str(file_path),
                    "start_line": start_line + 1,
                    "end_line": end_line + 1,
                    "semantic_metadata": semantic_metadata
                }
            )
            
        except Exception as e:
            return None
    
    def _get_type_hints(self, definition) -> Dict[str, str]:
        """Extract type hints from Jedi definition."""
        try:
            type_hints = {}
            if hasattr(definition, 'signature'):
                sig = definition.signature
                if sig:
                    type_hints["signature"] = str(sig)
            return type_hints
        except:
            return {}
    
    def _extract_function_calls_from_source(self, source: str) -> List[str]:
        """Extract function calls from source code using regex."""
        import re
        # Simple regex to find function calls (name followed by parentheses)
        call_pattern = r'(\w+)\s*\('
        calls = re.findall(call_pattern, source)
        # Filter out common keywords and duplicates
        keywords = {'if', 'for', 'while', 'try', 'except', 'with', 'def', 'class', 'return', 'print'}
        return list(set([call for call in calls if call not in keywords]))
    
    def _extract_imports_used_in_source(self, source: str) -> List[str]:
        """Extract imports referenced in the source code."""
        import re
        # Find module.function or module.class patterns
        module_pattern = r'(\w+)\.(\w+)'
        matches = re.findall(module_pattern, source)
        return list(set([f"{module}.{attr}" for module, attr in matches]))
    
    def _extract_exceptions_from_source(self, source: str) -> List[str]:
        """Extract exception types from source code."""
        import re
        # Find except SomeException patterns
        except_pattern = r'except\s+(\w+)'
        exceptions = re.findall(except_pattern, source)
        return list(set(exceptions))
    
    def _calculate_complexity_from_source(self, source: str) -> int:
        """Calculate complexity based on control flow statements."""
        complexity_keywords = ['if', 'elif', 'for', 'while', 'try', 'except', 'with']
        complexity = 1  # Base complexity
        for keyword in complexity_keywords:
            complexity += source.count(f' {keyword} ') + source.count(f'\n{keyword} ')
        return complexity
    
    def _create_calls_relations_from_chunks(self, chunks: List['EntityChunk'], file_path: Path) -> List['Relation']:
        """Create CALLS relations from extracted function calls in implementation chunks."""
        relations = []
        
        for chunk in chunks:
            if chunk.chunk_type == "implementation":
                semantic_metadata = chunk.metadata.get("semantic_metadata", {})
                calls = semantic_metadata.get("calls", [])
                
                for called_function in calls:
                    relation = RelationFactory.create_calls_relation(
                        caller=chunk.entity_name,
                        callee=called_function,
                        context=f"Function call in {file_path.name}"
                    )
                    relations.append(relation)
        
        return relations


class MarkdownParser(CodeParser):
    """Parser for Markdown documentation files."""
    
    def can_parse(self, file_path: Path) -> bool:
        """Check if this is a Markdown file."""
        return file_path.suffix.lower() in ['.md', '.markdown']
    
    def get_supported_extensions(self) -> List[str]:
        """Get supported extensions."""
        return ['.md', '.markdown']
    
    def parse(self, file_path: Path) -> ParserResult:
        """Parse Markdown file to extract documentation entities."""
        import time
        
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        
        try:
            result.file_hash = self._get_file_hash(file_path)
            
            # Create file entity
            file_entity = EntityFactory.create_file_entity(
                file_path,
                content_type="documentation",
                parsing_method="markdown"
            )
            result.entities.append(file_entity)
            
            # Extract headers and structure
            headers = self._extract_headers(file_path)
            result.entities.extend(headers)
            
            # Create containment relations
            file_name = str(file_path)
            for header in headers:
                relation = RelationFactory.create_contains_relation(file_name, header.name)
                result.relations.append(relation)
        
        except Exception as e:
            result.errors.append(f"Markdown parsing failed: {e}")
        
        result.parsing_time = time.time() - start_time
        return result
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file contents."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""
    
    def _extract_headers(self, file_path: Path) -> List['Entity']:
        """Extract headers, links, and code blocks from Markdown file."""
        
        entities = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.split('\n')
            
            # Extract headers
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if line.startswith('#'):
                    # Count the level of the header
                    level = len(line) - len(line.lstrip('#'))
                    header_text = line.lstrip('#').strip()
                    
                    if header_text:
                        entity = Entity(
                            name=header_text,
                            entity_type=EntityType.DOCUMENTATION,
                            observations=[
                                f"Header level {level}: {header_text}",
                                f"Line {line_num} in {file_path.name}"
                            ],
                            file_path=file_path,
                            line_number=line_num,
                            metadata={"header_level": level, "type": "header"}
                        )
                        entities.append(entity)
            
            # Extract links with regex pattern [text](url)
            link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
            for match in re.finditer(link_pattern, content):
                text = match.group(1)
                url = match.group(2)
                
                # Find line number
                line_num = content[:match.start()].count('\n') + 1
                
                entity = Entity(
                    name=f"Link: {text}",
                    entity_type=EntityType.DOCUMENTATION,
                    observations=[
                        f"Link text: {text}",
                        f"URL: {url}",
                        f"Line {line_num} in {file_path.name}"
                    ],
                    file_path=file_path,
                    line_number=line_num,
                    metadata={"type": "link", "url": url, "text": text}
                )
                entities.append(entity)
            
            # Extract code blocks with language detection
            code_block_pattern = r'```(\w+)?\n(.*?)\n```'
            for match in re.finditer(code_block_pattern, content, re.DOTALL):
                language = match.group(1) or "unknown"
                code = match.group(2)
                
                # Find line number
                line_num = content[:match.start()].count('\n') + 1
                
                # Truncate long code blocks
                display_code = code[:100] + "..." if len(code) > 100 else code
                
                entity = Entity(
                    name=f"Code Block ({language})",
                    entity_type=EntityType.DOCUMENTATION,
                    observations=[
                        f"Language: {language}",
                        f"Code: {display_code}",
                        f"Line {line_num} in {file_path.name}",
                        f"Full code length: {len(code)} characters"
                    ],
                    file_path=file_path,
                    line_number=line_num,
                    metadata={"type": "code_block", "language": language, "code": code}
                )
                entities.append(entity)
        
        except Exception:
            pass  # Ignore errors, return what we could extract
        
        return entities


class ParserRegistry:
    """Registry for managing multiple code parsers."""
    
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self._parsers: List[CodeParser] = []
        self._register_default_parsers()
    
    def _register_default_parsers(self):
        """Register default parsers."""
        self.register(PythonParser(self.project_path))
        self.register(MarkdownParser())
    
    def register(self, parser: CodeParser):
        """Register a new parser."""
        self._parsers.append(parser)
    
    def get_parser_for_file(self, file_path: Path) -> Optional[CodeParser]:
        """Get the appropriate parser for a file."""
        for parser in self._parsers:
            if parser.can_parse(file_path):
                return parser
        return None
    
    def parse_file(self, file_path: Path) -> ParserResult:
        """Parse a file using the appropriate parser."""
        parser = self.get_parser_for_file(file_path)
        
        if parser is None:
            result = ParserResult(file_path=file_path, entities=[], relations=[])
            result.errors.append(f"No parser available for {file_path.suffix}")
            return result
        
        return parser.parse(file_path)
    
    def get_supported_extensions(self) -> List[str]:
        """Get all supported file extensions."""
        extensions = set()
        for parser in self._parsers:
            extensions.update(parser.get_supported_extensions())
        return sorted(list(extensions))