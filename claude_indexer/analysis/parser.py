"""Code parsing abstractions with Tree-sitter and Jedi integration."""

import hashlib
import copy
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Import TiktokenMixin for accurate token counting
from ..embeddings.base import TiktokenMixin

try:
    import jedi
    import tree_sitter
    import tree_sitter_python as tspython

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

# Import entities at module level to avoid scope issues
try:
    from .entities import (
        Entity,
        EntityChunk,
        EntityFactory,
        EntityType,
        Relation,
        RelationFactory,
        RelationType,
    )

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
    entities: list["Entity"]
    relations: list["Relation"]

    # Progressive disclosure: implementation chunks for dual storage
    implementation_chunks: list["EntityChunk"] | None = None

    # Metadata
    parsing_time: float = 0.0
    file_hash: str = ""
    errors: list[str] | None = None
    warnings: list[str] | None = None

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
        return len(self.errors or []) == 0

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
    def get_supported_extensions(self) -> list[str]:
        """Get list of supported file extensions."""
        pass

    def _create_chunk_id(
        self,
        file_path: Path,
        entity_name: str,
        chunk_type: str,
        entity_type: str | None = None,
    ) -> str:
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
        self._parser: tree_sitter.Parser | None = None
        self._project: Any | None = None  # jedi.Project
        self._observation_extractor: Any | None = None  # ObservationExtractor

        if TREE_SITTER_AVAILABLE:
            self._initialize_parsers()

    def _initialize_parsers(self) -> None:
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
            raise RuntimeError(f"Failed to initialize Python parser: {e}") from e

    def can_parse(self, file_path: Path) -> bool:
        """Check if this is a Python file."""
        return file_path.suffix == ".py" and TREE_SITTER_AVAILABLE

    def get_supported_extensions(self) -> list[str]:
        """Get supported extensions."""
        return [".py"]

    def parse(
        self, file_path: Path, batch_callback: Any = None, global_entity_names: set[str] | None = None  # noqa: ARG002
    ) -> ParserResult:
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
                if self._has_syntax_errors(tree) and result.errors is not None:
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
            implementation_chunks = self._extract_implementation_chunks(file_path, tree)  # type: ignore[arg-type]
            result.implementation_chunks.extend(implementation_chunks)  # type: ignore[union-attr]
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
            entity_list_for_calls = [
                type("Entity", (), {"name": name})() for name in all_entity_names
            ]

            calls_relations = self._create_calls_relations_from_chunks(
                implementation_chunks, file_path, entity_list_for_calls
            )
            result.relations.extend(calls_relations)

            # Extract file operations (open, json.load, etc.)
            if tree:
                with open(file_path, encoding="utf-8") as f:
                    content = f.read()
                file_op_relations = self._extract_file_operations(
                    tree, file_path, content
                )
                result.relations.extend(file_op_relations)

            # Create file entity
            file_entity = EntityFactory.create_file_entity(
                file_path,
                entity_count=len(result.entities),
                parsing_method="tree-sitter+jedi",
            )
            result.entities.insert(0, file_entity)  # File first

            # Create containment relations
            file_name = str(file_path)
            for entity in result.entities[1:]:  # Skip file entity itself
                if entity.entity_type in [
                    EntityType.FUNCTION,
                    EntityType.CLASS,
                    EntityType.VARIABLE,
                    EntityType.IMPORT,
                ]:
                    relation = RelationFactory.create_contains_relation(
                        file_name, entity.name
                    )
                    result.relations.append(relation)

        except Exception as e:
            result.errors.append(f"Parsing failed: {e}")  # type: ignore[union-attr]
        result.parsing_time = time.time() - start_time
        return result

    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file contents."""
        try:
            with open(file_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError as e:
            logger = get_logger()
            logger.warning(f"Failed to read file for hashing {file_path}: {e}")
            return ""
        except Exception as e:
            logger = get_logger()
            logger.error(f"Unexpected error hashing file {file_path}: {e}")
            return ""

    def _parse_with_tree_sitter(self, file_path: Path) -> Optional["tree_sitter.Tree"]:
        """Parse file with Tree-sitter."""
        try:
            with open(file_path, "rb") as f:
                source_code = f.read()
            return self._parser.parse(source_code)  # type: ignore[union-attr]
        except Exception:
            return None

    def _has_syntax_errors(self, tree: "tree_sitter.Tree") -> bool:
        """Check if the parse tree contains syntax errors."""

        def check_node_for_errors(node: Any) -> bool:
            # Tree-sitter marks syntax errors with 'ERROR' node type
            if node.type == "ERROR":
                return True
            # Recursively check children
            return any(check_node_for_errors(child) for child in node.children)

        return check_node_for_errors(tree.root_node)

    def _extract_tree_sitter_entities(
        self, tree: "tree_sitter.Tree", file_path: Path
    ) -> list["Entity"]:
        """Extract entities from Tree-sitter AST."""

        entities = []

        def traverse_node(node: Any, depth: int = 0, parent_context: str | None = None) -> None:
            entity_mapping = {
                "function_definition": EntityType.FUNCTION,
                "class_definition": EntityType.CLASS,
                "assignment": EntityType.VARIABLE,
                "import_statement": EntityType.IMPORT,
                "import_from_statement": EntityType.IMPORT,
            }

            # Track current context for scope-aware filtering
            current_context = parent_context
            if node.type in ["function_definition", "class_definition"]:
                current_context = node.type
            elif node.type in [
                "with_statement",
                "try_statement",
                "except_clause",
                "finally_clause",
            ]:
                # Block contexts should also be tracked to prevent extraction of block-local variables
                current_context = "block_context"

            if node.type in entity_mapping:
                # Apply scope-aware filtering for variables
                if node.type == "assignment":
                    # Skip function-local and block-local variables
                    if current_context in ["function_definition", "block_context"]:
                        # logger.debug(
                        #     f"Skipping {current_context} variable at line {node.start_point[0] + 1}"
                        # )
                        pass
                    else:
                        # Use enhanced assignment extraction for complex patterns
                        assignment_variables = self._extract_variables_from_assignment(
                            node, file_path
                        )
                        entities.extend(assignment_variables)
                else:
                    entity = self._extract_named_entity(
                        node, entity_mapping[node.type], file_path
                    )
                    if entity:
                        entities.append(entity)

            # Enhanced variable extraction for new patterns
            elif node.type == "with_statement":
                # Skip context manager variables - these are temporary local variables
                # that shouldn't be indexed as entities
                pass

            elif node.type == "except_clause":
                # Skip exception handler variables - these are temporary local variables
                # that shouldn't be indexed as entities (e.g., 'e' from 'except ... as e:')
                pass

            elif node.type == "named_expression" and current_context != "function_definition":
                # Extract walrus operator variables
                walrus_variables = self._extract_variables_from_walrus(
                    node, file_path
                )
                entities.extend(walrus_variables)

            # Recursively traverse children with context
            for child in node.children:
                traverse_node(child, depth + 1, current_context)

        traverse_node(tree.root_node)
        return entities

    def _extract_tree_sitter_relations(
        self, tree: "tree_sitter.Tree", file_path: Path
    ) -> list["Relation"]:
        """Extract relations from Tree-sitter AST (inheritance + imports for debugging)."""

        relations = []

        def traverse_node(node: Any, depth: int = 0) -> None:
            # Extract inheritance relations from class definitions
            if node.type == "class_definition":
                inheritance_relations = self._extract_inheritance_relations(
                    node, file_path
                )
                relations.extend(inheritance_relations)

            # NOTE: Import relations now handled exclusively by Jedi for better cross-module resolution

            # Recursively traverse children
            for child in node.children:
                traverse_node(child, depth + 1)

        traverse_node(tree.root_node)
        return relations

    def _extract_named_entity(
        self, node: "tree_sitter.Node", entity_type: "EntityType", file_path: Path
    ) -> Optional["Entity"]:
        """Extract named entity from Tree-sitter node with enhanced observations."""

        # Find identifier child - different structure for different node types
        entity_name = None

        if node.type == "assignment":
            # Use proven pattern from find_attributes() for assignment node traversal
            for child in node.children:
                if child.type == "identifier":
                    # Check if this is a type-only annotation (name: str) vs real assignment (name = value)
                    node_text = node.text.decode("utf-8")  # type: ignore[union-attr]
                    if ":" in node_text and "=" not in node_text:
                        # Skip type-only annotations
                        return None
                    entity_name = child.text.decode("utf-8")  # type: ignore[union-attr]
                    break
        elif node.type in ["import_statement", "import_from_statement"]:
            # For imports: find the imported module name
            for child in node.children:
                if child.type == "dotted_name":
                    # Get the full module path or just the identifier
                    entity_name = child.text.decode("utf-8")  # type: ignore[union-attr]
                    break
                elif child.type == "identifier":
                    entity_name = child.text.decode("utf-8")  # type: ignore[union-attr]
                    break
        else:
            # For functions/classes: find identifier child
            for child in node.children:
                if child.type == "identifier":
                    entity_name = child.text.decode("utf-8")  # type: ignore[union-attr]
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
                with open(file_path, encoding="utf-8") as f:
                    source_code = f.read()

                # Create Jedi script for semantic analysis
                jedi_script = None
                if self._project:
                    try:
                        jedi_script = jedi.Script(
                            source_code, path=str(file_path), project=self._project
                        )
                    except ImportError as e:
                        logger = get_logger()
                        logger.warning(f"Jedi import error for file {file_path}: {e}")
                    except Exception as e:
                        logger = get_logger()
                        logger.warning(
                            f"Failed to create Jedi script for file {file_path}: {e}"
                        )

                # Extract observations based on entity type
                if entity_type == EntityType.FUNCTION:
                    enhanced_observations = (
                        self._observation_extractor.extract_function_observations(
                            node, source_code, jedi_script
                        )
                    )
                elif entity_type == EntityType.CLASS:
                    enhanced_observations = (
                        self._observation_extractor.extract_class_observations(
                            node, source_code, jedi_script
                        )
                    )

            except Exception as e:
                logger.debug(
                    f"Failed to extract enhanced observations for {entity_name}: {e}"
                )

        if entity_type == EntityType.FUNCTION:
            return EntityFactory.create_function_entity(
                name=entity_name,
                file_path=file_path,
                line_number=line_number,
                end_line=end_line,
                observations=enhanced_observations,
                source="tree-sitter",
            )
        elif entity_type == EntityType.CLASS:
            return EntityFactory.create_class_entity(
                name=entity_name,
                file_path=file_path,
                line_number=line_number,
                end_line=end_line,
                observations=enhanced_observations,
                source="tree-sitter",
            )
        elif entity_type == EntityType.VARIABLE:
            return Entity(
                name=entity_name,
                entity_type=EntityType.VARIABLE,
                observations=enhanced_observations
                or [
                    f"Variable: {entity_name}",
                    f"Defined in: {file_path}",
                    f"Line: {line_number}",
                ],
                file_path=file_path,
                line_number=line_number,
                end_line_number=end_line,
            )
        elif entity_type == EntityType.IMPORT:
            return Entity(
                name=entity_name,
                entity_type=EntityType.IMPORT,
                observations=enhanced_observations
                or [
                    f"Import: {entity_name}",
                    f"In file: {file_path}",
                    f"Line: {line_number}",
                ],
                file_path=file_path,
                line_number=line_number,
                end_line_number=end_line,
            )

        return None

    def _extract_variables_from_assignment(
        self, node: "tree_sitter.Node", file_path: Path
    ) -> list["Entity"]:
        """Extract multiple variables from complex assignment patterns (tuple unpacking, etc.)."""
        variables: list[Entity] = []

        def extract_identifiers_from_pattern(pattern_node: Any, line_number: int) -> list[str]:
            """Recursively extract identifiers from pattern nodes."""
            extracted_names = []

            if pattern_node.type == "identifier":
                name = pattern_node.text.decode("utf-8")
                extracted_names.append(name)
            elif pattern_node.type == "pattern_list":
                # Tuple unpacking: a, b, c = values
                for child in pattern_node.children:
                    if child.type != ",":
                        extracted_names.extend(
                            extract_identifiers_from_pattern(child, line_number)
                        )
            elif pattern_node.type == "list_pattern":
                # List unpacking: [a, b, c] = values
                for child in pattern_node.children:
                    if child.type not in ["[", "]", ","]:
                        extracted_names.extend(
                            extract_identifiers_from_pattern(child, line_number)
                        )
            elif pattern_node.type == "list_splat_pattern":
                # Starred unpacking: *rest
                for child in pattern_node.children:
                    if child.type == "identifier":
                        name = child.text.decode("utf-8")
                        extracted_names.append(name)
            elif pattern_node.type == "parenthesized_expression":
                # Nested patterns: (a, b), (c, d) = values
                for child in pattern_node.children:
                    if child.type not in ["(", ")", ","]:
                        extracted_names.extend(
                            extract_identifiers_from_pattern(child, line_number)
                        )
            elif pattern_node.type == "tuple_pattern":
                # Explicit tuple patterns
                for child in pattern_node.children:
                    if child.type not in ["(", ")", ","]:
                        extracted_names.extend(
                            extract_identifiers_from_pattern(child, line_number)
                        )

            return extracted_names

        # Check if this is a type annotation without assignment
        node_text = node.text.decode("utf-8")  # type: ignore[union-attr]
        if ":" in node_text and "=" not in node_text:
            return variables

        line_number = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        # Find the left side of assignment (target patterns)
        for child in node.children:
            if child.type in [
                "identifier",
                "pattern_list",
                "list_pattern",
                "list_splat_pattern",
                "parenthesized_expression",
                "tuple_pattern",
            ]:
                variable_names = extract_identifiers_from_pattern(child, line_number)

                for var_name in variable_names:
                    entity = Entity(
                        name=var_name,
                        entity_type=EntityType.VARIABLE,
                        observations=[
                            f"Variable: {var_name}",
                            f"Defined in: {file_path}",
                            f"Line: {line_number}",
                        ],
                        file_path=file_path,
                        line_number=line_number,
                        end_line_number=end_line,
                    )
                    variables.append(entity)
                break

        return variables

# Note: Context manager variables are now properly excluded from indexing
    # as they are temporary local variables that shouldn't be indexed as entities

# Note: Exception handler variables (e.g., 'e' from 'except ... as e:') are now
# correctly excluded from indexing as they are temporary local variables

    def _extract_variables_from_walrus(
        self, node: "tree_sitter.Node", file_path: Path
    ) -> list["Entity"]:
        """Extract variables from walrus operator (:=) expressions."""
        variables = []
        line_number = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        for child in node.children:
            if child.type == "identifier":
                var_name = child.text.decode("utf-8")  # type: ignore[union-attr]
                entity = Entity(
                    name=var_name,
                    entity_type=EntityType.VARIABLE,
                    observations=[
                        f"Variable: {var_name}",
                        f"Walrus operator assignment in: {file_path}",
                        f"Line: {line_number}",
                    ],
                    file_path=file_path,
                    line_number=line_number,
                    end_line_number=end_line,
                )
                variables.append(entity)
                break

        return variables

    def _extract_inheritance_relations(
        self, class_node: "tree_sitter.Node", file_path: Path  # noqa: ARG002
    ) -> list["Relation"]:
        """Extract inheritance relations from a class definition node."""
        relations: list[Relation] = []

        # Find class name
        class_name = None
        for child in class_node.children:
            if child.type == "identifier":
                class_name = child.text.decode("utf-8")  # type: ignore[union-attr]
                break

        if not class_name:
            return relations

        # Find argument_list (contains parent classes)
        for child in class_node.children:
            if child.type == "argument_list":
                # Extract parent classes from argument list
                for arg in child.children:
                    if arg.type == "identifier":
                        parent_name = arg.text.decode("utf-8")  # type: ignore[union-attr]
                        # Create inherits relation
                        relation = RelationFactory.create_inherits_relation(
                            subclass=class_name, superclass=parent_name
                        )
                        relations.append(relation)
                    elif arg.type == "attribute":
                        # Handle module.Class inheritance
                        parent_name = arg.text.decode("utf-8")  # type: ignore[union-attr]
                        relation = RelationFactory.create_inherits_relation(
                            subclass=class_name, superclass=parent_name
                        )
                        relations.append(relation)
                break

        return relations

    def _extract_import_relations(
        self, import_node: "tree_sitter.Node", file_path: Path
    ) -> list["Relation"]:
        """Extract import relations from import statements."""
        relations = []
        file_name = str(file_path)

        # Get project root for internal import checking
        project_root = (
            self._project.path
            if hasattr(self, "_project") and self._project
            else file_path.parent
        )

        if import_node.type == "import_statement":
            # Handle: import module1, module2
            for child in import_node.children:
                if child.type == "dotted_name" or child.type == "aliased_import":
                    if child.type == "aliased_import":
                        # Get the module name before 'as'
                        for subchild in child.children:
                            if subchild.type == "dotted_name":
                                module_name = subchild.text.decode("utf-8") if subchild.text else ""
                                break
                    else:
                        module_name = child.text.decode("utf-8") if child.text else ""
                    # Only create relations for relative imports or project-internal modules
                    if module_name.startswith(".") or self._is_internal_import(
                        module_name, file_path, project_root
                    ):
                        relation = RelationFactory.create_imports_relation(
                            importer=file_name,
                            imported=module_name,
                            import_type="module",
                        )
                        relations.append(relation)

        elif import_node.type == "import_from_statement":
            # Handle: from module import name1, name2
            module_name = None

            # Find the module name
            for i, child in enumerate(import_node.children):
                if child.type == "dotted_name":
                    module_name = child.text.decode("utf-8")  # type: ignore[union-attr]
                    break
                elif child.type == "relative_import":
                    # Handle relative imports like 'from . import' or 'from .. import'
                    dots = child.text.decode("utf-8")  # type: ignore[union-attr]
                    # Look for module name after dots
                    if (
                        i + 1 < len(import_node.children)
                        and import_node.children[i + 1].type == "dotted_name"
                    ):
                        child_text = import_node.children[i + 1].text
                        module_name = dots + (child_text.decode("utf-8") if child_text else "")
                    else:
                        module_name = dots
                    break

            if module_name and (module_name.startswith(".") or self._is_internal_import(
                module_name, file_path, project_root
            )):
                # Only create relations for relative imports or project-internal modules
                relation = RelationFactory.create_imports_relation(
                    importer=file_name, imported=module_name, import_type="module"
                )
                relations.append(relation)

        return relations

    def _is_internal_import(
        self, module_name: str, current_file: Path, project_root: Path  # noqa: ARG002
    ) -> bool:
        """Check if an import is internal to the project by checking if the module file exists."""
        # Handle relative imports (always internal to the project)
        if module_name.startswith("."):
            return True

        # Common external module prefixes to exclude
        if module_name.startswith(("_", "__")):  # Private/magic modules
            return False

        # Check if module file exists in project
        try:
            # Convert module name to potential file paths
            module_parts = module_name.split(".")
            base_module = module_parts[0]

            # Quick check: if first part doesn't exist as file/dir in project, it's external
            base_path = project_root / base_module
            base_file = project_root / f"{base_module}.py"

            if not base_path.exists() and not base_file.exists():
                return False

            # For deeper modules, verify the path exists
            if len(module_parts) > 1:
                # Check as module file
                module_path = (
                    project_root / Path(*module_parts[:-1]) / f"{module_parts[-1]}.py"
                )
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

    def _analyze_with_jedi(self, file_path: Path) -> dict[str, Any]:
        """Analyze file with Jedi for semantic information."""
        try:
            with open(file_path, encoding="utf-8") as f:
                source_code = f.read()

            script = jedi.Script(
                source_code, path=str(file_path), project=self._project
            )

            # Get ALL names including imports (definitions=True)
            names = script.get_names(all_scopes=True, definitions=True)

            analysis: dict[str, list[Any]] = {"functions": [], "classes": [], "imports": [], "variables": []}

            # Also check for import statements directly
            import_names = set()
            for line in source_code.split("\n"):
                line = line.strip()

                # Only process lines that actually start with import statements (not strings containing 'import')
                if line.startswith("import ") and not line.startswith(
                    ('"""', "'''", '"', "'")
                ):
                    # Handle: import os, sys
                    parts = line[7:].split(",")
                    for part in parts:
                        module = part.strip().split(" as ")[0].strip()
                        # Filter out file modes that aren't real imports
                        file_modes = {'r', 'w', 'a', 'x', 'b', 't', 'rb', 'wb', 'ab', 'rt', 'wt', 'at', 'r+', 'w+', 'a+', 'x+'}
                        if module not in file_modes and self._is_internal_import(
                            module, file_path, self.project_path
                        ):
                            import_names.add(module)
                elif line.startswith("from ") and not line.startswith(
                    ('"""', "'''", '"', "'")
                ):
                    # Handle: from pathlib import Path
                    if " import " in line:
                        module = line.split(" import ")[0][5:].strip()
                        # Filter out file modes that aren't real imports
                        file_modes = {'r', 'w', 'a', 'x', 'b', 't', 'rb', 'wb', 'ab', 'rt', 'wt', 'at', 'r+', 'w+', 'a+', 'x+'}
                        if module not in file_modes and self._is_internal_import(
                            module, file_path, self.project_path
                        ):
                            import_names.add(module)

            # Add direct imports first
            for module in import_names:
                analysis["imports"].append({"name": module, "full_name": module})

            # Process Jedi names for additional info
            for name in names:
                if name.type == "function":
                    analysis["functions"].append(
                        {
                            "name": name.name,
                            "line": name.line,
                            "docstring": name.docstring()
                            if hasattr(name, "docstring")
                            else None,
                            "full_name": name.full_name,
                        }
                    )
                elif name.type == "class":
                    analysis["classes"].append(
                        {
                            "name": name.name,
                            "line": name.line,
                            "docstring": name.docstring()
                            if hasattr(name, "docstring")
                            else None,
                            "full_name": name.full_name,
                        }
                    )
                elif name.type == "module" and name.full_name not in import_names:
                    # Add any modules Jedi found that we didn't catch
                    analysis["imports"].append(
                        {"name": name.name, "full_name": name.full_name}
                    )

            return analysis
        except Exception as e:
            logger.debug(f"Jedi analysis failed: {e}")
            return {"functions": [], "classes": [], "imports": [], "variables": []}

    def _process_jedi_analysis(
        self, analysis: dict[str, Any], file_path: Path
    ) -> tuple[list["Entity"], list["Relation"]]:
        """Process Jedi analysis results - IMPORTS ONLY (entities handled by Tree-sitter)."""

        entities: list[Entity] = []  # No entities from Jedi - Tree-sitter handles all entity creation
        relations = []

        # Process ONLY imports from Jedi (better cross-module resolution)
        file_name = str(file_path)
        (
            self.project_path if hasattr(self, "project_path") else file_path.parent
        )

        for imp in analysis["imports"]:
            module_name = imp["name"]
            # Create relations for ALL imports - let orphan cleanup handle filtering
            # This matches the previous system behavior which had 866+ imports
            relation = RelationFactory.create_imports_relation(
                importer=file_name, imported=module_name, import_type="module"
            )
            relations.append(relation)

        return entities, relations

    def _extract_implementation_chunks(
        self, file_path: Path, tree: "tree_sitter.Tree"
    ) -> list["EntityChunk"]:
        """Extract full implementation chunks using AST + Jedi for progressive disclosure."""
        chunks = []

        try:
            # Read source code
            with open(file_path, encoding="utf-8") as f:
                source_code = f.read()

            # Debug logging
            from ..indexer_logging import get_logger

            logger = get_logger()
            logger.debug(
                f"🔧 Starting implementation chunk extraction for {file_path.name}"
            )

            # Create Jedi script for semantic analysis
            script = jedi.Script(
                source_code, path=str(file_path), project=self._project
            )
            source_lines = source_code.split("\n")
            logger.debug(
                f"🔧 Jedi script created, source has {len(source_lines)} lines"
            )

            # Extract function and class implementations
            functions_found = 0

            def traverse_for_implementations(node: Any) -> None:
                nonlocal functions_found
                if node.type in ["function_definition", "class_definition"]:
                    functions_found += 1
                    # logger.debug(f"🔧 Found {node.type}: attempting chunk extraction")
                    chunk = self._extract_implementation_chunk(
                        node, source_lines, script, file_path
                    )
                    if chunk:
                        chunks.append(chunk)
                        # logger.debug(f"🔧 ✅ Created chunk for {chunk.entity_name}")
                    else:
                        logger.debug(f"🔧 ❌ Failed to create chunk for {node.type}")

                # Recursively traverse children
                for child in node.children:
                    traverse_for_implementations(child)

            traverse_for_implementations(tree.root_node)
            logger.debug(
                f"🔧 Traversal complete: found {functions_found} functions/classes, created {len(chunks)} chunks"
            )

        except Exception as e:
            # Log implementation chunk creation failures for debugging
            from ..indexer_logging import get_logger

            logger = get_logger()
            logger.debug(f"Implementation chunk extraction failed for {file_path}: {e}")
            logger.debug(f"Exception type: {type(e).__name__}")
            logger.debug(f"Exception details: {str(e)}")
            # Continue gracefully - implementation chunks are optional

        return chunks

    def _extract_implementation_chunk(
        self,
        node: "tree_sitter.Node",
        source_lines: list[str],
        script: "jedi.Script",
        file_path: Path,
    ) -> Optional["EntityChunk"]:
        """Extract implementation chunk for function or class with semantic metadata."""
        try:
            # Debug logging
            from ..indexer_logging import get_logger

            logger = get_logger()
            # logger.debug(f"🔧   Extracting chunk for {node.type} at line {node.start_point[0]}")

            # Get entity name
            entity_name = None
            for child in node.children:
                if child.type == "identifier":
                    entity_name = child.text.decode("utf-8")  # type: ignore[union-attr]
                    break

            # logger.debug(f"🔧   Entity name: {entity_name}")

            if not entity_name:
                logger.debug("🔧   ❌ No entity name found")
                return None

            # Extract source code lines
            start_line = node.start_point[0]
            end_line = node.end_point[0]
            implementation_lines = source_lines[start_line : end_line + 1]
            implementation = "\n".join(implementation_lines)

            # Extract semantic metadata using Jedi
            semantic_metadata = {}
            try:
                # Get Jedi definition at the entity location
                definitions = script.goto(start_line + 1, node.start_point[1])
                if definitions:
                    definition = definitions[0]
                    semantic_metadata = {
                        "inferred_types": self._get_type_hints(definition),
                        "calls": self._extract_function_calls_from_source(
                            implementation, node.type
                        ),
                        "imports_used": self._extract_imports_used_in_source(
                            implementation
                        ),
                        "exceptions_handled": self._extract_exceptions_from_source(
                            implementation
                        ),
                        "complexity": self._calculate_complexity_from_source(
                            implementation
                        ),
                    }
                else:
                    semantic_metadata = {
                        "calls": self._extract_function_calls_from_source(
                            implementation, node.type
                        ),
                        "imports_used": [],
                        "exceptions_handled": [],
                        "complexity": implementation.count("\n") + 1,
                    }
            except Exception:
                # Fallback to basic analysis
                semantic_metadata = {
                    "calls": self._extract_function_calls_from_source(implementation, node.type),
                    "imports_used": [],
                    "exceptions_handled": [],
                    "complexity": implementation.count("\n") + 1,  # Simple line count
                }

            # Create collision-resistant ID using MD5 hash suffix
            entity_type = "function" if node.type == "function_definition" else "class"
            base_id = self._create_chunk_id(
                file_path, entity_name, "implementation", entity_type
            )

            # Add unique hash suffix for collision prevention
            import hashlib

            unique_content = f"{str(file_path)}::{entity_name}::{entity_type}::{start_line}::{end_line}"
            unique_hash = hashlib.md5(unique_content.encode()).hexdigest()[:8]
            collision_resistant_id = f"{base_id}::{unique_hash}"

            # logger.debug(
            #     f"🔧   ✅ Creating EntityChunk for {entity_name} ({len(implementation)} chars)"
            # )

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
                    "semantic_metadata": semantic_metadata,
                },
            )

        except Exception as e:
            from ..indexer_logging import get_logger

            logger = get_logger()
            logger.debug(f"🔧   ❌ Individual chunk extraction failed: {e}")
            return None

    def _get_type_hints(self, definition: Any) -> dict[str, str]:
        """Extract type hints from Jedi definition."""
        try:
            type_hints = {}
            if hasattr(definition, "signature"):
                sig = definition.signature
                if sig:
                    type_hints["signature"] = str(sig)
            return type_hints
        except Exception:
            return {}

    def _extract_function_calls_from_source(self, source: str, node_type: str = "function") -> list[str]:
        """Extract function calls from source code using regex."""

        # Split source into lines to filter out function definitions
        lines = source.split("\n")

        # For classes: filter out method bodies to avoid double-counting
        if node_type == "class_definition":
            filtered_lines = []
            in_method = False
            method_indent = 0

            for line in lines:
                stripped = line.strip()
                current_indent = len(line) - len(line.lstrip())

                # Detect method start
                if stripped.startswith("def "):
                    in_method = True
                    method_indent = current_indent
                    continue  # Skip method definition line

                # Skip method body lines (higher indent than method definition)
                if in_method and current_indent > method_indent:
                    continue

                # End of method body (back to class level or less)
                if in_method and current_indent <= method_indent and stripped:
                    in_method = False

                # Keep class-level lines
                if not in_method:
                    filtered_lines.append(line)
        else:
            # For functions: filter out function definition lines that start with 'def '
            filtered_lines = []
            for line in lines:
                stripped = line.strip()
                # Skip lines that are function definitions
                if not stripped.startswith("def "):
                    filtered_lines.append(line)

        # Rejoin the filtered source
        filtered_source = "\n".join(filtered_lines)

        # Simple regex to find function calls (name followed by parentheses)
        call_pattern = r"(\w+)\s*\("
        calls = re.findall(call_pattern, filtered_source)

        # No filtering for built-ins - let entity validation handle it
        return list(set(calls))

    def _extract_imports_used_in_source(self, source: str) -> list[str]:
        """Extract imports referenced in the source code."""
        # Find module.function or module.class patterns
        module_pattern = r"(\w+)\.(\w+)"
        matches = re.findall(module_pattern, source)
        return list({f"{module}.{attr}" for module, attr in matches})

    def _extract_exceptions_from_source(self, source: str) -> list[str]:
        """Extract exception types from source code."""
        # Find except SomeException patterns
        except_pattern = r"except\s+(\w+)"
        exceptions = re.findall(except_pattern, source)
        return list(set(exceptions))

    def _calculate_complexity_from_source(self, source: str) -> int:
        """Calculate complexity based on control flow statements."""
        complexity_keywords = ["if", "elif", "for", "while", "try", "except", "with"]
        complexity = 1  # Base complexity
        for keyword in complexity_keywords:
            complexity += source.count(f" {keyword} ") + source.count(f"\n{keyword} ")
        return complexity

    def _find_nodes_by_type(
        self, root: "tree_sitter.Node", node_types: list[str]
    ) -> list["tree_sitter.Node"]:
        """Recursively find all nodes matching given types."""
        nodes = []

        def walk(node: Any) -> None:
            if node.type in node_types:
                nodes.append(node)
            for child in node.children:
                walk(child)

        walk(root)
        return nodes

    def _extract_file_operations(
        self, tree: "tree_sitter.Tree", file_path: Path, content: str  # noqa: ARG002
    ) -> list["Relation"]:
        """Extract file operations from Python AST using tree-sitter."""
        relations = []
        logger.debug(f"🔍 _extract_file_operations called for {file_path.name}")

        # Define file operation patterns to detect
        FILE_OPERATIONS = {
            # Existing patterns (unchanged)
            "open": "file_open",
            "json.load": "json_load",
            "json.dump": "json_write",
            "json.loads": "json_parse",
            "yaml.load": "yaml_load",
            "yaml.dump": "yaml_write",
            "pickle.load": "pickle_load",
            "pickle.dump": "pickle_write",
            "csv.reader": "csv_read",
            "csv.writer": "csv_write",
            # NEW PATTERNS: Pandas operations
            "pandas.read_json": "pandas_json_read",
            "pandas.read_csv": "pandas_csv_read",
            "pandas.read_excel": "pandas_excel_read",
            "pd.read_json": "pandas_json_read",  # Common alias
            "pd.read_csv": "pandas_csv_read",
            "pd.read_excel": "pandas_excel_read",
            # NEW PATTERNS: Pandas DataFrame export methods
            ".to_json": "pandas_json_write",
            ".to_csv": "pandas_csv_write",
            ".to_excel": "pandas_excel_write",
            # NEW PATTERNS: Pathlib operations
            ".read_text": "path_read_text",
            ".read_bytes": "path_read_bytes",
            ".write_text": "path_write_text",
            ".write_bytes": "path_write_bytes",
            # NEW PATTERNS: Requests operations
            "requests.get": "requests_get",
            "requests.post": "requests_post",
            "urllib.request.urlopen": "urllib_open",
            # NEW PATTERNS: Config operations
            "configparser.read": "config_ini_read",
            "toml.load": "toml_read",
            "xml.etree.ElementTree.parse": "xml_parse",
        }

        def extract_string_literal(node: Any) -> str | None:
            """Extract string literal from node."""
            if node.type == "string":
                text = node.text.decode("utf-8") if node.text else ""
                # Remove quotes
                if text.startswith(('"""', "'''")):
                    return str(text[3:-3])
                elif text.startswith(('"', "'")):
                    return str(text[1:-1])
            return None

        def find_file_operations(node: Any) -> None:
            """Recursively find file operations in AST."""
            if node.type == "call":
                func_node = node.child_by_field_name("function")
                args_node = node.child_by_field_name("arguments")

                if func_node and args_node:
                    func_text = func_node.text.decode("utf-8")
                    # Check against known file operations
                    for op_name, op_type in FILE_OPERATIONS.items():
                        if func_text == op_name or (
                            op_name.startswith(".") and func_text.endswith(op_name)
                        ):
                            # Look for file path arguments
                            for arg in args_node.children:
                                if arg.type == "string":
                                    file_ref = extract_string_literal(arg)
                                    if file_ref:
                                        # Filter out file modes that shouldn't be relation targets
                                        file_modes = {
                                            "r", "w", "a", "x", "b", "t",
                                            "rb", "wb", "ab", "rt", "wt", "at",
                                            "r+", "w+", "a+", "x+",
                                        }
                                        if file_ref not in file_modes:
                                            relation = (
                                                RelationFactory.create_imports_relation(
                                                    importer=str(file_path),
                                                    imported=file_ref,
                                                    import_type=op_type,
                                                )
                                            )
                                            relations.append(relation)
                                            # Truncate long content for cleaner logs
                                            display_ref = (
                                                file_ref[:50] + "..."
                                                if len(file_ref) > 50
                                                else file_ref
                                            )
                                            logger.debug(
                                                f"   ✅ Created {op_type} relation: {file_path} -> {display_ref}"
                                            )
                                            # logger.debug(f"      Relation has import_type: {relation.metadata.get('import_type', 'MISSING')}")
                                        break

                    # Handle method calls on objects (e.g., df.to_json())
                    if func_node.type == "attribute":
                        attr_value = func_node.child_by_field_name("attribute")
                        if attr_value:
                            method_name = "." + (attr_value.text.decode("utf-8") if attr_value.text else "")
                            if method_name in FILE_OPERATIONS:
                                # For pandas DataFrame methods like .to_json(), .to_csv()
                                for arg in args_node.children:
                                    if arg.type == "string":
                                        file_ref = extract_string_literal(arg)
                                        if file_ref:
                                            # Filter out file modes that shouldn't be relation targets
                                            file_modes = {
                                                "r", "w", "a", "x", "b", "t",
                                                "rb", "wb", "ab", "rt", "wt", "at",
                                                "r+", "w+", "a+", "x+",
                                            }
                                            if file_ref not in file_modes:
                                                relation = (
                                                    RelationFactory.create_imports_relation(
                                                        importer=str(file_path),
                                                        imported=file_ref,
                                                        import_type=FILE_OPERATIONS[
                                                            method_name
                                                        ],
                                                    )
                                                )
                                                relations.append(relation)
                                                logger.debug(
                                                    f"   ✅ Created DataFrame {FILE_OPERATIONS[method_name]} relation: {file_path} -> {file_ref}"
                                                )
                                                # logger.debug(f"      Method: {method_name}, import_type: {relation.metadata.get('import_type', 'MISSING')}")
                                                break

                    # Special handling for open() built-in
                    if func_text == "open":
                        # Get first string argument only (filename, not mode)
                        # Only process the first string literal found to avoid mode arguments
                        first_string_found = False
                        for arg in args_node.children:
                            if arg.type == "string" and not first_string_found:
                                file_ref = extract_string_literal(arg)
                                if file_ref:
                                    # Filter out file modes that shouldn't be relation targets
                                    file_modes = {
                                        "r",
                                        "w",
                                        "a",
                                        "x",
                                        "b",
                                        "t",
                                        "rb",
                                        "wb",
                                        "ab",
                                        "rt",
                                        "wt",
                                        "at",
                                        "r+",
                                        "w+",
                                        "a+",
                                        "x+",
                                    }
                                    if file_ref not in file_modes:
                                        relation = (
                                            RelationFactory.create_imports_relation(
                                                importer=str(file_path),
                                                imported=file_ref,
                                                import_type="file_open",
                                            )
                                        )
                                        relations.append(relation)
                                    first_string_found = (
                                        True  # Ensure we only process the first string
                                    )
                                    break

                    # Handle Path().open() pattern
                    elif ".open" in func_text and "Path" in func_text:
                        # Look backwards for Path constructor
                        parent = node.parent
                        while parent:
                            if parent.type == "call":
                                parent_func = parent.child_by_field_name("function")
                                if parent_func and "Path" in parent_func.text.decode(
                                    "utf-8"
                                ):
                                    parent_args = parent.child_by_field_name(
                                        "arguments"
                                    )
                                    if parent_args:
                                        for arg in parent_args.children:
                                            if arg.type == "string":
                                                file_ref = extract_string_literal(arg)
                                                if file_ref:
                                                    # Filter out file modes that shouldn't be relation targets
                                                    file_modes = {
                                                        "r", "w", "a", "x", "b", "t",
                                                        "rb", "wb", "ab", "rt", "wt", "at",
                                                        "r+", "w+", "a+", "x+",
                                                    }
                                                    if file_ref not in file_modes:
                                                        relation = RelationFactory.create_imports_relation(
                                                            importer=str(file_path),
                                                            imported=file_ref,
                                                            import_type="path_open",
                                                        )
                                                        relations.append(relation)
                                                        break
                                    break
                            parent = parent.parent

            # Handle with statements
            elif node.type == "with_statement":
                # Look for with_item children
                for child in node.children:
                    if child.type == "with_clause":
                        for item in child.children:
                            if item.type == "with_item":
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
        type_counts: dict[str, int] = {}
        for rel in relations:
            imp_type = rel.metadata.get("import_type", "unknown")
            type_counts[imp_type] = type_counts.get(imp_type, 0) + 1

        # logger.debug(
        #     f"🔍 _extract_file_operations found {len(relations)} file operations"
        # )
        if type_counts:
            logger.debug(f"   By type: {type_counts}")
        return relations

    def _create_calls_relations_from_chunks(
        self,
        chunks: list["EntityChunk"],
        file_path: Path,
        entities: list["Entity"] | None = None,
    ) -> list["Relation"]:
        """Create CALLS relations only for project-defined entities."""
        relations = []

        # Get entity names from current batch for validation
        entity_names = {entity.name for entity in entities} if entities else set()

        # 🐛 DEBUG: Track chunks and their call metadata
        # logger.debug(f"🔍 PHANTOM DEBUG: Processing {len(chunks)} chunks for relations")
        # logger.debug(f"🔍 PHANTOM DEBUG: Current entity names: {sorted(entity_names)}")

        for chunk in chunks:
            if chunk.chunk_type == "implementation":
                # Only process function calls from semantic metadata
                calls = chunk.metadata.get("semantic_metadata", {}).get("calls", [])

                # 🐛 DEBUG: Log chunk details
                if calls:
                    # logger.debug(f"🔍 PHANTOM DEBUG: Chunk {chunk.entity_name} has calls: {calls}")
                    pass

                for called_name in calls:
                    # Only create relations to entities we actually indexed
                    if called_name in entity_names:
                        relation = Relation(
                            from_entity=chunk.entity_name,
                            to_entity=called_name,
                            relation_type=RelationType.CALLS,
                            context=f"Function call in {file_path.name}",
                            metadata={},
                        )
                        relations.append(relation)
                        # logger.debug(
                        #     f"🔍 PHANTOM DEBUG: Created CALLS relation: {chunk.entity_name} -> {called_name} [CURRENT ENTITY]"
                        # )
                        pass
                    else:
                        # 🐛 DEBUG: Log phantom call attempts
                        # logger.debug(f"🔍 PHANTOM DEBUG: Skipped call {chunk.entity_name} -> {called_name} [NOT IN CURRENT ENTITIES]")
                        pass

        # logger.debug(f"🔍 PHANTOM DEBUG: Created {len(relations)} total relations from chunks")
        return relations


