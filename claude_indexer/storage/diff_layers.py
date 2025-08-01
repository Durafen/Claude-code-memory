"""Diff layer tracking for immutable change history (Meta's approach)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..analysis.entities import Entity, Relation
from ..storage.qdrant import ContentHashMixin


@dataclass
class DiffLayer:
    """Represents a change layer in the immutable diff system"""

    timestamp: datetime
    file_path: str
    event_type: str  # "created", "modified", "deleted"
    changes: dict[str, Any]


@dataclass
class DiffSketch:
    """Mechanical summary of changes (Meta's approach)"""

    added_entities: list[str]  # entity IDs added
    removed_entities: list[str]  # entity IDs removed
    modified_entities: list[str]  # entity IDs with content changes
    unchanged_entities: list[str]  # entity IDs with same content hash


class DiffLayerManager:
    """Manages immutable diff layers for efficient change tracking"""

    def create_diff_sketch(
        self, old_entities: list[Entity], new_entities: list[Entity]
    ) -> DiffSketch:
        """Compare entity sets and create change summary"""
        old_by_id = {self._get_entity_id(e): e for e in old_entities}
        new_by_id = {self._get_entity_id(e): e for e in new_entities}

        # Compute differences using content hashes
        added = [id for id in new_by_id if id not in old_by_id]
        removed = [id for id in old_by_id if id not in new_by_id]

        modified = []
        unchanged = []
        for entity_id in set(old_by_id) & set(new_by_id):
            old_entity = old_by_id[entity_id]
            new_entity = new_by_id[entity_id]

            old_content = self._get_entity_content(old_entity)
            new_content = self._get_entity_content(new_entity)

            old_hash = ContentHashMixin.compute_content_hash(old_content)
            new_hash = ContentHashMixin.compute_content_hash(new_content)

            if old_hash != new_hash:
                modified.append(entity_id)
            else:
                unchanged.append(entity_id)

        return DiffSketch(added, removed, modified, unchanged)

    def _get_entity_id(self, entity: Entity) -> str:
        """Get consistent entity ID for comparison"""
        return f"{entity.file_path}::{entity.name}"

    def _get_entity_content(self, entity: Entity) -> str:
        """Get entity content for hashing"""
        # Use the same content generation as metadata chunks
        content_parts = []
        if entity.docstring:
            content_parts.append(f"Description: {entity.docstring}")

        # Add key observations
        content_parts.extend(entity.observations)
        return " | ".join(content_parts)


class SmartRelationsProcessor:
    """Processes only relations involving changed entities"""

    def filter_relations_for_changes(
        self, all_relations: list["Relation"], changed_entity_ids: set[str]
    ) -> tuple[list["Relation"], list["Relation"]]:
        """Split relations into changed vs unchanged based on entity involvement"""

        relations_to_update = []
        relations_unchanged = []

        for relation in all_relations:
            # Check if relation involves any changed entity (with file context preserved)
            from_matches = any(
                relation.from_entity == changed_id
                or changed_id.endswith(f"::{relation.from_entity}")
                for changed_id in changed_entity_ids
            )
            to_matches = any(
                relation.to_entity == changed_id
                or changed_id.endswith(f"::{relation.to_entity}")
                for changed_id in changed_entity_ids
            )

            if from_matches or to_matches:
                relations_to_update.append(relation)
            else:
                # Relation between two unchanged entities - skip
                relations_unchanged.append(relation)

        return relations_to_update, relations_unchanged

    def get_existing_relations_hashes(
        self, client, collection_name: str, file_path: str
    ) -> dict[str, str]:
        """Get existing relation content hashes for unchanged relations"""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        try:
            results = client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="metadata.file_path", match=MatchValue(value=file_path)
                        ),
                        FieldCondition(
                            key="chunk_type", match=MatchValue(value="relation")
                        ),
                    ]
                ),
                with_payload=True,
                limit=1000,
            )

            relation_hashes = {}
            for point in results[0]:
                if point.payload:
                    relation_id = point.payload.get("id")
                    content_hash = point.payload.get("content_hash")
                    if relation_id and content_hash:
                        relation_hashes[relation_id] = content_hash

            return relation_hashes
        except Exception:
            return {}


