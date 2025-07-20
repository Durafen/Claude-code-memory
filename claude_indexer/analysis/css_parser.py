import time
from pathlib import Path
from typing import Any

from tree_sitter import Node

from .base_parsers import TreeSitterParser
from .entities import (
    Entity,
    EntityChunk,
    EntityType,
    Relation,
    RelationFactory,
)
from .parser import ParserResult


class CSSParser(TreeSitterParser):
    """Parse CSS/SCSS with tree-sitter."""

    SUPPORTED_EXTENSIONS = [".css", ".scss", ".sass"]

    def __init__(self, config: dict[str, Any] | None = None):
        import tree_sitter_css as tscss

        super().__init__(tscss, config)

    def parse(self, file_path: Path) -> ParserResult:
        """Extract CSS rules, classes, IDs."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])

        try:
            # Read and parse CSS
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            result.file_hash = self._get_file_hash(file_path)
            tree = self.parse_tree(content)

            # Skip strict syntax checking for CSS - tree-sitter-css has false positives with valid CSS
            # (keyframe percentages, calc() expressions, etc. trigger ERROR nodes but are valid CSS)

            entities = []
            relations = []
            chunks = []

            # Create file entity
            file_entity = self._create_file_entity(file_path, content_type="stylesheet")
            entities.append(file_entity)

            # Extract class definitions
            class_entities = self._extract_class_definitions(
                tree.root_node, content, file_path
            )
            entities.extend(class_entities)

            # Extract ID definitions
            id_entities = self._extract_id_definitions(
                tree.root_node, content, file_path
            )
            entities.extend(id_entities)

            # Extract CSS variables
            variable_entities = self._extract_css_variables(
                tree.root_node, content, file_path
            )
            entities.extend(variable_entities)

            # Extract @import relations
            import_relations = self._extract_import_relations(
                tree.root_node, content, file_path
            )
            relations.extend(import_relations)

            # Create containment relations
            file_name = str(file_path)
            for entity in entities[1:]:  # Skip file entity
                relation = RelationFactory.create_contains_relation(
                    file_name, entity.name
                )
                relations.append(relation)

            # Create chunks for searchability
            chunks = self._create_css_chunks(file_path, tree.root_node, content)

            result.entities = entities
            result.relations = relations
            result.implementation_chunks = chunks

        except Exception as e:
            if result.errors is not None:
                result.errors.append(f"CSS parsing failed: {e}")

        result.parsing_time = time.time() - start_time
        return result

    def _extract_class_definitions(
        self, root: Node, content: str, file_path: Path
    ) -> list[Entity]:
        """Extract CSS class definitions."""
        entities = []

        # Find all selectors that contain class selectors
        for rule in self._find_nodes_by_type(root, ["rule_set"]):
            selectors = self._extract_selectors_from_rule(rule, content)

            for selector in selectors:
                # Extract class names (starting with .)
                if "." in selector:
                    class_parts = selector.split(".")
                    for part in class_parts[1:]:  # Skip first empty part
                        # Clean up class name (remove pseudo-selectors, etc.)
                        class_name = part.split(":")[0].split("[")[0].split(" ")[0]
                        if class_name:
                            entity = Entity(
                                name=f".{class_name}",
                                entity_type=EntityType.DOCUMENTATION,  # CSS rules as documentation
                                observations=[
                                    f"CSS class: .{class_name}",
                                    f"Selector: {selector}",
                                    f"Located in {file_path.name}",
                                ],
                                file_path=file_path,
                                line_number=rule.start_point[0] + 1,
                                metadata={
                                    "type": "css_class",
                                    "class_name": class_name,
                                    "full_selector": selector,
                                },
                            )
                            entities.append(entity)

        return entities

    def _extract_id_definitions(
        self, root: Node, content: str, file_path: Path
    ) -> list[Entity]:
        """Extract CSS ID definitions."""
        entities = []

        # Find all selectors that contain ID selectors
        for rule in self._find_nodes_by_type(root, ["rule_set"]):
            selectors = self._extract_selectors_from_rule(rule, content)

            for selector in selectors:
                # Extract ID names (starting with #)
                if "#" in selector:
                    id_parts = selector.split("#")
                    for part in id_parts[1:]:  # Skip first part
                        # Clean up ID name
                        id_name = part.split(":")[0].split("[")[0].split(" ")[0]
                        if id_name:
                            entity = Entity(
                                name=f"#{id_name}",
                                entity_type=EntityType.DOCUMENTATION,
                                observations=[
                                    f"CSS ID: #{id_name}",
                                    f"Selector: {selector}",
                                    f"Located in {file_path.name}",
                                ],
                                file_path=file_path,
                                line_number=rule.start_point[0] + 1,
                                metadata={
                                    "type": "css_id",
                                    "id_name": id_name,
                                    "full_selector": selector,
                                },
                            )
                            entities.append(entity)

        return entities

    def _extract_css_variables(
        self, root: Node, content: str, file_path: Path
    ) -> list[Entity]:
        """Extract CSS custom properties (variables)."""
        entities = []

        for declaration in self._find_nodes_by_type(root, ["declaration"]):
            property_text = self.extract_node_text(declaration, content)

            # Check if this is a CSS variable (starts with --)
            if property_text.strip().startswith("--"):
                lines = property_text.split(":")
                if len(lines) >= 2:
                    var_name = lines[0].strip()
                    var_value = ":".join(lines[1:]).strip().rstrip(";")

                    entity = Entity(
                        name=var_name,
                        entity_type=EntityType.DOCUMENTATION,
                        observations=[
                            f"CSS variable: {var_name}",
                            f"Value: {var_value}",
                            f"Located in {file_path.name}",
                        ],
                        file_path=file_path,
                        line_number=declaration.start_point[0] + 1,
                        metadata={
                            "type": "css_variable",
                            "variable_name": var_name,
                            "value": var_value,
                        },
                    )
                    entities.append(entity)

        return entities

    def _extract_import_relations(
        self, root: Node, content: str, file_path: Path
    ) -> list[Relation]:
        """Extract @import relations."""
        relations = []

        for import_stmt in self._find_nodes_by_type(root, ["import_statement"]):
            import_text = self.extract_node_text(import_stmt, content)

            # Extract the imported file path
            # @import "path" or @import url("path")
            import re

            match = re.search(r'["\']([^"\']+)["\']', import_text)
            if match:
                imported_path = match.group(1)

                relation = RelationFactory.create_imports_relation(
                    importer=str(file_path),
                    imported=imported_path,
                    import_type="css_import",
                )
                relations.append(relation)

        return relations

    def _extract_selectors_from_rule(self, rule: Node, content: str) -> list[str]:
        """Extract selectors from a CSS rule."""
        selectors = []

        # Find selectors node
        for child in rule.children:
            if child.type == "selectors":
                selector_text = self.extract_node_text(child, content)
                # Split by comma for multiple selectors
                selectors.extend([s.strip() for s in selector_text.split(",")])
                break

        return selectors

    def _create_css_chunks(
        self, file_path: Path, _root: Node, content: str
    ) -> list[EntityChunk]:
        """Create searchable chunks from CSS content."""
        chunks = []

        # Create implementation chunk with full CSS content
        impl_chunk = EntityChunk(
            id=self._create_chunk_id(file_path, "content", "implementation"),
            entity_name=str(file_path),
            chunk_type="implementation",
            content=content,  # Full CSS content
            metadata={
                "entity_type": "css_file",
                "file_path": str(file_path),
                "start_line": 1,
                "end_line": len(content.split("\n")),
            },
        )
        chunks.append(impl_chunk)

        # Note: Metadata chunks are auto-generated by progressive disclosure from Entity objects

        return chunks
