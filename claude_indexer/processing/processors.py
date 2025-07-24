"""Specialized processor implementations."""

from typing import TYPE_CHECKING

from .content_processor import ContentProcessor
from .context import ProcessingContext
from .results import ProcessingResult

if TYPE_CHECKING:
    from ..analysis.entities import Entity, EntityChunk, Relation


class EntityProcessor(ContentProcessor):
    """Processor for entity metadata chunks."""

    def process_batch(
        self, entities: list["Entity"], context: ProcessingContext
    ) -> ProcessingResult:
        """Process entity metadata with Git+Meta deduplication."""
        if not entities:
            return ProcessingResult.success_result()

        if self.logger:
            self.logger.debug(
                f"🧠 Processing entities with Git+Meta deduplication: {len(entities)} items"
            )

        # Phase 1: Create metadata chunks with implementation flags
        chunks_to_process = []
        for entity in entities:
            # Skip creating old-style metadata chunks for markdown documentation entities
            # since markdown parser now creates better progressive disclosure metadata chunks
            if (entity.file_path and str(entity.file_path).endswith('.md') and 
                entity.entity_type.value == 'documentation'):
                continue

            # BUGFIX: Import entities should NEVER have has_implementation=true
            # regardless of name collisions with classes/functions
            if entity.entity_type.value == 'import':
                has_implementation = False
            else:
                has_implementation = entity.name in context.implementation_entity_names
            
            
            from ..analysis.entities import EntityChunk

            metadata_chunk = EntityChunk.create_metadata_chunk(
                entity, has_implementation
            )
            chunks_to_process.append(metadata_chunk)

        # Phase 2: Enhanced Git+Meta deletion - handle both existing and deleted entities
        entities_deleted = 0

        # DEBUG: Log changed_entity_ids to diagnose deletion bug
        if self.logger:
            self.logger.debug(f"🔍 DEBUG: changed_entity_ids contains {len(context.changed_entity_ids)} items:")
            for i, entity_id in enumerate(list(context.changed_entity_ids)[:5]):  # Show first 5
                self.logger.debug(f"🔍 DEBUG:   [{i+1}] {entity_id}")
            if len(context.changed_entity_ids) > 5:
                self.logger.debug(f"🔍 DEBUG:   ... and {len(context.changed_entity_ids) - 5} more")

        # FIX: Find and delete entities that no longer exist in current parse
        # Get all files being processed to find deleted entities
        files_being_processed = {
            chunk.metadata.get("file_path")
            for chunk in chunks_to_process
            if chunk.metadata.get("file_path")
        }

        # Get current entity names from chunks being processed
        current_entity_names_by_file = {}
        for chunk in chunks_to_process:
            file_path = chunk.metadata.get("file_path")
            if file_path:
                if file_path not in current_entity_names_by_file:
                    current_entity_names_by_file[file_path] = set()
                current_entity_names_by_file[file_path].add(chunk.entity_name)

        # Track deleted entities separately from changed entities
        deleted_entity_ids = set()

        # For each file, find entities that exist in DB but not in current parse (deleted entities)
        for file_path in files_being_processed:
            if hasattr(self.vector_store, 'find_entities_for_file_by_type'):
                entities_by_type = self.vector_store.find_entities_for_file_by_type(
                    context.collection_name, file_path, ["metadata", "implementation"]
                )

                current_entities = current_entity_names_by_file.get(file_path, set())

                # Find entities that exist in DB but not in current parse
                for chunk_type in ["metadata", "implementation"]:
                    for existing_entity in entities_by_type.get(chunk_type, []):
                        entity_name = existing_entity.get('entity_name', '')
                        entity_id = f"{file_path}::{entity_name}"

                        # If entity exists in DB but not in current parse, it was deleted
                        if entity_name not in current_entities:
                            context.entities_to_delete.append(existing_entity["id"])
                            deleted_entity_ids.add(entity_id)  # Track as deleted, not changed
                            entities_deleted += 1
                            if self.logger:
                                self.logger.debug(f"🗑️ DELETED ENTITY: {entity_id} (no longer in source)")

        if self.logger and entities_deleted > 0:
            self.logger.debug(f"🔄 Git+Meta Enhanced: Found {entities_deleted} deleted entities")

        # Process changed entities (entities that still exist but need replacement)
        # Only delete old chunks for entities that have new chunks to replace them
        processed_entity_ids = set()
        entities_with_new_chunks = set()

        # First pass: collect all entities that have new chunks
        for chunk in chunks_to_process:
            entity_id = f"{chunk.metadata.get('file_path', '')}::{chunk.entity_name}"
            entities_with_new_chunks.add(entity_id)

        # Second pass: only delete old chunks for entities that have replacements
        for chunk in chunks_to_process:
            entity_id = f"{chunk.metadata.get('file_path', '')}::{chunk.entity_name}"

            # Only process entities that:
            # 1. Are marked as changed by Git+Meta
            # 2. Are NOT deleted entities
            # 3. Have new chunks to replace them
            # 4. Haven't been processed yet
            if (entity_id in context.changed_entity_ids and
                entity_id not in deleted_entity_ids and
                entity_id in entities_with_new_chunks and
                entity_id not in processed_entity_ids):

                processed_entity_ids.add(entity_id)

                # Find existing entity with same name/file for replacement
                file_path = chunk.metadata.get("file_path")
                if file_path and hasattr(self.vector_store, 'find_entities_for_file_by_type'):
                    entities_by_type = self.vector_store.find_entities_for_file_by_type(
                        context.collection_name, file_path, ["metadata", "implementation"]
                    )

                    # Delete both metadata AND implementation chunks for THIS specific entity
                    for chunk_type in ["metadata", "implementation"]:
                        for existing_entity in entities_by_type.get(chunk_type, []):
                            if existing_entity.get("entity_name") == chunk.entity_name:
                                context.entities_to_delete.append(existing_entity["id"])
                                entities_deleted += 1
                                if self.logger:
                                    self.logger.debug(f"🔄 ENTITY-LEVEL: Deleting old {chunk_type} {existing_entity['id']} (name: {chunk.entity_name})")

        if self.logger and entities_deleted > 0:
            self.logger.debug(f"🔄 Entity-level replacement: Will delete {entities_deleted} changed entities")

        # Check deduplication AFTER selective deletion
        # Skip deduplication for entities that were just replaced (their old content was deleted)
        chunks_to_check_dedup = []
        chunks_already_processed = []

        for chunk in chunks_to_process:
            entity_id = f"{chunk.metadata.get('file_path', '')}::{chunk.entity_name}"
            if entity_id in processed_entity_ids:
                # This entity was just replaced - don't deduplicate against stale data
                chunks_already_processed.append(chunk)
            else:
                chunks_to_check_dedup.append(chunk)

        # Run deduplication only on chunks that weren't just replaced
        chunks_to_embed_dedup, chunks_to_skip = self.check_deduplication(
            chunks_to_check_dedup, context.collection_name
        )

        # Combine: replaced entities + deduplicated new entities
        chunks_to_embed = chunks_already_processed + chunks_to_embed_dedup

        # Track replaced entities in context for implementation processor
        for chunk in chunks_already_processed:
            entity_id = f"{chunk.metadata.get('file_path', '')}::{chunk.entity_name}"
            context.replaced_entity_ids.add(entity_id)

        # Update changed entity IDs for relation filtering
        for chunk in chunks_to_embed:
            entity_id = f"{chunk.metadata.get('file_path', '')}::{chunk.entity_name}"
            if chunk.metadata.get("file_path"):
                context.changed_entity_ids.add(entity_id)

        # Log efficiency gains
        if chunks_to_skip and self.logger:
            self.logger.info(
                f"⚡ Git+Meta Efficiency: Skipped {len(chunks_to_skip)} unchanged entities (saved {len(chunks_to_skip)} embeddings)"
            )

        if not chunks_to_embed:
            return ProcessingResult.success_result(
                items_processed=len(entities), embeddings_skipped=len(chunks_to_skip)
            )

        # Generate embeddings
        embedding_results, cost_data = self.process_embeddings(
            chunks_to_embed, "entity"
        )

        # Create points
        points, failed_count = self.create_points(
            chunks_to_embed, embedding_results, context.collection_name
        )

        if self.logger:
            self.logger.debug(
                f"🧠 Created {len(points)} entity points from {len(chunks_to_embed)} embedded chunks"
            )
            if failed_count > 0:
                self.logger.warning(
                    f"⚠️ {failed_count} entity embeddings failed and were skipped"
                )

        return ProcessingResult.success_result(
            items_processed=len(entities),
            embeddings_saved=len(points),
            embeddings_skipped=len(chunks_to_skip),
            total_tokens=cost_data["tokens"],
            total_cost=cost_data["cost"],
            total_requests=cost_data["requests"],
            points_created=points,
        )


