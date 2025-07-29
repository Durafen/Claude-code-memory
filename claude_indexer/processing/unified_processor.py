"""Unified content processor that coordinates all processing phases."""

from typing import TYPE_CHECKING, Any

from .context import ProcessingContext
from .processors import EntityProcessor, ImplementationProcessor, RelationProcessor
from .results import ProcessingResult

if TYPE_CHECKING:
    from ..analysis.entities import Entity, EntityChunk, Relation


class UnifiedContentProcessor:
    """Orchestrates unified content processing pipeline."""

    def __init__(self, vector_store: Any, embedder: Any, logger: Any = None) -> None:
        self.vector_store = vector_store
        self.embedder = embedder
        self.logger = logger

        # Initialize specialized processors
        self.entity_processor = EntityProcessor(vector_store, embedder, logger)
        self.relation_processor = RelationProcessor(vector_store, embedder, logger)
        self.impl_processor = ImplementationProcessor(vector_store, embedder, logger)

    def process_all_content(
        self,
        collection_name: str,
        entities: list["Entity"],
        relations: list["Relation"],
        implementation_chunks: list["EntityChunk"],
        changed_entity_ids: set[str],
    ) -> ProcessingResult:
        """Single entry point replacing _store_vectors() logic."""

        # DEBUG: Print parameters to compare CLI vs Watcher calls
        if self.logger:
            self.logger.debug("🔥 DEBUG process_all_content CALL:")
            self.logger.debug(f"🔥   collection_name: {collection_name}")
            self.logger.debug(f"🔥   entities_count: {len(entities)}")
            self.logger.debug(f"🔥   relations_count: {len(relations)}")
            self.logger.debug(f"🔥   implementation_chunks_count: {len(implementation_chunks)}")
            self.logger.debug(f"🔥   changed_entity_ids_count: {len(changed_entity_ids)}")

            # Check if collection has existing data
            try:
                if hasattr(self.vector_store, "backend"):
                    qdrant_client = self.vector_store.backend.client
                else:
                    qdrant_client = self.vector_store.client

                total_points = qdrant_client.count(collection_name).count
                self.logger.debug(f"🔥   existing_points_in_collection: {total_points}")
            except Exception as e:
                self.logger.debug(f"🔥   existing_points_check_failed: {e}")

        # Phase 1: Identify files being processed
        files_being_processed = set()
        if entities:
            files_being_processed.update(entity.file_path for entity in entities if entity.file_path)
        if relations:
            files_being_processed.update(getattr(relation, 'file_path', None) for relation in relations if getattr(relation, 'file_path', None))
        if implementation_chunks:
            files_being_processed.update(getattr(chunk, 'file_path', None) for chunk in implementation_chunks if getattr(chunk, 'file_path', None))

        # Remove None values
        files_being_processed.discard(None)

        # Create implementation chunk lookup for has_implementation flags
        implementation_entity_names = set()
        if implementation_chunks:
            implementation_entity_names = {
                chunk.entity_name for chunk in implementation_chunks
            }
        
        # DEBUG: Track implementation_entity_names population
        if self.logger:
            self.logger.debug(f"🔍 UNIFIED PROCESSOR DEBUG:")
            self.logger.debug(f"🔍   implementation_chunks count: {len(implementation_chunks) if implementation_chunks else 0}")
            self.logger.debug(f"🔍   implementation_entity_names: {sorted(implementation_entity_names)}")
            self.logger.debug(f"🔍   entities count: {len(entities)}")
            import_entities = [e for e in entities if hasattr(e, 'entity_type') and e.entity_type.value == 'import']
            self.logger.debug(f"🔍   import_entities: {[e.name for e in import_entities]}")

        # Phase 2: Create enhanced processing context
        context = ProcessingContext(
            collection_name=collection_name,
            changed_entity_ids=changed_entity_ids,
            implementation_entity_names=implementation_entity_names,
            files_being_processed=files_being_processed,
            entities_to_delete=[],
            replacement_mode=True,
        )

        # Debug logging for context
        if self.logger:
            self.logger.debug(f"🔍 DEBUG: context.files_being_processed = {context.files_being_processed}")
            self.logger.debug(f"🔍 DEBUG: context.replacement_mode = {context.replacement_mode}")

        all_points: list[Any] = []
        combined_result = ProcessingResult.success_result()

        try:
            # Phase 1: Process entities
            if entities:
                entity_result = self.entity_processor.process_batch(entities, context)
                if not entity_result.success:
                    return entity_result
                combined_result = combined_result.combine_with(entity_result)
                all_points.extend(entity_result.points_created or [])

            # Phase 2: Process relations (with smart filtering based on changed entities)
            if relations:
                relation_result = self.relation_processor.process_batch(
                    relations, context
                )
                if not relation_result.success:
                    return relation_result
                combined_result = combined_result.combine_with(relation_result)
                all_points.extend(relation_result.points_created or [])

            # Phase 3: Process implementation chunks
            if implementation_chunks:
                impl_result = self.impl_processor.process_batch(
                    implementation_chunks, context
                )
                if not impl_result.success:
                    return impl_result
                combined_result = combined_result.combine_with(impl_result)
                all_points.extend(impl_result.points_created or [])

            # Phase 4: Execute deletion + upsert in single transaction
            if context.entities_to_delete or all_points:
                # Execute deletion before upsert if entities need to be replaced
                if context.entities_to_delete:
                    deletion_result = self._delete_entities_batch(collection_name, context.entities_to_delete)
                    if not deletion_result:
                        return ProcessingResult.failure_result(
                            "Failed to delete existing entities for replacement"
                        )

                # Execute upsert for new/updated entities
                if all_points:
                    storage_result = self._reliable_batch_upsert(collection_name, all_points)
                    if not storage_result:
                        return ProcessingResult.failure_result(
                            "Failed to store points in batch operation"
                        )

                # Phase 5: Enhanced orphan cleanup after successful storage
                try:
                    self._cleanup_orphaned_relations(collection_name)
                except Exception as cleanup_error:
                    if self.logger:
                        self.logger.warning(
                            f"⚠️ Orphan cleanup failed but storage succeeded: {cleanup_error}"
                        )

            # Update combined result with final metrics
            combined_result.points_created = all_points
            return combined_result

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error in unified content processing: {e}")
            return ProcessingResult.failure_result(f"Processing failed: {e}")

    def _batch_store_points(self, all_points: list, collection_name: str) -> bool:
        """Store all points in batch with detailed logging."""
        if self.logger:
            self.logger.debug("💾 === FINAL STORAGE SUMMARY ===")
            self.logger.debug(f"   Collection: {collection_name}")
            self.logger.info(f"   Total points to store: {len(all_points)}")

            # Count different types of points
            entity_points = sum(
                1
                for p in all_points
                if p.payload.get("chunk_type") == "metadata"
                and p.payload.get("entity_type") != "relation"
            )
            relation_points = sum(
                1 for p in all_points if p.payload.get("chunk_type") == "relation"
            )
            impl_points = sum(
                1 for p in all_points if p.payload.get("chunk_type") == "implementation"
            )

            self.logger.debug(f"   - Entity metadata: {entity_points}")
            self.logger.debug(f"   - Relations: {relation_points}")
            self.logger.debug(f"   - Implementations: {impl_points}")

        result = self.vector_store.batch_upsert(collection_name, all_points)

        if self.logger:
            if result.success:
                self.logger.debug(
                    f"✅ Successfully stored {result.items_processed} points (attempted: {len(all_points)})"
                )
                if result.items_processed < len(all_points):
                    self.logger.warning(
                        f"⚠️ Storage discrepancy: {len(all_points) - result.items_processed} points not stored"
                    )
            else:
                self.logger.error(
                    f"❌ Failed to store points: {getattr(result, 'errors', 'Unknown error')}"
                )

        return bool(result.success)

    def _cleanup_orphaned_relations(self, collection_name: str) -> None:
        """Clean up orphaned relations after successful storage with timer control."""
        backend = getattr(self.vector_store, "backend", self.vector_store)

        if self.logger:
            self.logger.debug(
                "🔍 DEBUG: Starting comprehensive orphan cleanup after successful storage"
            )

        # Phase 1: Enhanced hash-based orphan cleanup (existing logic)
        if hasattr(backend, "_should_run_cleanup") and not backend._should_run_cleanup(
            collection_name
        ):
            if self.logger:
                self.logger.debug(
                    "⏱️ Hash-based cleanup skipped - timer interval not elapsed"
                )
        else:
            from ..storage.diff_layers import EnhancedOrphanCleanup

            cleanup = EnhancedOrphanCleanup(backend.client)
            orphaned_count = cleanup.cleanup_hash_orphaned_relations(collection_name)

            if self.logger:
                self.logger.debug(
                    f"🔍 DEBUG: EnhancedOrphanCleanup returned count: {orphaned_count}"
                )
                if orphaned_count > 0:
                    self.logger.info(
                        f"🧹 Cleaned {orphaned_count} orphaned relations after hash changes"
                    )

            # Update timestamp after successful cleanup
            if hasattr(backend, "_update_cleanup_timestamp") and orphaned_count >= 0:
                backend._update_cleanup_timestamp(collection_name)

        # Phase 2: Force standard orphan cleanup for phantom relations (NEW)
        # This bypasses the timer to catch phantom relations from incremental updates
        if hasattr(backend, "_cleanup_orphaned_relations"):
            phantom_count = backend._cleanup_orphaned_relations(
                collection_name, verbose=True, force=True
            )
            if self.logger and phantom_count > 0:
                self.logger.info(
                    f"🧹 PHANTOM FIX: Cleaned {phantom_count} phantom relations during incremental update"
                )

    def _delete_entities_batch(self, collection_name: str, entity_ids: list[str]) -> bool:
        """Delete entities in batch before upsert."""
        if not entity_ids:
            return True

        try:
            if self.logger:
                self.logger.debug(f"🗑️ DEBUG: About to delete {len(entity_ids)} entities from {collection_name}")
                for i, entity_id in enumerate(entity_ids[:5]):  # Show first 5
                    self.logger.debug(f"🗑️ DEBUG: Entity {i+1}: {entity_id}")

            # Handle both string and integer entity IDs
            integer_ids = []
            for entity_id in entity_ids:
                if isinstance(entity_id, int):
                    integer_ids.append(entity_id)
                else:
                    integer_ids.append(self.vector_store.generate_deterministic_id(entity_id))

            if self.logger:
                self.logger.debug("🗑️ DEBUG: Converted to integer IDs:")
                for _i, (str_id, int_id) in enumerate(zip(entity_ids[:5], integer_ids[:5], strict=False)):
                    self.logger.debug(f"🗑️ DEBUG: {str_id} → {int_id}")

            from qdrant_client.models import PointIdsList

            # Verify entities exist before deletion
            if self.logger:
                existing_count = self.vector_store.client.count(collection_name=collection_name).count
                self.logger.debug(f"🗑️ DEBUG: Collection {collection_name} has {existing_count} points before deletion")

            # Perform deletion
            delete_result = self.vector_store.client.delete(
                collection_name=collection_name,
                points_selector=PointIdsList(points=integer_ids)
            )

            if self.logger:
                self.logger.debug(f"🗑️ DEBUG: Qdrant delete result: {delete_result}")
                # Check count after deletion
                remaining_count = self.vector_store.client.count(collection_name=collection_name).count
                self.logger.debug(f"🗑️ DEBUG: Collection {collection_name} has {remaining_count} points after deletion (reduced by {existing_count - remaining_count})")

            if self.logger:
                self.logger.debug(f"Deleted {len(entity_ids)} existing entities for replacement")

            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Failed to delete entities: {e}")
                import traceback
                self.logger.error(f"❌ Deletion traceback: {traceback.format_exc()}")
            return False

    def _reliable_batch_upsert(self, collection_name: str, points: list[Any]) -> bool:
        """Perform reliable batch upsert with verification."""
        if not points:
            return True

        try:
            # Use existing reliable batch upsert from vector store if available
            if hasattr(self.vector_store, 'batch_upsert'):
                result = self.vector_store.batch_upsert(collection_name, points)
                return bool(result.success)
            else:
                # Fallback to simple upsert
                self.vector_store.client.upsert(
                    collection_name=collection_name,
                    points=points,
                    wait=True  # Ensure synchronous operation
                )
                return True

        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Batch upsert failed: {e}")
            return False
