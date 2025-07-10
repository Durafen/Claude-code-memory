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

# Import logger
from ..indexer_logging import get_logger
logger = get_logger()


@dataclass
class ParserResult:
    """Result of parsing a code file."""
    
    file_path: Path
    entities: List['Entity']
    relations: List['Relation']
    
    # Progressive disclosure: implementation chunks for dual storage
    implementation_chunks: Optional[List['EntityChunk']] = None
    
    # Metadata
    parsing_time: float = 0.0
    file_hash: str = ""
    errors: Optional[List[str]] = None
    warnings: Optional[List[str]] = None
    
    def __post_init__(self) -> None:
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
    
    def _create_chunk_id(self, file_path: Path, entity_name: str, chunk_type: str, entity_type: str = None) -> str:
        """Create deterministic chunk ID following existing pattern."""
        # Pattern: {file_path}::{entity_type}::{entity_name}::{chunk_type} (if entity_type provided)
        # Pattern: {file_path}::{entity_name}::{chunk_type} (backward compatibility)
        if entity_type:
            return f"{str(file_path)}::{entity_type}::{entity_name}::{chunk_type}"
        return f"{str(file_path)}::{entity_name}::{chunk_type}"


class PythonParser(CodeParser):
    """Parser for Python files using Tree-sitter and Jedi."""
    
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self._parser = None
        self._project = None
        self._observation_extractor = None
        
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
            
            # Initialize observation extractor
            from .observation_extractor import ObservationExtractor
            self._observation_extractor = ObservationExtractor(self.project_path)
            
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Python parser: {e}")
    
    def can_parse(self, file_path: Path) -> bool:
        """Check if this is a Python file."""
        return file_path.suffix == '.py' and TREE_SITTER_AVAILABLE
    
    def get_supported_extensions(self) -> List[str]:
        """Get supported extensions."""
        return ['.py']
    
    def parse(self, file_path: Path, batch_callback=None, global_entity_names=None) -> ParserResult:
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
                
                # Extract Tree-sitter relations (inheritance, imports)
                ts_relations = self._extract_tree_sitter_relations(tree, file_path)
                result.relations.extend(ts_relations)
            
            # Analyze with Jedi for semantic information (relations only - entities come from Tree-sitter)
            jedi_analysis = self._analyze_with_jedi(file_path)
            _, jedi_relations = self._process_jedi_analysis(jedi_analysis, file_path)
            
            # Only add relations from Jedi, not entities (Tree-sitter handles entities with enhanced observations)
            result.relations.extend(jedi_relations)
            
            # Progressive disclosure: Extract implementation chunks for v2.4
            implementation_chunks = self._extract_implementation_chunks(file_path, tree)
            result.implementation_chunks.extend(implementation_chunks)
            
            # Create CALLS relations from extracted function calls (entity-aware to prevent orphans)
            # Combine current file entities with global entities for comprehensive validation
            all_entity_names = set()
            
            # Add current file entities
            for entity in result.entities:
                all_entity_names.add(entity.name)
            
            # Add global entities if available
            if global_entity_names:
                all_entity_names.update(global_entity_names)
            
            # Convert to pseudo-entities for compatibility with existing method signature
            entity_list_for_calls = [type('Entity', (), {'name': name})() for name in all_entity_names]
            
            calls_relations = self._create_calls_relations_from_chunks(implementation_chunks, file_path, entity_list_for_calls)
            result.relations.extend(calls_relations)
            
            # Extract file operations (open, json.load, etc.)
            if tree:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                file_op_relations = self._extract_file_operations(tree, file_path, content)
                result.relations.extend(file_op_relations)
            
            # Create file entity
            file_entity = EntityFactory.create_file_entity(file_path, 
                                                         entity_count=len(result.entities),
                                                         parsing_method="tree-sitter+jedi")
            result.entities.insert(0, file_entity)  # File first
            
            # Create containment relations
            file_name = str(file_path)
            for entity in result.entities[1:]:  # Skip file entity itself
                if entity.entity_type in [EntityType.FUNCTION, EntityType.CLASS, EntityType.VARIABLE, EntityType.IMPORT]:
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
        except (OSError, IOError) as e:
            logger = get_logger()
            logger.warning(f"Failed to read file for hashing {file_path}: {e}")
            return ""
        except Exception as e:
            logger = get_logger()
            logger.error(f"Unexpected error hashing file {file_path}: {e}")
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
                'class_definition': EntityType.CLASS,
                'assignment': EntityType.VARIABLE,
                'import_statement': EntityType.IMPORT,
                'import_from_statement': EntityType.IMPORT
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
    
    def _extract_tree_sitter_relations(self, tree: 'tree_sitter.Tree', file_path: Path) -> List['Relation']:
        """Extract relations from Tree-sitter AST (inheritance + imports for debugging)."""
        
        relations = []
        
        def traverse_node(node, depth=0):
            # Extract inheritance relations from class definitions
            if node.type == 'class_definition':
                inheritance_relations = self._extract_inheritance_relations(node, file_path)
                relations.extend(inheritance_relations)
            
            # NOTE: Import relations now handled exclusively by Jedi for better cross-module resolution
            
            # Recursively traverse children
            for child in node.children:
                traverse_node(child, depth + 1)
        
        traverse_node(tree.root_node)
        return relations
    

    def _extract_named_entity(self, node: 'tree_sitter.Node', entity_type: 'EntityType', 
                             file_path: Path) -> Optional['Entity']:
        """Extract named entity from Tree-sitter node with enhanced observations."""
        
        # Find identifier child - different structure for different node types
        entity_name = None
        
        if node.type == 'assignment':
            # For assignment: assignment -> identifier (left side)
            if node.children and node.children[0].type == 'identifier':
                entity_name = node.children[0].text.decode('utf-8')
        elif node.type in ['import_statement', 'import_from_statement']:
            # For imports: find the imported module name
            for child in node.children:
                if child.type == 'dotted_name':
                    # Get the full module path or just the identifier
                    entity_name = child.text.decode('utf-8')
                    break
                elif child.type == 'identifier':
                    entity_name = child.text.decode('utf-8')
                    break
        else:
            # For functions/classes: find identifier child
            for child in node.children:
                if child.type == 'identifier':
                    entity_name = child.text.decode('utf-8')
                    break
        
        if not entity_name:
            return None
        
        line_number = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        
        # Extract enhanced observations if extractor is available
        enhanced_observations = None
        if self._observation_extractor:
            try:
                # Read source code for observation extraction
                with open(file_path, 'r', encoding='utf-8') as f:
                    source_code = f.read()
                
                # Create Jedi script for semantic analysis
                jedi_script = None
                if self._project:
                    try:
                        jedi_script = jedi.Script(source_code, path=str(file_path), project=self._project)
                    except ImportError as e:
                        logger = get_logger()
                        logger.warning(f"Jedi import error for file {file_path}: {e}")
                    except Exception as e:
                        logger = get_logger()
                        logger.warning(f"Failed to create Jedi script for file {file_path}: {e}")
                
                # Extract observations based on entity type
                if entity_type == EntityType.FUNCTION:
                    enhanced_observations = self._observation_extractor.extract_function_observations(
                        node, source_code, jedi_script
                    )
                elif entity_type == EntityType.CLASS:
                    enhanced_observations = self._observation_extractor.extract_class_observations(
                        node, source_code, jedi_script
                    )
                
            except Exception as e:
                logger.debug(f"Failed to extract enhanced observations for {entity_name}: {e}")
        
        if entity_type == EntityType.FUNCTION:
            return EntityFactory.create_function_entity(
                name=entity_name,
                file_path=file_path,
                line_number=line_number,
                end_line=end_line,
                observations=enhanced_observations,
                source="tree-sitter"
            )
        elif entity_type == EntityType.CLASS:
            return EntityFactory.create_class_entity(
                name=entity_name,
                file_path=file_path,
                line_number=line_number,
                end_line=end_line,
                observations=enhanced_observations,
                source="tree-sitter"
            )
        elif entity_type == EntityType.VARIABLE:
            return Entity(
                name=entity_name,
                entity_type=EntityType.VARIABLE,
                observations=enhanced_observations or [
                    f"Variable: {entity_name}",
                    f"Defined in: {file_path}",
                    f"Line: {line_number}"
                ],
                file_path=file_path,
                line_number=line_number,
                end_line_number=end_line
            )
        elif entity_type == EntityType.IMPORT:
            return Entity(
                name=entity_name,
                entity_type=EntityType.IMPORT,
                observations=enhanced_observations or [
                    f"Import: {entity_name}",
                    f"In file: {file_path}",
                    f"Line: {line_number}"
                ],
                file_path=file_path,
                line_number=line_number,
                end_line_number=end_line
            )
        
        return None
    
    def _extract_inheritance_relations(self, class_node: 'tree_sitter.Node', file_path: Path) -> List['Relation']:
        """Extract inheritance relations from a class definition node."""
        relations = []
        
        # Find class name
        class_name = None
        for child in class_node.children:
            if child.type == 'identifier':
                class_name = child.text.decode('utf-8')
                break
        
        if not class_name:
            return relations
        
        # Find argument_list (contains parent classes)
        for child in class_node.children:
            if child.type == 'argument_list':
                # Extract parent classes from argument list
                for arg in child.children:
                    if arg.type == 'identifier':
                        parent_name = arg.text.decode('utf-8')
                        # Create inherits relation
                        relation = RelationFactory.create_inherits_relation(
                            subclass=class_name,
                            superclass=parent_name
                        )
                        relations.append(relation)
                    elif arg.type == 'attribute':
                        # Handle module.Class inheritance
                        parent_name = arg.text.decode('utf-8')
                        relation = RelationFactory.create_inherits_relation(
                            subclass=class_name,
                            superclass=parent_name
                        )
                        relations.append(relation)
                break
        
        return relations
    
    def _extract_import_relations(self, import_node: 'tree_sitter.Node', file_path: Path) -> List['Relation']:
        """Extract import relations from import statements."""
        relations = []
        file_name = str(file_path)
        
        # Get project root for internal import checking
        project_root = self._project.path if hasattr(self, '_project') and self._project else file_path.parent
        
        if import_node.type == 'import_statement':
            # Handle: import module1, module2
            for child in import_node.children:
                if child.type == 'dotted_name' or child.type == 'aliased_import':
                    if child.type == 'aliased_import':
                        # Get the module name before 'as'
                        for subchild in child.children:
                            if subchild.type == 'dotted_name':
                                module_name = subchild.text.decode('utf-8')
                                break
                    else:
                        module_name = child.text.decode('utf-8')
                    
                    # Only create relations for relative imports or project-internal modules
                    if module_name.startswith('.') or self._is_internal_import(module_name, file_path, project_root):
                        relation = RelationFactory.create_imports_relation(
                            importer=file_name,
                            imported=module_name,
                            import_type="module"
                        )
                        relations.append(relation)
        
        elif import_node.type == 'import_from_statement':
            # Handle: from module import name1, name2
            module_name = None
            
            # Find the module name
            for i, child in enumerate(import_node.children):
                if child.type == 'dotted_name':
                    module_name = child.text.decode('utf-8')
                    break
                elif child.type == 'relative_import':
                    # Handle relative imports like 'from . import' or 'from .. import'
                    dots = child.text.decode('utf-8')
                    # Look for module name after dots
                    if i + 1 < len(import_node.children) and import_node.children[i + 1].type == 'dotted_name':
                        module_name = dots + import_node.children[i + 1].text.decode('utf-8')
                    else:
                        module_name = dots
                    break
            
            if module_name:
                # Only create relations for relative imports or project-internal modules
                if module_name.startswith('.') or self._is_internal_import(module_name, file_path, project_root):
                    relation = RelationFactory.create_imports_relation(
                        importer=file_name,
                        imported=module_name,
                        import_type="module"
                    )
                    relations.append(relation)
        
        return relations
    
    def _is_internal_import(self, module_name: str, current_file: Path, project_root: Path) -> bool:
        """Check if an import is internal to the project by checking if the module file exists."""
        # Handle relative imports (always internal to the project)
        if module_name.startswith('.'):
            return True
            
        # Common external module prefixes to exclude
        if module_name.startswith(('_', '__')):  # Private/magic modules
            return False
            
        # Check if module file exists in project
        try:
            # Convert module name to potential file paths
            module_parts = module_name.split('.')
            base_module = module_parts[0]
            
            # Quick check: if first part doesn't exist as file/dir in project, it's external
            base_path = project_root / base_module
            base_file = project_root / f"{base_module}.py"
            
            if not base_path.exists() and not base_file.exists():
                return False
                
            # For deeper modules, verify the path exists
            if len(module_parts) > 1:
                # Check as module file
                module_path = project_root / Path(*module_parts[:-1]) / f"{module_parts[-1]}.py"
                if module_path.exists():
                    return True
                    
                # Check as package
                package_path = project_root / Path(*module_parts) / "__init__.py"
                if package_path.exists():
                    return True
            else:
                # Single module name already checked above
                return True
                
        except (OSError, ValueError) as e:
            logger = get_logger()
            logger.warning(f"Failed to determine if module is local: {e}")
            # If we can't determine, assume it's external to avoid orphans
            return False
        except Exception as e:
            logger = get_logger()
            logger.error(f"Unexpected error determining if module is local: {e}")
            # If we can't determine, assume it's external to avoid orphans
            return False
            
        return False
    
    def _analyze_with_jedi(self, file_path: Path) -> Dict[str, Any]:
        """Analyze file with Jedi for semantic information."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            script = jedi.Script(source_code, path=str(file_path), project=self._project)
            
            # Get ALL names including imports (definitions=True)
            names = script.get_names(all_scopes=True, definitions=True)
            
            analysis = {
                'functions': [],
                'classes': [],
                'imports': [],
                'variables': []
            }
            
            # Also check for import statements directly
            import_names = set()
            for line in source_code.split('\n'):
                line = line.strip()
                
                # Only process lines that actually start with import statements (not strings containing 'import')
                if line.startswith('import ') and not line.startswith(('"""', "'''", '"', "'")):
                    # Handle: import os, sys
                    parts = line[7:].split(',')
                    for part in parts:
                        module = part.strip().split(' as ')[0].strip()
                        if self._is_internal_import(module, file_path, self.project_path):
                            import_names.add(module)
                elif line.startswith('from ') and not line.startswith(('"""', "'''", '"', "'")):
                    # Handle: from pathlib import Path
                    if ' import ' in line:
                        module = line.split(' import ')[0][5:].strip()
                        if self._is_internal_import(module, file_path, self.project_path):
                            import_names.add(module)
            
            # Add direct imports first
            for module in import_names:
                analysis['imports'].append({
                    'name': module,
                    'full_name': module
                })
            
            # Process Jedi names for additional info
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
                elif name.type == 'module' and name.full_name not in import_names:
                    # Add any modules Jedi found that we didn't catch
                    analysis['imports'].append({
                        'name': name.name,
                        'full_name': name.full_name
                    })
            
            return analysis
        except Exception as e:
            logger.debug(f"Jedi analysis failed: {e}")
            return {'functions': [], 'classes': [], 'imports': [], 'variables': []}
    
    def _process_jedi_analysis(self, analysis: Dict[str, Any], 
                              file_path: Path) -> tuple[List['Entity'], List['Relation']]:
        """Process Jedi analysis results - IMPORTS ONLY (entities handled by Tree-sitter)."""
        
        entities = []  # No entities from Jedi - Tree-sitter handles all entity creation
        relations = []
        
        # Process ONLY imports from Jedi (better cross-module resolution)
        file_name = str(file_path)
        project_root = self.project_path if hasattr(self, 'project_path') else file_path.parent
        
        for imp in analysis['imports']:
            module_name = imp['name']
            # Create relations for ALL imports - let orphan cleanup handle filtering
            # This matches the previous system behavior which had 866+ imports
            relation = RelationFactory.create_imports_relation(
                importer=file_name,
                imported=module_name,
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
            
            # Debug logging
            from ..indexer_logging import get_logger
            logger = get_logger()
            logger.debug(f"🔧 Starting implementation chunk extraction for {file_path.name}")
            
            # Create Jedi script for semantic analysis
            script = jedi.Script(source_code, path=str(file_path), project=self._project)
            source_lines = source_code.split('\n')
            logger.debug(f"🔧 Jedi script created, source has {len(source_lines)} lines")
            
            # Extract function and class implementations
            functions_found = 0
            def traverse_for_implementations(node):
                nonlocal functions_found
                if node.type in ['function_definition', 'class_definition']:
                    functions_found += 1
                    logger.debug(f"🔧 Found {node.type}: attempting chunk extraction")
                    chunk = self._extract_implementation_chunk(node, source_lines, script, file_path)
                    if chunk:
                        chunks.append(chunk)
                        logger.debug(f"🔧 ✅ Created chunk for {chunk.entity_name}")
                    else:
                        logger.debug(f"🔧 ❌ Failed to create chunk for {node.type}")
                
                # Recursively traverse children
                for child in node.children:
                    traverse_for_implementations(child)
            
            traverse_for_implementations(tree.root_node)
            logger.debug(f"🔧 Traversal complete: found {functions_found} functions/classes, created {len(chunks)} chunks")
            
        except Exception as e:
            # Log implementation chunk creation failures for debugging
            from ..indexer_logging import get_logger
            logger = get_logger()
            logger.debug(f"Implementation chunk extraction failed for {file_path}: {e}")
            logger.debug(f"Exception type: {type(e).__name__}")
            logger.debug(f"Exception details: {str(e)}")
            # Continue gracefully - implementation chunks are optional
        
        return chunks
    
    def _extract_implementation_chunk(self, node: 'tree_sitter.Node', source_lines: List[str], 
                                    script: 'jedi.Script', file_path: Path) -> Optional['EntityChunk']:
        """Extract implementation chunk for function or class with semantic metadata."""
        try:
            # Debug logging
            from ..indexer_logging import get_logger
            logger = get_logger()
            logger.debug(f"🔧   Extracting chunk for {node.type} at line {node.start_point[0]}")
            
            # Get entity name
            entity_name = None
            for child in node.children:
                if child.type == 'identifier':
                    entity_name = child.text.decode('utf-8')
                    break
            
            logger.debug(f"🔧   Entity name: {entity_name}")
            
            if not entity_name:
                logger.debug(f"🔧   ❌ No entity name found")
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
                else:
                    semantic_metadata = {
                        "calls": self._extract_function_calls_from_source(implementation),
                        "imports_used": [],
                        "exceptions_handled": [],
                        "complexity": implementation.count('\n') + 1
                    }
            except Exception as e:
                # Fallback to basic analysis
                semantic_metadata = {
                    "calls": self._extract_function_calls_from_source(implementation),
                    "imports_used": [],
                    "exceptions_handled": [],
                    "complexity": implementation.count('\n') + 1  # Simple line count
                }
            
            # Create collision-resistant ID using MD5 hash suffix
            entity_type = "function" if node.type == 'function_definition' else "class"
            base_id = self._create_chunk_id(file_path, entity_name, "implementation", entity_type)
            
            # Add unique hash suffix for collision prevention
            import hashlib
            unique_content = f"{str(file_path)}::{entity_name}::{entity_type}::{start_line}::{end_line}"
            unique_hash = hashlib.md5(unique_content.encode()).hexdigest()[:8]
            collision_resistant_id = f"{base_id}::{unique_hash}"
            
            logger.debug(f"🔧   ✅ Creating EntityChunk for {entity_name} ({len(implementation)} chars)")
            
            return EntityChunk(
                id=collision_resistant_id,
                entity_name=entity_name,
                chunk_type="implementation",
                content=implementation,
                metadata={
                    "entity_type": entity_type,
                    "file_path": str(file_path),
                    "start_line": start_line + 1,
                    "end_line": end_line + 1,
                    "semantic_metadata": semantic_metadata
                }
            )
            
        except Exception as e:
            from ..indexer_logging import get_logger
            logger = get_logger()
            logger.debug(f"🔧   ❌ Individual chunk extraction failed: {e}")
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
        
        # Split source into lines to filter out function definitions
        lines = source.split('\n')
        
        # Filter out function definition lines that start with 'def '
        filtered_lines = []
        for line in lines:
            stripped = line.strip()
            # Skip lines that are function definitions
            if not stripped.startswith('def '):
                filtered_lines.append(line)
        
        # Rejoin the filtered source
        filtered_source = '\n'.join(filtered_lines)
        
        # Simple regex to find function calls (name followed by parentheses)
        call_pattern = r'(\w+)\s*\('
        calls = re.findall(call_pattern, filtered_source)
        
        # No filtering for built-ins - let entity validation handle it
        return list(set(calls))
    
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
    
    
    
    
    def _find_nodes_by_type(self, root: 'tree_sitter.Node', node_types: List[str]) -> List['tree_sitter.Node']:
        """Recursively find all nodes matching given types."""
        nodes = []
        
        def walk(node):
            if node.type in node_types:
                nodes.append(node)
            for child in node.children:
                walk(child)
                
        walk(root)
        return nodes
    
    def _extract_file_operations(self, tree: 'tree_sitter.Tree', file_path: Path, content: str) -> List['Relation']:
        """Extract file operations from Python AST using tree-sitter."""
        relations = []
        logger.debug(f"🔍 _extract_file_operations called for {file_path.name}")
        
        # Define file operation patterns to detect
        FILE_OPERATIONS = {
            # Existing patterns (unchanged)
            'open': 'file_open',
            'json.load': 'json_load',
            'json.dump': 'json_write',
            'json.loads': 'json_parse',
            'yaml.load': 'yaml_load',
            'yaml.dump': 'yaml_write',
            'pickle.load': 'pickle_load',
            'pickle.dump': 'pickle_write',
            'csv.reader': 'csv_read',
            'csv.writer': 'csv_write',
            
            # NEW PATTERNS: Pandas operations
            'pandas.read_json': 'pandas_json_read',
            'pandas.read_csv': 'pandas_csv_read',
            'pandas.read_excel': 'pandas_excel_read',
            'pd.read_json': 'pandas_json_read',  # Common alias
            'pd.read_csv': 'pandas_csv_read',
            'pd.read_excel': 'pandas_excel_read',
            
            # NEW PATTERNS: Pandas DataFrame export methods  
            '.to_json': 'pandas_json_write',
            '.to_csv': 'pandas_csv_write',
            '.to_excel': 'pandas_excel_write',
            
            # NEW PATTERNS: Pathlib operations
            '.read_text': 'path_read_text',
            '.read_bytes': 'path_read_bytes',
            '.write_text': 'path_write_text',
            '.write_bytes': 'path_write_bytes',
            
            # NEW PATTERNS: Requests operations
            'requests.get': 'requests_get',
            'requests.post': 'requests_post',
            'urllib.request.urlopen': 'urllib_open',
            
            # NEW PATTERNS: Config operations
            'configparser.read': 'config_ini_read',
            'toml.load': 'toml_read',
            'xml.etree.ElementTree.parse': 'xml_parse',
        }
        
        def extract_string_literal(node):
            """Extract string literal from node."""
            if node.type == 'string':
                text = node.text.decode('utf-8')
                # Remove quotes
                if text.startswith(('"""', "'''")):
                    return text[3:-3]
                elif text.startswith(('"', "'")):
                    return text[1:-1]
            return None
        
        def find_file_operations(node):
            """Recursively find file operations in AST."""
            if node.type == 'call':
                func_node = node.child_by_field_name('function')
                args_node = node.child_by_field_name('arguments')
                
                if func_node and args_node:
                    func_text = func_node.text.decode('utf-8')
                    
                    # Check against known file operations
                    for op_name, op_type in FILE_OPERATIONS.items():
                        if func_text == op_name or (op_name.startswith('.') and func_text.endswith(op_name)):
                            # Look for file path arguments
                            for arg in args_node.children:
                                if arg.type == 'string':
                                    file_ref = extract_string_literal(arg)
                                    if file_ref:
                                        relation = RelationFactory.create_imports_relation(
                                            importer=str(file_path),
                                            imported=file_ref,
                                            import_type=op_type
                                        )
                                        relations.append(relation)
                                        # Truncate long content for cleaner logs
                                        display_ref = file_ref[:50] + "..." if len(file_ref) > 50 else file_ref
                                        logger.debug(f"   ✅ Created {op_type} relation: {file_path} -> {display_ref}")
                                        logger.debug(f"      Relation has import_type: {relation.metadata.get('import_type', 'MISSING')}")
                                        break
                    
                    # Handle method calls on objects (e.g., df.to_json())
                    if func_node.type == 'attribute':
                        attr_value = func_node.child_by_field_name('attribute')
                        if attr_value:
                            method_name = '.' + attr_value.text.decode('utf-8')
                            if method_name in FILE_OPERATIONS:
                                # For pandas DataFrame methods like .to_json(), .to_csv()
                                for arg in args_node.children:
                                    if arg.type == 'string':
                                        file_ref = extract_string_literal(arg)
                                        if file_ref:
                                            relation = RelationFactory.create_imports_relation(
                                                importer=str(file_path),
                                                imported=file_ref,
                                                import_type=FILE_OPERATIONS[method_name]
                                            )
                                            relations.append(relation)
                                            logger.debug(f"   ✅ Created DataFrame {FILE_OPERATIONS[method_name]} relation: {file_path} -> {file_ref}")
                                            logger.debug(f"      Method: {method_name}, import_type: {relation.metadata.get('import_type', 'MISSING')}")
                                            break
                    
                    # Special handling for open() built-in
                    if func_text == 'open':
                        # Get first string argument only (filename, not mode)
                        # Only process the first string literal found to avoid mode arguments
                        first_string_found = False
                        for arg in args_node.children:
                            if arg.type == 'string' and not first_string_found:
                                file_ref = extract_string_literal(arg)
                                if file_ref:
                                    # Filter out file modes that shouldn't be relation targets
                                    file_modes = {'r', 'w', 'a', 'x', 'b', 't', 'rb', 'wb', 'ab', 'rt', 'wt', 'at', 'r+', 'w+', 'a+', 'x+'}
                                    if file_ref not in file_modes:
                                        relation = RelationFactory.create_imports_relation(
                                            importer=str(file_path),
                                            imported=file_ref,
                                            import_type='file_open'
                                        )
                                        relations.append(relation)
                                    first_string_found = True  # Ensure we only process the first string
                                    break
                    
                    # Handle Path().open() pattern
                    elif '.open' in func_text and 'Path' in func_text:
                        # Look backwards for Path constructor
                        parent = node.parent
                        while parent:
                            if parent.type == 'call':
                                parent_func = parent.child_by_field_name('function')
                                if parent_func and 'Path' in parent_func.text.decode('utf-8'):
                                    parent_args = parent.child_by_field_name('arguments')
                                    if parent_args:
                                        for arg in parent_args.children:
                                            if arg.type == 'string':
                                                file_ref = extract_string_literal(arg)
                                                if file_ref:
                                                    relation = RelationFactory.create_imports_relation(
                                                        importer=str(file_path),
                                                        imported=file_ref,
                                                        import_type='path_open'
                                                    )
                                                    relations.append(relation)
                                                    break
                                    break
                            parent = parent.parent
            
            # Handle with statements
            elif node.type == 'with_statement':
                # Look for with_item children
                for child in node.children:
                    if child.type == 'with_clause':
                        for item in child.children:
                            if item.type == 'with_item':
                                # Process calls within with_item
                                for sub in item.children:
                                    find_file_operations(sub)
            
            # Recurse through children
            for child in node.children:
                find_file_operations(child)
        
        # Start traversal from root
        if tree and tree.root_node:
            find_file_operations(tree.root_node)
        
        # Count by type for debugging
        type_counts = {}
        for rel in relations:
            imp_type = rel.metadata.get('import_type', 'unknown')
            type_counts[imp_type] = type_counts.get(imp_type, 0) + 1
        
        logger.debug(f"🔍 _extract_file_operations found {len(relations)} file operations")
        if type_counts:
            logger.debug(f"   By type: {type_counts}")
        return relations
    
    def _create_calls_relations_from_chunks(self, chunks: List['EntityChunk'], file_path: Path, entities: List['Entity'] = None) -> List['Relation']:
        """Create CALLS relations only for project-defined entities."""
        relations = []
        
        # Get entity names from current batch for validation
        entity_names = {entity.name for entity in entities} if entities else set()
        
        for chunk in chunks:
            if chunk.chunk_type == "implementation":
                # Only process function calls from semantic metadata
                calls = chunk.metadata.get('semantic_metadata', {}).get('calls', [])
                
                for called_name in calls:
                    # Only create relations to entities we actually indexed
                    if called_name in entity_names:
                        relation = Relation(
                            from_entity=chunk.entity_name,
                            to_entity=called_name,
                            relation_type=RelationType.CALLS,
                            context=f"Function call in {file_path.name}",
                            metadata={}
                        )
                        relations.append(relation)
                        logger.debug(f"Created CALLS relation: {chunk.entity_name} -> {called_name}")
                    else:
                        logger.debug(f"Skipped non-entity call: {chunk.entity_name} -> {called_name}")
        
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
            
            # Extract section content as implementation chunks (NEW)
            implementation_chunks = self._extract_section_content(file_path)
            result.implementation_chunks = implementation_chunks
            
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
        except (OSError, IOError) as e:
            logger = get_logger()
            logger.warning(f"Failed to read file for hashing {file_path}: {e}")
            return ""
        except Exception as e:
            logger = get_logger()
            logger.error(f"Unexpected error hashing file {file_path}: {e}")
            return ""
    
    def _extract_headers(self, file_path: Path) -> List['Entity']:
        """Extract headers, links, and code blocks from Markdown file."""
        
        entities = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.split('\n')
            
            # Extract headers (only h1 and h2 to reduce entity bloat)
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if line.startswith('#'):
                    # Count the level of the header
                    level = len(line) - len(line.lstrip('#'))
                    header_text = line.lstrip('#').strip()
                    
                    # Only extract major headers (h1, h2) to reduce noise
                    if header_text and level <= 2:
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
            
            # Links and code blocks filtered out to reduce relation bloat
            # Content still accessible via get_implementation for full markdown sections
        
        except Exception:
            pass  # Ignore errors, return what we could extract
        
        return entities
    
    def _extract_section_content(self, file_path: Path) -> List['EntityChunk']:
        """Extract section content between headers as implementation chunks for searchability."""
        chunks = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.split('\n')
            
            # Find all headers with their positions
            headers = []
            for line_num, line in enumerate(lines):
                line = line.strip()
                if line.startswith('#'):
                    level = len(line) - len(line.lstrip('#'))
                    header_text = line.lstrip('#').strip()
                    if header_text:
                        headers.append({
                            'text': header_text,
                            'level': level,
                            'line_num': line_num
                        })
            
            # Extract content between headers
            for i, header in enumerate(headers):
                # Determine section bounds
                start_line = header['line_num'] + 1
                end_line = headers[i + 1]['line_num'] if i + 1 < len(headers) else len(lines)
                
                # Extract section content
                section_lines = lines[start_line:end_line]
                section_content = '\n'.join(section_lines).strip()
                
                # Only create chunks for sections with meaningful content
                if section_content and len(section_content.strip()) > 5:
                    # Create collision-resistant implementation chunk ID
                    import hashlib
                    unique_content = f"{str(file_path)}::{header['text']}::documentation::{start_line}::{end_line}"
                    unique_hash = hashlib.md5(unique_content.encode()).hexdigest()[:8]
                    impl_base_id = self._create_chunk_id(file_path, header['text'], "implementation", "documentation")
                    collision_resistant_impl_id = f"{impl_base_id}::{unique_hash}"
                    
                    # Create implementation chunk using existing patterns
                    impl_chunk = EntityChunk(
                        id=collision_resistant_impl_id,
                        entity_name=header['text'],
                        chunk_type="implementation",
                        content=section_content,
                        metadata={
                            "entity_type": "documentation",
                            "file_path": str(file_path),
                            "start_line": start_line + 1,
                            "end_line": end_line,
                            "section_type": "markdown_section",
                            "content_length": len(section_content)
                        }
                    )
                    chunks.append(impl_chunk)
                    
                    # Create metadata chunk for fast discovery
                    preview = section_content[:200]
                    if len(section_content) > 200:
                        preview += "..."
                    
                    line_count = section_content.count('\n') + 1
                    word_count = len(section_content.split())
                    
                    metadata_content = f"Section: {header['text']} | Preview: {preview} | Lines: {line_count} | Words: {word_count}"
                    
                    # Create collision-resistant metadata chunk ID
                    metadata_unique_content = f"{str(file_path)}::{header['text']}::documentation::metadata::{header['line_num']}"
                    metadata_unique_hash = hashlib.md5(metadata_unique_content.encode()).hexdigest()[:8]
                    metadata_base_id = self._create_chunk_id(file_path, header['text'], "metadata", "documentation")
                    collision_resistant_metadata_id = f"{metadata_base_id}::{metadata_unique_hash}"
                    
                    # FIX: Create implementation chunk since this contains full section content
                    implementation_chunk = EntityChunk(
                        id=collision_resistant_metadata_id,
                        entity_name=header['text'],
                        chunk_type="implementation",  # FIXED: Was "metadata", now "implementation"
                        content=section_content,  # Use full section content
                        metadata={
                            "entity_type": "documentation",
                            "file_path": str(file_path),
                            "line_number": header['line_num'] + 1,
                            "section_type": "markdown_section",
                            "has_implementation": True,
                            "content_length": len(section_content),
                            "word_count": word_count,
                            "line_count": line_count
                        }
                    )
                    chunks.append(implementation_chunk)
        
        except Exception as e:
            # Graceful fallback - implementation chunks are optional
            logger.debug(f"Section content extraction failed for {file_path}: {e}")
        
        return chunks


class ParserRegistry:
    """Registry for managing multiple code parsers."""
    
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self._parsers: List[CodeParser] = []
        
        # Load project config for parser initialization
        self.project_config = self._load_project_config()
        
        self._register_default_parsers()
    
    def _load_project_config(self) -> Dict[str, Any]:
        """Load project-specific configuration."""
        try:
            from ..config.project_config import ProjectConfigManager
            config_manager = ProjectConfigManager(self.project_path)
            if config_manager.exists:
                return config_manager.load()
            return {}
        except Exception as e:
            logger.debug(f"Failed to load project config: {e}")
            return {}
    
    def _register_default_parsers(self):
        """Register default parsers."""
        from .javascript_parser import JavaScriptParser
        from .json_parser import JSONParser
        from .html_parser import HTMLParser
        from .css_parser import CSSParser
        from .yaml_parser import YAMLParser
        from .text_parser import TextParser, CSVParser, INIParser
        
        # Core language parsers
        self.register(PythonParser(self.project_path))
        self.register(JavaScriptParser())
        
        # Data format parsers with project config
        json_config = {}
        try:
            # Extract JSON config from project config (handling ProjectConfig objects)
            if hasattr(self.project_config, 'indexing') and self.project_config.indexing:
                if hasattr(self.project_config.indexing, 'parser_config') and self.project_config.indexing.parser_config:
                    json_parser_config = self.project_config.indexing.parser_config.get('json', None)
                    if json_parser_config:
                        # Convert ParserConfig object to dict
                        json_config = {
                            'content_only': getattr(json_parser_config, 'content_only', False),
                            'max_content_items': getattr(json_parser_config, 'max_content_items', 0),
                            'special_files': getattr(json_parser_config, 'special_files', ['package.json', 'tsconfig.json', 'composer.json'])
                        }
                        logger.debug(f"Extracted JSON parser config: {json_config}")
            elif isinstance(self.project_config, dict):
                # Fallback for dict-based config
                json_config = self.project_config.get('indexing', {}).get('parser_config', {}).get('json', {})
        except Exception as e:
            logger.debug(f"Failed to extract JSON parser config: {e}")
            json_config = {}
        
        self.register(JSONParser(json_config))
        self.register(YAMLParser())
        
        # Web parsers
        self.register(HTMLParser())
        self.register(CSSParser())
        
        # Documentation parsers
        self.register(MarkdownParser())
        self.register(TextParser())
        
        # Config parsers
        self.register(CSVParser())
        self.register(INIParser())
    
    def register(self, parser: CodeParser):
        """Register a new parser."""
        self._parsers.append(parser)
    
    def get_parser_for_file(self, file_path: Path) -> Optional[CodeParser]:
        """Get the appropriate parser for a file."""
        for parser in self._parsers:
            if parser.can_parse(file_path):
                return parser
        return None
    
    def parse_file(self, file_path: Path, batch_callback=None, global_entity_names=None) -> ParserResult:
        """Parse a file using the appropriate parser."""
        parser = self.get_parser_for_file(file_path)
        
        if parser is None:
            result = ParserResult(file_path=file_path, entities=[], relations=[])
            result.errors.append(f"No parser available for {file_path.suffix}")
            return result
        
        # Try to pass both batch_callback and global_entity_names if parser supports them
        try:
            return parser.parse(file_path, batch_callback=batch_callback, global_entity_names=global_entity_names)
        except TypeError:
            # Fallback for parsers that don't support new parameters
            try:
                return parser.parse(file_path, batch_callback=batch_callback)
            except TypeError:
                # Final fallback for basic parsers
                return parser.parse(file_path)
    
    def get_supported_extensions(self) -> List[str]:
        """Get all supported file extensions."""
        extensions = set()
        for parser in self._parsers:
            extensions.update(parser.get_supported_extensions())
        return sorted(list(extensions))