class RelationProcessor(ContentProcessor):
    """Processor for relation chunks with smart filtering."""

    def process_batch(
        self, relations: list["Relation"], context: ProcessingContext
    ) -> ProcessingResult:
        """Process relations with Git+Meta smart filtering."""
        if not relations:
            return ProcessingResult.success_result()

        if self.logger:
            self.logger.debug(
                f"🔗 Processing relations with Git+Meta smart filtering: {len(relations)} items"
            )

        # Import SmartRelationsProcessor for filtering
        from ..storage.diff_layers import SmartRelationsProcessor

        relations_processor = SmartRelationsProcessor()

        # Apply smart filtering if we have changed entities
        if context.changed_entity_ids:
            relations_to_embed, relations_unchanged = (
                relations_processor.filter_relations_for_changes(
                    relations, context.changed_entity_ids
                )
            )

            if self.logger:
                self.logger.debug(
                    f"🔗 Smart Relations filtering: {len(relations_to_embed)} to embed, {len(relations_unchanged)} unchanged"
                )

            if relations_unchanged:
                self.logger.info(
                    f"⚡ Smart Relations: Skipped {len(relations_unchanged)} unchanged relations (saved {len(relations_unchanged)} embeddings)"
                )

            relations_to_process = relations_to_embed
        else:
            # Fallback: process all relations (initial indexing)
            relations_to_process = relations
            if self.logger:
                self.logger.debug(
                    f"🔗 Initial indexing: Processing all {len(relations_to_process)} relations"
                )

        # Phase 2: Handle TRUE entity-level relation replacement (only for relations that will be re-embedded)
        relations_replaced = 0

        for relation in relations_to_process:
            file_path = getattr(relation, 'file_path', None)
            if (file_path and
                self._should_replace_file_entities(file_path, context)):

                # Only delete relations that will actually be re-embedded
                from ..analysis.entities import RelationChunk
                relation_chunk = RelationChunk.from_relation(relation)
                relation_id = relation_chunk.id
                context.entities_to_delete.append(relation_id)
                relations_replaced += 1

        if self.logger and relations_replaced > 0:
            self.logger.debug(
                f"🔄 TRUE Relation replacement: Will delete {relations_replaced} old relation versions before adding new ones"
            )

        if not relations_to_process:
            return ProcessingResult.success_result(
                items_processed=len(relations),
                embeddings_skipped=len(relations) - len(relations_to_process),
            )

        # Deduplicate relations BEFORE embedding to save API costs
        unique_relations = self._deduplicate_relations(relations_to_process)

        # Generate relation texts for embedding
        relation_texts = [
            self._relation_to_text(relation) for relation in unique_relations
        ]

        if self.logger:
            self.logger.debug(
                f"🔤 Generating embeddings for {len(relation_texts)} unique relation texts"
            )

        # Generate embeddings
        embedding_results, cost_data = self.process_embeddings(
            unique_relations, "relation"
        )

        # Create relation chunk points
        points, failed_count = self.create_points(
            unique_relations,
            embedding_results,
            context.collection_name,
            "create_relation_chunk_point",
        )

        if self.logger:
            self.logger.debug(
                f"🔗 Created {len(points)} relation points from {len(unique_relations)} unique relations"
            )
            if failed_count > 0:
                self.logger.warning(
                    f"⚠️ {failed_count} relation embeddings failed and were skipped"
                )

        return ProcessingResult.success_result(
            items_processed=len(relations),
            embeddings_saved=len(points),
            embeddings_skipped=len(relations) - len(relations_to_process),
            total_tokens=cost_data["tokens"],
            total_cost=cost_data["cost"],
            total_requests=cost_data["requests"],
            points_created=points,
        )

    def _deduplicate_relations(self, relations: list["Relation"]) -> list["Relation"]:
        """Deduplicate relations before embedding to save costs."""
        seen_relation_keys = set()
        unique_relations = []
        duplicate_count = 0
        duplicate_details = {}

        if self.logger:
            self.logger.debug("🔍 === RELATION DEDUPLICATION ===")
            self.logger.debug(f"   Total relations to process: {len(relations)}")

        for relation in relations:
            # Generate the same key that will be used for storage
            from ..analysis.entities import RelationChunk

            relation_chunk = RelationChunk.from_relation(relation)
            relation_key = relation_chunk.id
            import_type = (
                relation.metadata.get("import_type", "none")
                if relation.metadata
                else "none"
            )

            if relation_key not in seen_relation_keys:
                seen_relation_keys.add(relation_key)
                unique_relations.append(relation)
                if self.logger and len(unique_relations) <= 5:
                    self.logger.debug(
                        f"   ✅ Unique: {relation_key} [import_type: {import_type}]"
                    )
            else:
                duplicate_count += 1
                if import_type not in duplicate_details:
                    duplicate_details[import_type] = 0
                duplicate_details[import_type] += 1
                if self.logger and duplicate_count <= 5:
                    self.logger.debug(
                        f"   ❌ Duplicate: {relation_key} [import_type: {import_type}]"
                    )

        if self.logger:
            self.logger.debug(f"   Unique relations: {len(unique_relations)}")
            self.logger.debug(f"   Duplicates removed: {duplicate_count}")
            if duplicate_details:
                self.logger.debug(f"   Duplicates by type: {duplicate_details}")

        return unique_relations

    def _relation_to_text(self, relation: "Relation") -> str:
        """Convert relation to text for embedding."""
        text = f"Relation: {relation.from_entity} {relation.relation_type.value} {relation.to_entity}"

        if relation.context:
            text += f" | Context: {relation.context}"

        return text

    def create_points(
        self,
        items: list,
        embedding_results: list,
        collection_name: str,
        point_creation_method: str = "create_relation_chunk_point",  # noqa: ARG002
    ) -> tuple:
        """Override to handle relation chunk creation."""
        points = []
        failed_count = 0

        for relation, embedding_result in zip(items, embedding_results, strict=False):
            if embedding_result.success:
                # Convert relation to chunk for v2.4 pure architecture
                from ..analysis.entities import RelationChunk

                relation_chunk = RelationChunk.from_relation(relation)
                point = self.vector_store.create_relation_chunk_point(
                    relation_chunk, embedding_result.embedding, collection_name
                )
                points.append(point)
            else:
                failed_count += 1
                if self.logger:
                    error_msg = getattr(embedding_result, "error", "Unknown error")
                    self.logger.warning(
                        f"❌ Relation embedding failed: {relation.from_entity} -> {relation.to_entity} - {error_msg}"
                    )

        return points, failed_count