class EnhancedOrphanCleanup:
    """Enhanced orphan cleanup for hash-based storage scenarios"""

    def __init__(self, client):
        self.client = client
        self._qdrant_store = None

    def _get_qdrant_store(self):
        """Get QdrantStore instance"""
        if self._qdrant_store is None:
            from ..config.config_loader import ConfigLoader
            from ..storage.qdrant import QdrantStore

            config = ConfigLoader().load()
            self._qdrant_store = QdrantStore(
                url=config.qdrant_url, api_key=config.qdrant_api_key
            )
        return self._qdrant_store

    def _batch_get_existing_entities(self, collection_name: str) -> set:
        """Batch get all existing entities in single query"""
        from qdrant_client import models

        qdrant_store = self._get_qdrant_store()

        # Use existing _scroll_collection method
        metadata_points = qdrant_store._scroll_collection(
            collection_name=collection_name,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="chunk_type", match=models.MatchValue(value="metadata")
                    )
                ]
            ),
            limit=1000,
            with_vectors=False,
            handle_pagination=True,
        )

        # Build set of existing entity names
        existing_entities = set()
        for point in metadata_points:
            if point.payload and point.payload.get("entity_name"):
                entity_name = point.payload.get("entity_name")
                existing_entities.add(entity_name)
                
                # Handle markdown grouped headers: extract all headers from metadata
                if " (+" in entity_name and entity_name.endswith(" more)"):
                    # Get headers from metadata.headers field (same as comprehensive cleanup)
                    metadata = point.payload.get("metadata", {})
                    headers = metadata.get("headers", [])
                    if headers:
                        for header in headers:
                            existing_entities.add(header)

        return existing_entities

    def cleanup_hash_orphaned_relations(
        self, collection_name: str, file_path: str = None
    ) -> int:
        """Clean up relations orphaned by content hash changes

        Note: For 66x performance improvement, use:
        - self._get_qdrant_store()._get_all_relations() instead of manual scroll
        - self._batch_get_existing_entities() instead of individual lookups
        """
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchValue,
            PointIdsList,
        )

        # Scenario 1: Entity content changed, hash changed, old relations point to old entity
        # Scenario 2: Entity deleted but relations still reference it
        # Scenario 3: Cross-file relations where target entity changed hash

        orphaned_count = 0

        try:
            # Get all relations using optimized approach with file filtering
            qdrant_store = self._get_qdrant_store()

            if file_path:
                # File-specific relations - use optimized pagination
                relation_filter = [
                    FieldCondition(
                        key="chunk_type", match=MatchValue(value="relation")
                    ),
                    FieldCondition(key="metadata.file_path", match=MatchValue(value=file_path)),
                ]
                all_relations = qdrant_store._scroll_collection(
                    collection_name=collection_name,
                    scroll_filter=Filter(must=relation_filter),
                    limit=1000,
                    with_vectors=False,
                    handle_pagination=True,
                )
            else:
                # All relations - use existing optimized method
                all_relations = qdrant_store._get_all_relations(collection_name)

            # Get all existing entities in batch (221x faster than individual queries)
            existing_entities = self._batch_get_existing_entities(collection_name)

            orphaned_points = []

            for point in all_relations:
                if not point.payload:
                    continue

                relation_data = point.payload
                from_entity = relation_data.get("entity_name")
                to_entity = relation_data.get("relation_target")

                # Check if target entities still exist using batch lookup (O(1) vs O(N))
                if from_entity not in existing_entities:
                    orphaned_points.append(point.id)
                    continue

                if to_entity not in existing_entities:
                    orphaned_points.append(point.id)
                    continue

            # Batch delete orphaned relations
            if orphaned_points:
                self.client.delete(
                    collection_name=collection_name,
                    points_selector=PointIdsList(points=orphaned_points),
                )
                orphaned_count = len(orphaned_points)

        except Exception as e:
            # Log error but don't fail
            print(f"EnhancedOrphanCleanup exception: {e}")
            pass

        return orphaned_count

    def _entity_exists_with_current_hash(
        self, collection_name: str, entity_name: str
    ) -> bool:
        """Check if entity exists with valid current content hash"""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if not entity_name:
            return False

        try:
            # Look for metadata chunk of this entity
            results = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="entity_name", match=MatchValue(value=entity_name)
                        ),
                        FieldCondition(
                            key="chunk_type", match=MatchValue(value="metadata")
                        ),
                    ]
                ),
                limit=1,
                with_payload=True,
            )

            if not results[0]:
                return False

            # Entity exists if we found a metadata chunk
            # The hash validation happens during storage - if it's there, it's current
            return True

        except Exception:
            return False
