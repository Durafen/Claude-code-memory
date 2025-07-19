import os
import re
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


class JSONParser(TreeSitterParser):
    """Parse JSON with tree-sitter for structural relation extraction."""

    SUPPORTED_EXTENSIONS = [".json"]

    def __init__(self, config: dict[str, Any] | None = None):
        import tree_sitter_json as tsjson

        super().__init__(tsjson, config)
        self.special_files = (
            config.get(
                "special_files", ["package.json", "tsconfig.json", "composer.json"]
            )
            if config
            else ["package.json", "tsconfig.json", "composer.json"]
        )

    def parse(self, file_path: Path, batch_callback: Any = None) -> ParserResult:
        """Extract JSON structure as entities and relations."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])

        try:
            # Check file size for streaming decision
            file_size = os.path.getsize(file_path)
            # Use content extraction for all files in content_only mode
            # Use streaming (batch processing) when batch_callback is provided
            use_content_extraction = self.config.get("content_only", False)
            use_streaming = use_content_extraction and batch_callback is not None

            if use_content_extraction:
                if use_streaming:
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.info(
                        f"🚀 Using STREAMING parser for {file_path.name} ({file_size / 1024 / 1024:.1f} MB)"
                    )
                    # Use streaming parser for large files
                    return self._extract_content_items_streaming(
                        file_path, batch_callback
                    )
                else:
                    # Read and parse JSON for smaller files with content extraction
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()

                    result.file_hash = self._get_file_hash(file_path)
                    tree = self.parse_tree(content)

                    # Check for syntax errors
                    if self._has_syntax_errors(tree):
                        result.errors.append(f"JSON syntax errors in {file_path.name}")  # type: ignore[union-attr]

                    # Use traditional content extraction for smaller files
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.info(
                        f"📄 Using CONTENT extraction for {file_path.name} ({file_size / 1024 / 1024:.1f} MB)"
                    )
                    return self._extract_content_items(file_path, tree, content)

            # Read and parse JSON normally for smaller files (non-content mode)
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            result.file_hash = self._get_file_hash(file_path)
            tree = self.parse_tree(content)

            # Check for syntax errors
            if self._has_syntax_errors(tree):
                result.errors.append(f"JSON syntax errors in {file_path.name}")  # type: ignore[union-attr]

            entities = []
            relations = []
            chunks = []

            # Create file entity
            file_entity = self._create_file_entity(
                file_path, content_type="configuration"
            )
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
            result.errors.append(f"JSON parsing failed: {e}")  # type: ignore[union-attr]

        result.parsing_time = time.time() - start_time
        return result

    def _find_first_object(self, node: Node) -> Node | None:
        """Find the first object node in the tree."""
        if node.type == "object":
            return node
        for child in node.children:
            obj = self._find_first_object(child)
            if obj:
                return obj
        return None

    def _extract_object_structure(
        self, node: Node, content: str, file_path: Path, parent_path: str
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract entities and relations from JSON object structure."""
        entities = []
        relations = []

        # Process object pairs (key-value)
        for child in node.children:
            if child.type == "pair":
                key_node = child.child_by_field_name("key")
                value_node = child.child_by_field_name("value")

                if key_node and value_node:
                    key = self.extract_node_text(key_node, content).strip('"')
                    current_path = f"{parent_path}.{key}" if parent_path else key

                    # Create entity for this key
                    entity = Entity(
                        name=current_path,
                        entity_type=EntityType.DOCUMENTATION,  # JSON keys as documentation
                        observations=[f"JSON key: {key}"],
                        file_path=file_path,
                        line_number=key_node.start_point[0] + 1,
                    )
                    entities.append(entity)

                    # Create containment relation
                    parent = str(file_path) if not parent_path else parent_path
                    relation = RelationFactory.create_contains_relation(
                        parent, current_path
                    )
                    relations.append(relation)

                    # Recursively process nested objects
                    if value_node.type == "object":
                        nested_entities, nested_relations = (
                            self._extract_object_structure(
                                value_node, content, file_path, current_path
                            )
                        )
                        entities.extend(nested_entities)
                        relations.extend(nested_relations)

                    # Process arrays
                    elif value_node.type == "array":
                        # Create collection entity
                        array_entity = Entity(
                            name=f"{current_path}[]",
                            entity_type=EntityType.DOCUMENTATION,
                            observations=[f"JSON array: {key}"],
                            file_path=file_path,
                            line_number=value_node.start_point[0] + 1,
                        )
                        entities.append(array_entity)

                        # Array contains relation
                        relation = RelationFactory.create_contains_relation(
                            current_path, f"{current_path}[]"
                        )
                        relations.append(relation)

        return entities, relations

    def _handle_special_json(
        self, file_path: Path, root: Node, content: str
    ) -> tuple[list[Entity], list[Relation]]:
        """Special handling for known JSON file types."""
        entities = []
        relations = []

        if file_path.name == "package.json":
            # Extract dependencies as import relations
            deps_entities, deps_relations = self._extract_package_dependencies(
                root, content, file_path
            )
            entities.extend(deps_entities)
            relations.extend(deps_relations)

        elif file_path.name == "tsconfig.json":
            # Extract TypeScript configuration
            config_entities = self._extract_tsconfig_info(root, content, file_path)
            entities.extend(config_entities)

        return entities, relations

    def _extract_package_dependencies(
        self, root: Node, content: str, file_path: Path
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract dependencies from package.json."""
        entities = []
        relations = []

        # Find dependencies and devDependencies objects
        for node in self._find_nodes_by_type(root, ["pair"]):
            key_node = node.child_by_field_name("key")
            if key_node:
                key = self.extract_node_text(key_node, content).strip('"')
                if key in ["dependencies", "devDependencies"]:
                    value_node = node.child_by_field_name("value")
                    if value_node and value_node.type == "object":
                        # Each dependency is an import relation
                        for pair in value_node.children:
                            if pair.type == "pair":
                                dep_key = pair.child_by_field_name("key")
                                if dep_key:
                                    dep_name = self.extract_node_text(
                                        dep_key, content
                                    ).strip('"')
                                    # Create import relation
                                    relation = RelationFactory.create_imports_relation(
                                        importer=str(file_path),
                                        imported=dep_name,
                                        import_type="npm_dependency",
                                    )
                                    relations.append(relation)

        return entities, relations

    def _extract_tsconfig_info(
        self, root: Node, content: str, file_path: Path
    ) -> list[Entity]:
        """Extract TypeScript configuration info."""
        entities = []

        # Create entity for compiler options
        for node in self._find_nodes_by_type(root, ["pair"]):
            key_node = node.child_by_field_name("key")
            if key_node:
                key = self.extract_node_text(key_node, content).strip('"')
                if key == "compilerOptions":
                    entity = Entity(
                        name="TypeScript Compiler Options",
                        entity_type=EntityType.DOCUMENTATION,
                        observations=["TypeScript compiler configuration"],
                        file_path=file_path,
                        line_number=node.start_point[0] + 1,
                    )
                    entities.append(entity)

        return entities

    def _create_json_chunks(
        self, file_path: Path, root: Node, content: str
    ) -> list[EntityChunk]:
        """Create searchable chunks from JSON content."""
        chunks = []

        # Create implementation chunk with full JSON content
        impl_chunk = EntityChunk(
            id=self._create_chunk_id(file_path, "content", "implementation"),
            entity_name=str(file_path),
            chunk_type="implementation",
            content=content,  # Full JSON content
            metadata={
                "entity_type": "json_file",
                "file_path": str(file_path),
                "start_line": 1,
                "end_line": len(content.split("\n")),
            },
        )
        chunks.append(impl_chunk)

        # Note: Metadata chunks are auto-generated by progressive disclosure from Entity objects

        return chunks

    def _create_content_only_result(
        self, file_path: Path, content: str
    ) -> ParserResult:
        """Create a content-only result without structural parsing."""
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        result.file_hash = self._get_file_hash(file_path)

        # Create only the file entity
        file_entity = self._create_file_entity(file_path, content_type="content")
        result.entities = [file_entity]

        # Create content chunk for searchability
        chunk = EntityChunk(
            id=self._create_chunk_id(file_path, "content", "implementation"),
            entity_name=str(file_path),
            chunk_type="implementation",
            content=content,
            metadata={
                "entity_type": "json_content",
                "file_path": str(file_path),
                "has_implementation": True,
            },
        )
        result.implementation_chunks = [chunk]

        return result

    def _extract_content_items(
        self, file_path: Path, tree, content: str
    ) -> ParserResult:
        """Enhanced content extraction: extract individual posts/articles as separate entities."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        result.file_hash = self._get_file_hash(file_path)

        try:
            import json

            data = json.loads(content)

            entities = []
            chunks = []

            # Create file entity
            file_entity = self._create_file_entity(
                file_path, content_type="content_collection"
            )
            entities.append(file_entity)

            # Extract content items from arrays
            content_count = 0
            content_count += self._extract_array_items(
                data, "topics", file_path, entities, chunks
            )
            content_count += self._extract_array_items(
                data, "posts", file_path, entities, chunks
            )
            content_count += self._extract_array_items(
                data, "articles", file_path, entities, chunks
            )
            content_count += self._extract_array_items(
                data, "comments", file_path, entities, chunks
            )
            content_count += self._extract_array_items(
                data, "messages", file_path, entities, chunks
            )
            content_count += self._extract_array_items(
                data, "threads", file_path, entities, chunks
            )
            content_count += self._extract_array_items(
                data, "forums", file_path, entities, chunks
            )
            content_count += self._extract_array_items(
                data, "site_pages", file_path, entities, chunks
            )
            content_count += self._extract_array_items(
                data, "items", file_path, entities, chunks
            )

            # If no content items found, fallback to full content chunk
            if content_count == 0:
                chunk = EntityChunk(
                    id=self._create_chunk_id(file_path, "content", "implementation"),
                    entity_name=str(file_path),
                    chunk_type="implementation",
                    content=content,
                    metadata={
                        "entity_type": "json_content",
                        "file_path": str(file_path),
                        "has_implementation": True,
                    },
                )
                chunks.append(chunk)

            result.entities = entities
            result.relations = []  # No structural relations in content_only mode
            result.implementation_chunks = chunks

        except json.JSONDecodeError as e:
            result.errors.append(f"JSON parsing failed: {e}")  # type: ignore[union-attr]
            # Fallback to simple content result
            return self._create_content_only_result(file_path, content)
        except Exception as e:
            result.errors.append(f"Content extraction failed: {e}")  # type: ignore[union-attr]
            return self._create_content_only_result(file_path, content)

        result.parsing_time = time.time() - start_time
        return result

    def _extract_array_items(
        self, data: dict, array_key: str, file_path: Path, entities: list, chunks: list
    ) -> int:
        """Extract individual items from a content array."""
        if array_key not in data or not isinstance(data[array_key], list):
            return 0

        items = data[array_key]
        count = 0

        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue

            # Create meaningful entity name
            entity_name = self._create_content_entity_name(array_key, item, i)

            # Extract text content from the item
            content_text = self._extract_item_content(item)

            if content_text:
                # Create entity
                entity = Entity(
                    name=entity_name,
                    entity_type=EntityType.DOCUMENTATION,
                    observations=[f"{array_key.rstrip('s').title()}: {entity_name}"],
                    file_path=file_path,
                    line_number=1,  # JSON items don't have line numbers
                    metadata={
                        "content_type": array_key.rstrip("s"),
                        "item_index": i,
                        "source_array": array_key,
                    },
                )
                entities.append(entity)

                # Create content chunk
                chunk = EntityChunk(
                    id=self._create_chunk_id(file_path, entity_name, "implementation"),
                    entity_name=entity_name,
                    chunk_type="implementation",
                    content=content_text,
                    metadata={
                        "entity_type": f"{array_key.rstrip('s')}_content",
                        "file_path": str(file_path),
                        "has_implementation": True,
                        "item_index": i,
                    },
                )
                chunks.append(chunk)
                count += 1

        return count

    def _create_content_entity_name(
        self, array_key: str, item: dict, index: int
    ) -> str:
        """Create a meaningful name for a content entity."""
        # Use chunk_number from JSON if available (for book files)
        if "chunk_number" in item:
            chunk_num = item["chunk_number"]

            # Try to use title, subject, or name fields with chunk number
            for field in ["title", "subject", "name", "headline"]:
                if field in item and isinstance(item[field], str):
                    title = item[field].strip()
                    if title:
                        # Clean and truncate title
                        title = title.replace("\n", " ").replace("\r", "")[:100]
                        return f"{array_key.rstrip('s')}_{chunk_num}_{title}"

            # Use chunk number from JSON
            return f"{array_key.rstrip('s')}_{chunk_num}"

        # Try to use title, subject, or name fields
        for field in ["title", "subject", "name", "headline"]:
            if field in item and isinstance(item[field], str):
                title = item[field].strip()
                if title:
                    # Clean and truncate title
                    title = title.replace("\n", " ").replace("\r", "")[:100]
                    return f"{array_key.rstrip('s')}_{index + 1}_{title}"

        # Try to use ID fields
        for field in ["id", "_id", "post_id", "article_id"]:
            if field in item:
                return f"{array_key.rstrip('s')}_{item[field]}"

        # Fallback to index
        return f"{array_key.rstrip('s')}_{index + 1}"

    def _extract_item_content(self, item: dict) -> str:
        """Extract meaningful text content from a content item."""
        content_parts = []

        # Primary content fields
        for field in ["content", "body", "text", "message", "description"]:
            if field in item and isinstance(item[field], str):
                content_text = item[field].strip()
                # Strip HTML/JS/CSS if content_only mode is enabled
                if self.config.get("content_only", False):
                    content_text = self._strip_html_js_css(content_text)
                content_parts.append(content_text)

        # Metadata fields
        for field in ["title", "subject", "name", "headline"]:
            if field in item and isinstance(item[field], str):
                title = item[field].strip()
                # Strip HTML from titles too
                if self.config.get("content_only", False):
                    title = self._strip_html_js_css(title)
                if title:
                    content_parts.insert(0, f"Title: {title}")

        # Author information
        author_info = self._extract_author_info(item)
        if author_info:
            content_parts.append(f"Author: {author_info}")

        # Join all content
        full_content = "\n\n".join(content_parts)

        # Include nested content (replies, comments)
        nested_content = self._extract_nested_content(item)
        if nested_content:
            full_content += "\n\n--- Replies/Comments ---\n" + nested_content

        return full_content if full_content.strip() else str(item)

    def _extract_author_info(self, item: dict) -> str:
        """Extract author information from an item."""
        for field in ["author", "user", "username", "created_by", "poster"]:
            if field in item:
                author = item[field]
                if isinstance(author, str):
                    return author
                elif isinstance(author, dict) and "name" in author:
                    return author["name"]
        return ""

    def _extract_nested_content(self, item: dict) -> str:
        """Extract content from nested arrays (replies, comments)."""
        nested_parts = []

        for field in ["replies", "comments", "responses"]:
            if field in item and isinstance(item[field], list):
                for i, nested_item in enumerate(item[field]):
                    if isinstance(nested_item, dict):
                        nested_content = self._extract_item_content(nested_item)
                        if nested_content.strip():
                            nested_parts.append(f"Reply {i + 1}: {nested_content}")

        return "\n\n".join(nested_parts)

    def _strip_html_js_css(self, text: str) -> str:
        """Remove HTML tags, JavaScript, and CSS from text while preserving readable content."""
        if not text:
            return text

        # Remove PHP code blocks (common in הפיקאפר data)
        text = re.sub(r"<\?php.*?\?>", "", text, flags=re.DOTALL)

        # Remove JavaScript blocks
        text = re.sub(
            r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE
        )
        text = re.sub(r"<script[^>]*>", "", text, flags=re.IGNORECASE)

        # Remove CSS/style blocks
        text = re.sub(
            r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE
        )
        text = re.sub(r'style\s*=\s*["\'].*?["\']', "", text, flags=re.IGNORECASE)

        # Convert line break tags to actual line breaks
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<p[^>]*>", "", text, flags=re.IGNORECASE)

        # Convert list items to structured text
        text = re.sub(r"<li[^>]*>", "• ", text, flags=re.IGNORECASE)
        text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)

        # Remove all remaining HTML tags
        text = re.sub(r"<[^>]+>", "", text)

        # Decode HTML entities
        text = text.replace("&nbsp;", " ")
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        text = text.replace("&#39;", "'")

        # Clean up excessive whitespace
        text = re.sub(r"\r\n", "\n", text)  # Normalize line endings
        text = re.sub(r"\n{3,}", "\n\n", text)  # Max 2 consecutive newlines
        text = re.sub(r"[ \t]+", " ", text)  # Collapse multiple spaces/tabs
        text = re.sub(r" +\n", "\n", text)  # Remove trailing spaces
        text = re.sub(r"\n +", "\n", text)  # Remove leading spaces on lines

        return text.strip()

    def _extract_content_items_streaming(
        self, file_path: Path, batch_callback=None, batch_size=1000
    ) -> ParserResult:
        """Extract content items using streaming JSON parser for large files."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        result.file_hash = self._get_file_hash(file_path)

        try:
            import logging

            import ijson

            logger = logging.getLogger(__name__)

            # Batch processing variables
            current_batch_entities = []
            current_batch_chunks = []
            total_entities_processed = 0
            total_chunks_processed = 0
            processed_count = 0
            max_items = self.config.get("max_content_items", 0)  # 0 means no limit

            # Create file entity (processed immediately, not batched)
            file_entity = self._create_file_entity(
                file_path, content_type="content_collection"
            )

            # Process file entity immediately if callback available
            if batch_callback:
                batch_result = batch_callback([file_entity], [], [])
                if batch_result:
                    total_entities_processed += 1
                    logger.info(f"📁 Processed file entity for {file_path.name}")
            else:
                # Fallback: accumulate for traditional return
                current_batch_entities.append(file_entity)

            # Arrays to check for content
            content_arrays = [
                "topics",
                "posts",
                "articles",
                "comments",
                "messages",
                "threads",
                "forums",
                "site_pages",
                "items",
                "content",
                "chunks",
            ]

            # Process each content array
            for array_key in content_arrays:
                with open(file_path, "rb") as f:
                    try:
                        # Stream items from the specific array
                        parser = ijson.items(f, f"{array_key}.item")

                        for i, item in enumerate(parser):
                            if not isinstance(item, dict):
                                continue

                            # Check item limit
                            if max_items > 0 and processed_count >= max_items:
                                break

                            # Process individual item into current batch
                            self._process_streamed_item(
                                item,
                                array_key,
                                i,
                                file_path,
                                current_batch_entities,
                                current_batch_chunks,
                            )
                            processed_count += 1

                            # Check if batch is ready for processing
                            if len(current_batch_chunks) >= batch_size:
                                if batch_callback:
                                    # Process batch immediately
                                    batch_result = batch_callback(
                                        current_batch_entities, [], current_batch_chunks
                                    )
                                    if batch_result:
                                        total_entities_processed += len(
                                            current_batch_entities
                                        )
                                        total_chunks_processed += len(
                                            current_batch_chunks
                                        )
                                        logger.info(
                                            f"✅ Processed batch: {len(current_batch_chunks)} chunks from {file_path.name} (Total: {total_chunks_processed})"
                                        )

                                    # Clear batch memory
                                    current_batch_entities.clear()
                                    current_batch_chunks.clear()
                                else:
                                    # Fallback: accumulate (this shouldn't happen in streaming mode)
                                    logger.warning(
                                        f"⚠️ No batch callback provided, accumulating {len(current_batch_chunks)} chunks"
                                    )

                            # Log progress for large files
                            if processed_count % 100 == 0:
                                logger.debug(
                                    f"Parsed {processed_count} items from {file_path.name} (Memory: {len(current_batch_chunks)} chunks)"
                                )

                    except ijson.JSONError:
                        # Array doesn't exist or parsing error, continue to next
                        continue
                    except Exception as e:
                        # Log specific array error but continue processing
                        result.errors.append(  # type: ignore[union-attr]
                            f"Error processing {array_key}: {str(e)[:100]}"
                        )
                        continue

                # Check if we've hit the limit
                if max_items > 0 and processed_count >= max_items:
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.info(
                        f"Reached max_content_items limit ({max_items}) for {file_path.name}"
                    )
                    break

            # Process final partial batch if any items remain
            if current_batch_entities or current_batch_chunks:
                if batch_callback:
                    batch_result = batch_callback(
                        current_batch_entities, [], current_batch_chunks
                    )
                    if batch_result:
                        total_entities_processed += len(current_batch_entities)
                        total_chunks_processed += len(current_batch_chunks)
                        logger.info(
                            f"✅ Processed final batch: {len(current_batch_chunks)} chunks from {file_path.name}"
                        )
                    # Clear final batch
                    current_batch_entities.clear()
                    current_batch_chunks.clear()

            # Handle case where no content items were found
            if processed_count == 0:
                result.errors.append("No content items found in any expected arrays")  # type: ignore[union-attr]
                if not batch_callback:
                    # Only create placeholder chunk if not using callback (traditional mode)
                    chunk = EntityChunk(
                        id=self._create_chunk_id(
                            file_path, "no_content", "implementation"
                        ),
                        entity_name=str(file_path),
                        chunk_type="implementation",
                        content="Large JSON file with no extractable content items",
                        metadata={
                            "entity_type": "json_content",
                            "file_path": str(file_path),
                        },
                    )
                    current_batch_chunks.append(chunk)

            # Set result based on processing mode
            if batch_callback:
                # Streaming mode: return minimal result with counts
                result.entities = []  # Already processed via callback
                result.relations = []  # No structural relations in content_only mode
                result.implementation_chunks = []  # Already processed via callback
                # Store metadata for reporting
                result.entities_created = total_entities_processed
                result.implementation_chunks_created = total_chunks_processed
                logger.info(
                    f"🚀 Streaming completed: {processed_count} items → {total_chunks_processed} chunks processed from {file_path.name}"
                )
            else:
                # Traditional mode: return accumulated results
                result.entities = current_batch_entities
                result.relations = []  # No structural relations in content_only mode
                result.implementation_chunks = current_batch_chunks
                logger.info(
                    f"📄 Traditional processing: {processed_count} items from {file_path.name}"
                )

        except Exception as e:
            result.errors.append(f"Streaming JSON parsing failed: {str(e)[:200]}")  # type: ignore[union-attr]
            # Return minimal result on error
            file_entity = self._create_file_entity(file_path, content_type="error")
            result.entities = [file_entity]
            result.implementation_chunks = []

        result.parsing_time = time.time() - start_time
        return result

    def _process_streamed_item(
        self,
        item: dict,
        array_key: str,
        index: int,
        file_path: Path,
        entities: list,
        chunks: list,
    ):
        """Process a single streamed item."""
        # Create meaningful entity name
        entity_name = self._create_content_entity_name(array_key, item, index)

        # Extract text content from the item
        content_text = self._extract_item_content(item)

        if content_text:
            # Create entity
            entity = Entity(
                name=entity_name,
                entity_type=EntityType.DOCUMENTATION,
                observations=[f"{array_key.rstrip('s').title()}: {entity_name}"],
                file_path=file_path,
                line_number=1,  # JSON items don't have line numbers
                metadata={
                    "content_type": array_key.rstrip("s"),
                    "item_index": index,
                    "source_array": array_key,
                },
            )
            entities.append(entity)

            # Create content chunk
            chunk = EntityChunk(
                id=self._create_chunk_id(file_path, entity_name, "implementation"),
                entity_name=entity_name,
                chunk_type="implementation",
                content=content_text,
                metadata={
                    "entity_type": f"{array_key.rstrip('s')}_content",
                    "file_path": str(file_path),
                    "has_implementation": True,
                    "item_index": index,
                },
            )
            chunks.append(chunk)