class ImplementationProcessor(ContentProcessor):
    """Processor for implementation chunks."""

    def process_batch(
        self, implementation_chunks: list["EntityChunk"], context: ProcessingContext
    ) -> ProcessingResult:
        """Process implementation chunks with Git+Meta deduplication."""
        if not implementation_chunks:
            return ProcessingResult.success_result()

        if self.logger:
            self.logger.debug(
                f"💻 Processing implementation chunks with Git+Meta deduplication: {len(implementation_chunks)} items"
            )

        # Check which implementation chunks need embedding
        # Skip deduplication for entities that were just replaced (their old content was deleted)
        chunks_to_check_dedup = []
        chunks_already_processed = []

        for chunk in implementation_chunks:
            entity_id = f"{chunk.metadata.get('file_path', '')}::{chunk.entity_name}"
            if entity_id in context.replaced_entity_ids:
                # This entity was just replaced - don't deduplicate against stale data
                chunks_already_processed.append(chunk)
            else:
                chunks_to_check_dedup.append(chunk)

        # Run deduplication only on chunks that weren't just replaced
        chunks_to_embed_dedup, chunks_to_skip = self.check_deduplication(
            chunks_to_check_dedup, context.collection_name
        )

        # Combine: replaced entities + deduplicated new entities
        chunks_to_embed = chunks_already_processed + chunks_to_embed_dedup

        # Log efficiency gains
        if chunks_to_skip and self.logger:
            self.logger.info(
                f"⚡ Git+Meta Implementation: Skipped {len(chunks_to_skip)} unchanged implementations (saved {len(chunks_to_skip)} embeddings)"
            )

        if not chunks_to_embed:
            return ProcessingResult.success_result(
                items_processed=len(implementation_chunks),
                embeddings_skipped=len(chunks_to_skip),
            )

        # Generate embeddings
        embedding_results, cost_data = self.process_embeddings(
            chunks_to_embed, "implementation"
        )

        # Create points
        points, failed_count = self.create_points(
            chunks_to_embed, embedding_results, context.collection_name
        )

        if self.logger:
            self.logger.debug(
                f"💻 Created {len(points)} implementation points from {len(chunks_to_embed)} embedded chunks"
            )
            if failed_count > 0:
                self.logger.warning(
                    f"⚠️ {failed_count} implementation embeddings failed and were skipped"
                )

        return ProcessingResult.success_result(
            items_processed=len(implementation_chunks),
            embeddings_saved=len(points),
            embeddings_skipped=len(chunks_to_skip),
            total_tokens=cost_data["tokens"],
            total_cost=cost_data["cost"],
            total_requests=cost_data["requests"],
            points_created=points,
        )