class MarkdownParser(CodeParser, TiktokenMixin):
    """Parser for Markdown documentation files with intelligent chunking."""
    
    # Hardcoded optimal values for chunking
    TARGET_CHUNK_TOKENS = 800  # Target tokens per chunk
    MAX_CHUNK_TOKENS = 1000   # Hard limit
    OVERLAP_PERCENT = 0.125   # 12.5% overlap
    MIN_CHUNK_TOKENS = 100    # Minimum viable chunk size
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def can_parse(self, file_path: Path) -> bool:
        """Check if this is a Markdown file."""
        return file_path.suffix.lower() in [".md", ".markdown"]

    def get_supported_extensions(self) -> list[str]:
        """Get supported extensions."""
        return [".md", ".markdown"]

    def parse(self, file_path: Path) -> ParserResult:
        """Parse Markdown file to extract documentation entities."""
        import time

        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])

        try:
            result.file_hash = self._get_file_hash(file_path)

            # Create file entity
            file_entity = EntityFactory.create_file_entity(
                file_path, content_type="documentation", parsing_method="markdown"
            )
            result.entities.append(file_entity)

            # Extract headers and structure
            headers = self._extract_headers(file_path)
            result.entities.extend(headers)

            # Extract section content as implementation chunks  
            implementation_chunks = self._extract_section_content(file_path)
            result.implementation_chunks = implementation_chunks

            # Create containment relations
            file_name = str(file_path)
            for header in headers:
                relation = RelationFactory.create_contains_relation(
                    file_name, header.name
                )
                result.relations.append(relation)

        except Exception as e:
            result.errors.append(f"Markdown parsing failed: {e}")  # type: ignore[union-attr]

        result.parsing_time = time.time() - start_time
        return result

    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file contents."""
        try:
            with open(file_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError as e:
            logger = get_logger()
            logger.warning(f"Failed to read file for hashing {file_path}: {e}")
            return ""
        except Exception as e:
            logger = get_logger()
            logger.error(f"Unexpected error hashing file {file_path}: {e}")
            return ""

    def _extract_headers(self, file_path: Path) -> list["Entity"]:
        """Extract headers, links, and code blocks from Markdown file."""

        entities = []

        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
                lines = content.split("\n")

            # Extract headers (only h1 and h2 to reduce entity bloat)
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if line.startswith("#"):
                    # Count the level of the header
                    level = len(line) - len(line.lstrip("#"))
                    header_text = line.lstrip("#").strip()

                    # Only extract major headers (h1, h2) to reduce noise
                    if header_text and level <= 2:
                        entity = Entity(
                            name=header_text,
                            entity_type=EntityType.DOCUMENTATION,
                            observations=[
                                f"Header level {level}: {header_text}",
                                f"Line {line_num} in {file_path.name}",
                            ],
                            file_path=file_path,
                            line_number=line_num,
                            metadata={"header_level": level, "type": "header"},
                        )
                        entities.append(entity)

            # Links and code blocks filtered out to reduce relation bloat
            # Content still accessible via get_implementation for full markdown sections

        except Exception:
            pass  # Ignore errors, return what we could extract

        return entities

    def _extract_section_content(self, file_path: Path) -> list["EntityChunk"]:
        """Extract section content with intelligent chunking and semantic boundaries."""
        chunks = []

        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
                lines = content.split("\n")

            # Parse markdown into structured sections
            sections = self._parse_markdown_sections(content, file_path)
            
            # Apply intelligent chunking algorithm
            chunk_groups = self._create_intelligent_chunks(sections)
            
            # Convert chunk groups to EntityChunks
            for chunk_group in chunk_groups:
                impl_chunk, metadata_chunk = self._create_entity_chunks(chunk_group, file_path, content)
                chunks.extend([impl_chunk, metadata_chunk])

        except Exception as e:
            # Graceful fallback - implementation chunks are optional
            from ..indexer_logging import get_logger
            logger = get_logger()
            logger.debug(f"Section content extraction failed for {file_path}: {e}")

        return chunks
    
    def _parse_markdown_sections(self, content: str, file_path: Path) -> list[dict]:
        """Parse markdown into hierarchical sections with token counts."""
        sections = []
        lines = content.split('\n')
        header_stack = []  # Track parent hierarchy
        
        # Find all headers
        headers = []
        for line_num, line in enumerate(lines):
            line = line.strip()
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                header_text = line.lstrip("#").strip()
                if header_text:  # Accept all header levels now
                    headers.append({
                        "text": header_text,
                        "level": level,
                        "line_num": line_num
                    })
        
        # Forward-merge empty headers into next content sections
        for i, header in enumerate(headers):
            level = header["level"]
            
            # Find section boundaries
            start_line = header["line_num"] + 1
            end_line = len(lines)
            
            # Find next header - stop at same level, higher level, or immediate child
            for j in range(i + 1, len(headers)):
                next_header = headers[j]
                if level == 1:
                    # H1 sections end at next H1 or H2
                    if next_header["level"] <= 2:
                        end_line = next_header["line_num"]
                        break
                else:
                    # Other levels: end at same level, higher level, or immediate child (level+1)
                    if next_header["level"] <= level or next_header["level"] == level + 1:
                        end_line = next_header["line_num"]
                        break
            
            # Extract section content
            section_lines = lines[start_line:end_line]  
            section_content = "\n".join(section_lines).strip()
            
            # If empty, merge into next section with content
            if not section_content or len(section_content.strip()) <= 5:
                # Find next section with content to merge into
                for k in range(i + 1, len(headers)):
                    next_header = headers[k]
                    
                    # Get next section's content
                    next_start = next_header["line_num"] + 1
                    next_end = len(lines)
                    for m in range(k + 1, len(headers)):
                        if headers[m]["level"] <= next_header["level"]:
                            next_end = headers[m]["line_num"]
                            break
                    
                    next_content = "\n".join(lines[next_start:next_end]).strip()
                    
                    # If next section has content, merge this empty header into it
                    if next_content and len(next_content.strip()) > 5:
                        headers[k]["merged_headers"] = headers[k].get("merged_headers", []) + [header["text"]]
                        break
            else:
                # Section has content - check if any empty headers should be merged into it
                merged_header_texts = header.get("merged_headers", [])
                
                # Update hierarchy stack with merged headers included
                header_stack = header_stack[:level-1] + [header["text"]]
                
                # Create combined content if we have merged headers
                display_header = header["text"]
                if merged_header_texts:
                    # Include merged header names in display header only (don't duplicate in content)
                    display_header = f"{header['text']} (+{len(merged_header_texts)} more)"
                
                # Calculate tokens for this section (header + content)
                full_section = f"{display_header}\n\n{section_content}"
                tokens = self._estimate_tokens_with_tiktoken(full_section)
                
                sections.append({
                    "header": display_header,
                    "level": level,
                    "content": section_content,
                    "tokens": tokens,
                    "line_start": start_line,
                    "line_end": end_line,
                    "parent_path": header_stack.copy(),
                    "original_header_line": header["line_num"]
                })
        
        return sections
    
    def _create_intelligent_chunks(self, sections: list[dict]) -> list[list[dict]]:
        """Group sections intelligently into token-optimized chunks."""
        if not sections:
            return []
            
        # First pass: handle oversized sections
        processed_sections = []
        for section in sections:
            if section["tokens"] > self.MAX_CHUNK_TOKENS:
                # Split large sections
                split_sections = self._split_large_section(section)
                processed_sections.extend(split_sections)
            else:
                processed_sections.append(section)
        
        # Second pass: aggressive grouping for optimal token utilization
        chunk_groups = []
        current_group = []
        current_tokens = 0
        current_parent = None
        
        # Smart grouping: prefer individual sections when they're substantial enough
        MIN_SECTION_TOKENS = 100  # Increased from 20 to encourage more grouping
        AGGRESSIVE_TOKEN_BUDGET = int(self.MAX_CHUNK_TOKENS * 0.85)  # 850 tokens
        MAX_SECTIONS_PER_CHUNK = 10  # Increased from 8
        
        for section in processed_sections:
            parent_key = tuple(section["parent_path"][:-1]) if len(section["parent_path"]) > 1 else ()
            
            # Smart grouping logic - respect section boundaries for substantial content
            is_substantial = section["tokens"] >= MIN_SECTION_TOKENS
            would_exceed_budget = current_tokens + section["tokens"] > AGGRESSIVE_TOKEN_BUDGET
            
            can_group = (
                not would_exceed_budget and
                len(current_group) < MAX_SECTIONS_PER_CHUNK and
                not (is_substantial and current_group) and  # Don't group substantial sections with others
                (
                    current_parent == parent_key or  # Same parent (preferred)
                    (len(section["parent_path"]) <= 3 and len(current_group) < 6) or  # Allow deeper nesting
                    len(current_group) < 3  # Always allow first few sections to group
                )
            )
            
            if can_group and current_group:
                current_group.append(section)
                current_tokens += section["tokens"]
            else:
                # Start new group
                if current_group:
                    chunk_groups.append(current_group)
                current_group = [section]
                current_tokens = section["tokens"]
                current_parent = parent_key
        
        # Add final group
        if current_group:
            chunk_groups.append(current_group)
        
        # Third pass: redistribute undersized chunks  
        chunk_groups = self._redistribute_undersized_chunks(chunk_groups)
        
        # Fourth pass: add overlap context
        return self._add_overlap_context(chunk_groups)
    
    def _split_large_section(self, section: dict) -> list[dict]:
        """Split sections that exceed token limit at semantic boundaries."""
        content = section["content"]
        
        # Try splitting by semantic boundaries
        boundaries = [
            r'\n\n\n+',           # Multiple blank lines
            r'\n\n(?=[A-Z])',     # Paragraph breaks  
            r'\n(?=[-*+] |\d+\.)', # Before list items
            r'(?<=\.)\s+(?=[A-Z])', # After sentences
            r'\n(?=```)',         # Before code blocks
            r'(?<=```)\n',        # After code blocks
        ]
        
        parts = [content]
        for pattern in boundaries:
            new_parts = []
            for part in parts:
                part_tokens = self._estimate_tokens_with_tiktoken(f"{section['header']}\n\n{part}")
                if part_tokens > self.MAX_CHUNK_TOKENS:
                    splits = re.split(pattern, part)
                    new_parts.extend([s.strip() for s in splits if s.strip()])
                else:
                    new_parts.append(part)
            parts = new_parts
        
        # Create section objects for each part
        result_sections = []
        for i, part in enumerate(parts):
            if not part.strip():
                continue
                
            part_tokens = self._estimate_tokens_with_tiktoken(f"{section['header']}\n\n{part}")
            if part_tokens < self.MIN_CHUNK_TOKENS and i > 0:
                # Merge small parts with previous
                if result_sections:
                    result_sections[-1]["content"] += f"\n\n{part}"
                    result_sections[-1]["tokens"] = self._estimate_tokens_with_tiktoken(
                        f"{section['header']}\n\n{result_sections[-1]['content']}"
                    )
                continue
            
            chunk_name = section["header"]
            if len(parts) > 1:
                chunk_name += f" (Part {i + 1})"
            
            result_sections.append({
                **section,
                "header": chunk_name,
                "content": part,
                "tokens": part_tokens
            })
        
        return result_sections
    
    def _redistribute_undersized_chunks(self, chunk_groups: list[list[dict]]) -> list[list[dict]]:
        """Redistribute sections from undersized chunks to meet minimum token requirements."""
        if len(chunk_groups) <= 1:
            return chunk_groups
        
        MIN_CHUNK_TOKENS = 600  # Target minimum
        redistributed_groups = []
        
        i = 0
        while i < len(chunk_groups):
            current_group = chunk_groups[i]
            current_tokens = sum(section["tokens"] for section in current_group)
            
            # If chunk is undersized, try to merge with adjacent chunks
            if current_tokens < MIN_CHUNK_TOKENS:
                merged = False
                
                # First try merging with next chunk (if available)
                if i < len(chunk_groups) - 1:
                    next_group = chunk_groups[i + 1]
                    next_tokens = sum(section["tokens"] for section in next_group)
                    combined_tokens = current_tokens + next_tokens
                    
                    if (combined_tokens <= self.MAX_CHUNK_TOKENS and 
                        len(current_group) + len(next_group) <= 8):
                        
                        # Merge current undersized chunk with next
                        merged_group = current_group + next_group
                        redistributed_groups.append(merged_group)
                        i += 2  # Skip the next group since we merged it
                        merged = True
                
                # If no next merge, try merging with previous group
                if not merged and len(redistributed_groups) > 0:
                    prev_group = redistributed_groups[-1]
                    prev_tokens = sum(section["tokens"] for section in prev_group)
                    combined_tokens = prev_tokens + current_tokens
                    
                    if (combined_tokens <= self.MAX_CHUNK_TOKENS and 
                        len(prev_group) + len(current_group) <= 8):
                        
                        # Merge with previous group
                        redistributed_groups[-1] = prev_group + current_group
                        i += 1
                        merged = True
                
                if merged:
                    continue
            
            # Default: add current group as-is
            redistributed_groups.append(current_group)
            i += 1
        
        return redistributed_groups
    
    def _add_overlap_context(self, chunk_groups: list[list[dict]]) -> list[list[dict]]:
        """Add 12.5% overlap between adjacent chunks for context."""
        if len(chunk_groups) <= 1:
            return chunk_groups
        
        # Add overlap by including trailing content from previous chunk
        # and leading content preview in next chunk
        enhanced_groups = []
        
        for i, group in enumerate(chunk_groups):
            enhanced_group = copy.deepcopy(group)
            
            # Add trailing context from previous chunk
            if i > 0:
                prev_group = enhanced_groups[i-1]
                # Get last section content from previous chunk
                if prev_group:
                    last_section = prev_group[-1]
                    clean_content = self._extract_clean_content(last_section["content"])
                    overlap_content = clean_content[-200:] if len(clean_content) > 200 else clean_content
                    
                    # Append overlap context after main content to preserve header-content relationship
                    if enhanced_group:
                        enhanced_group[0] = {
                            **enhanced_group[0],
                            "content": f"{enhanced_group[0]['content']}\n\n[Previous context: ...{overlap_content}]",
                            "has_overlap": True
                        }
            
            enhanced_groups.append(enhanced_group)
        
        return enhanced_groups
    
    def _extract_clean_content(self, content: str) -> str:
        """Extract clean content by removing existing overlap markers to prevent cascading."""
        if not content:
            return content
    
        # If no overlap markers, return as-is
        if '[...' not in content:
            return content
    
        original_content = content
    
        # Split by paragraphs and find the first clean section
        parts = content.split('\n\n')
        for part in parts:
            part = part.strip()
            if part and not part.startswith('[...'):
                return part
    
        # Fallback: more conservative regex cleanup
        # Only remove overlap markers that are clearly at the beginning of content
        lines = content.split('\n')
        clean_lines = []
    
        for line in lines:
            line = line.strip()
            # Skip lines that are clearly overlap markers
            if line.startswith('[...') and line.endswith(']'):
                continue
            # Keep everything else
            clean_lines.append(line)
    
        cleaned = '\n'.join(clean_lines).strip()
    
        # CRITICAL: If cleaning resulted in empty content, return original
        # This prevents data loss when entire content looks like overlap
        if not cleaned and original_content:
            return original_content
    
        return cleaned if cleaned else original_content

    def _create_entity_chunks(self, section_group: list[dict], file_path: Path, source_content: str) -> tuple["EntityChunk", "EntityChunk"]:
        """Create implementation and metadata chunks from section group."""
        import hashlib
        
        # Combine all sections in group
        combined_content = []
        combined_headers = []
        total_tokens = 0
        start_line = float('inf')
        end_line = 0
        first_header_line = None
        
        for section in section_group:
            combined_headers.append(section["header"])
            # For grouped entities, include headers to match source exactly
            if len(section_group) > 1:
                # Add header with appropriate level markers
                header_level = section.get("level", 1)
                header_prefix = "#" * header_level
                combined_content.append(f"{header_prefix} {section['header']}\n\n{section['content']}")
            else:
                combined_content.append(section['content'])
            total_tokens += section["tokens"]
            # For grouped entities that include headers, use header line for start_line
            if len(section_group) > 1:
                # Use header line for first section, content line for others
                if first_header_line is None:
                    header_line = section.get("original_header_line")
                    if header_line is not None:  # Check for None specifically since 0 is valid
                        # Convert 0-based to 1-based line number
                        start_line = min(start_line, header_line + 1)
                        first_header_line = header_line + 1
                    else:
                        start_line = min(start_line, section["line_start"])
                else:
                    start_line = min(start_line, section["line_start"])
            else:
                start_line = min(start_line, section["line_start"])
            end_line = max(end_line, section["line_end"])
        
        # Create chunk name
        if len(section_group) == 1:
            chunk_name = section_group[0]["header"]
        else:
            chunk_name = f"{section_group[0]['header']} (+{len(section_group)-1} more)"
        
        full_content = "\n\n".join(combined_content)
        
        # For grouped entities that span to the end of file, preserve trailing newline
        if len(section_group) > 1 and end_line >= len(source_content.split('\n')):
            # Check if source file ends with newline and preserve it
            if source_content.endswith('\n') and not full_content.endswith('\n'):
                full_content += '\n'
        
        # Create implementation chunk
        unique_content = f"{str(file_path)}::{chunk_name}::documentation::{start_line}::{end_line}"
        unique_hash = hashlib.md5(unique_content.encode()).hexdigest()[:8]
        impl_base_id = self._create_chunk_id(file_path, chunk_name, "implementation", "documentation")
        impl_id = f"{impl_base_id}::{unique_hash}"
        
        impl_chunk = EntityChunk(
            id=impl_id,
            entity_name=chunk_name,
            chunk_type="implementation",
            content=full_content,
            metadata={
                "entity_type": "documentation",
                "file_path": str(file_path),
                "start_line": int(start_line),
                "end_line": int(end_line),
                "section_type": "markdown_section",
                "content_length": len(full_content),
                "token_count": total_tokens,
                "section_count": len(section_group),
                "headers": combined_headers
            },
        )
        
        # Create metadata chunk for fast discovery
        preview = full_content[:300] + "..." if len(full_content) > 300 else full_content
        line_count = full_content.count("\n") + 1
        word_count = len(full_content.split())
        
        metadata_content = f"Sections: {', '.join(combined_headers)} | Tokens: {total_tokens} | Preview: {preview} | Lines: {line_count} | Words: {word_count}"
        
        metadata_unique_content = f"{str(file_path)}::{chunk_name}::documentation::metadata::{start_line}"
        metadata_hash = hashlib.md5(metadata_unique_content.encode()).hexdigest()[:8]
        metadata_base_id = self._create_chunk_id(file_path, chunk_name, "metadata", "documentation")
        metadata_id = f"{metadata_base_id}::{metadata_hash}"
        
        metadata_chunk = EntityChunk(
            id=metadata_id,
            entity_name=chunk_name,
            chunk_type="metadata",
            content=metadata_content,
            metadata={
                "entity_type": "documentation",
                "file_path": str(file_path),
                "line_number": int(first_header_line) + 1,
                "section_type": "markdown_section",
                "has_implementation": True,
                "content_length": len(full_content),
                "word_count": word_count,
                "line_count": line_count,
                "token_count": total_tokens,
                "section_count": len(section_group),
                "headers": combined_headers
            },
        )
        
        return impl_chunk, metadata_chunk


class ParserRegistry:
    """Registry for managing multiple code parsers."""

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self._parsers: list[CodeParser] = []

        # Load project config for parser initialization
        self.project_config = self._load_project_config()

        self._register_default_parsers()

    def _load_project_config(self) -> dict[str, Any]:
        """Load project-specific configuration."""
        try:
            from ..config.project_config import ProjectConfigManager

            config_manager = ProjectConfigManager(self.project_path)
            if config_manager.exists:
                config = config_manager.load()
                return config.__dict__ if hasattr(config, '__dict__') else {}
            return {}
        except Exception as e:
            logger.debug(f"Failed to load project config: {e}")
            return {}

    def _register_default_parsers(self) -> None:
        """Register default parsers."""
        from .css_parser import CSSParser
        from .html_parser import HTMLParser
        from .javascript_parser import JavaScriptParser
        from .json_parser import JSONParser
        from .text_parser import CSVParser, INIParser, TextParser
        from .yaml_parser import YAMLParser

        # Core language parsers
        self.register(PythonParser(self.project_path))
        self.register(JavaScriptParser())

        # Data format parsers with project config
        json_config = {}
        try:
            # Extract JSON config from project config (handling ProjectConfig objects)
            if (
                hasattr(self.project_config, "indexing")
                and self.project_config.indexing
            ):
                if (
                    hasattr(self.project_config.indexing, "parser_config")
                    and self.project_config.indexing.parser_config
                ):
                    parser_config = self.project_config.indexing.parser_config
                    if isinstance(parser_config, dict):
                        json_parser_config = parser_config.get("json", None)
                    else:
                        # Handle Pydantic model attributes
                        json_parser_config = getattr(parser_config, "json", None)
                    if json_parser_config:
                        # Convert ParserConfig object to dict
                        json_config = {
                            "content_only": getattr(
                                json_parser_config, "content_only", False
                            ),
                            "max_content_items": getattr(
                                json_parser_config, "max_content_items", 0
                            ),
                            "special_files": getattr(
                                json_parser_config,
                                "special_files",
                                ["package.json", "tsconfig.json", "composer.json"],
                            ),
                        }
                        logger.debug(f"Extracted JSON parser config: {json_config}")
            elif isinstance(self.project_config, dict):
                # Fallback for dict-based config
                indexing_config = self.project_config.get("indexing", {})
                if hasattr(indexing_config, "parser_config"):
                    # indexing_config is a Pydantic object
                    parser_config = indexing_config.parser_config
                    if isinstance(parser_config, dict):
                        json_config = parser_config.get("json", {})
                    else:
                        json_parser_config = getattr(parser_config, "json", None)
                        if json_parser_config:
                            json_config = {
                                "content_only": getattr(json_parser_config, "content_only", False),
                                "max_content_items": getattr(json_parser_config, "max_content_items", 0),
                                "special_files": getattr(json_parser_config, "special_files", ["package.json", "tsconfig.json", "composer.json"]),
                            }
                elif isinstance(indexing_config, dict):
                    # Pure dict-based config
                    parser_config = indexing_config.get("parser_config", {})
                    json_config = parser_config.get("json", {})
        except Exception as e:
            logger.debug(f"Failed to extract JSON parser config: {e}")
            json_config = {}

        # Ensure json_config is always a dict
        if not isinstance(json_config, dict):
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

    def register(self, parser: CodeParser) -> None:
        """Register a new parser."""
        self._parsers.append(parser)

    def get_parser_for_file(self, file_path: Path) -> CodeParser | None:
        """Get the appropriate parser for a file."""
        for parser in self._parsers:
            if parser.can_parse(file_path):
                return parser
        return None

    def parse_file(
        self, file_path: Path, batch_callback: Any = None, global_entity_names: Any = None
    ) -> ParserResult:
        """Parse a file using the appropriate parser."""
        parser = self.get_parser_for_file(file_path)

        if parser is None:
            result = ParserResult(file_path=file_path, entities=[], relations=[])
            result.errors.append(f"No parser available for {file_path.suffix}")  # type: ignore[union-attr]
            return result

        # Try to pass both batch_callback and global_entity_names if parser supports them
        try:
            return parser.parse(
                file_path,
                batch_callback=batch_callback,  # type: ignore[call-arg]
                global_entity_names=global_entity_names,
            )
        except TypeError:
            # Fallback for parsers that don't support new parameters
            try:
                return parser.parse(file_path, batch_callback=batch_callback)  # type: ignore[call-arg]
            except TypeError:
                # Final fallback for basic parsers
                return parser.parse(file_path)

    def get_supported_extensions(self) -> list[str]:
        """Get all supported file extensions."""
        extensions = set()
        for parser in self._parsers:
            extensions.update(parser.get_supported_extensions())
        return sorted(extensions)
