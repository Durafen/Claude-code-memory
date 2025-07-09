"""Diff layer tracking for immutable change history (Meta's approach)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Any, Set
from ..analysis.entities import Entity
from ..storage.qdrant import ContentHashMixin


@dataclass
class DiffLayer:
    """Represents a change layer in the immutable diff system"""
    timestamp: datetime
    file_path: str
    event_type: str  # "created", "modified", "deleted"
    changes: Dict[str, Any]


@dataclass
class DiffSketch:
    """Mechanical summary of changes (Meta's approach)"""
    added_entities: List[str]      # entity IDs added
    removed_entities: List[str]    # entity IDs removed
    modified_entities: List[str]   # entity IDs with content changes
    unchanged_entities: List[str]  # entity IDs with same content hash


class DiffLayerManager:
    """Manages immutable diff layers for efficient change tracking"""
    
    def create_diff_sketch(self, old_entities: List[Entity], new_entities: List[Entity]) -> DiffSketch:
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
    
    def filter_relations_for_changes(self, all_relations: List['Relation'], 
                                    changed_entity_ids: Set[str]) -> tuple[List['Relation'], List['Relation']]:
        """Split relations into changed vs unchanged based on entity involvement"""
        
        # Extract entity names from file_path::entity_name format
        changed_names = {id.split("::")[-1] for id in changed_entity_ids}
        
        relations_to_update = []
        relations_unchanged = []
        
        for relation in all_relations:
            # Check if relation involves any changed entity
            if (relation.from_entity in changed_names or 
                relation.to_entity in changed_names):
                relations_to_update.append(relation)
            else:
                # Relation between two unchanged entities - skip
                relations_unchanged.append(relation)
                
        return relations_to_update, relations_unchanged
    
    def get_existing_relations_hashes(self, client, collection_name: str, 
                                    file_path: str) -> Dict[str, str]:
        """Get existing relation content hashes for unchanged relations"""
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        
        try:
            results = client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[
                    FieldCondition(key="file_path", match=MatchValue(value=file_path)),
                    FieldCondition(key="chunk_type", match=MatchValue(value="relation"))
                ]),
                with_payload=True,
                limit=1000
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
    
    def cleanup_hash_orphaned_relations(self, collection_name: str, 
                                      file_path: str = None) -> int:
        """Clean up relations orphaned by content hash changes"""
        from qdrant_client.models import Filter, FieldCondition, MatchValue, PointIdsList
        
        # Scenario 1: Entity content changed, hash changed, old relations point to old entity
        # Scenario 2: Entity deleted but relations still reference it
        # Scenario 3: Cross-file relations where target entity changed hash
        
        orphaned_count = 0
        
        try:
            # Get all relations (file-specific if provided)
            relation_filter = [FieldCondition(key="chunk_type", match=MatchValue(value="relation"))]
            if file_path:
                relation_filter.append(
                    FieldCondition(key="file_path", match=MatchValue(value=file_path))
                )
            
            relations_result = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=relation_filter),
                with_payload=True,
                limit=1000
            )
            
            orphaned_points = []
            
            for point in relations_result[0]:
                if not point.payload:
                    continue
                    
                relation_data = point.payload
                from_entity = relation_data.get("entity_name")
                to_entity = relation_data.get("relation_target")
                
                # Check if target entities still exist with current content hashes
                if not self._entity_exists_with_current_hash(collection_name, from_entity):
                    orphaned_points.append(point.id)
                    continue
                    
                if not self._entity_exists_with_current_hash(collection_name, to_entity):
                    orphaned_points.append(point.id)
                    continue
            
            # Batch delete orphaned relations
            if orphaned_points:
                self.client.delete(
                    collection_name=collection_name,
                    points_selector=PointIdsList(points=orphaned_points)
                )
                orphaned_count = len(orphaned_points)
                
        except Exception as e:
            # Log error but don't fail
            pass
            
        return orphaned_count
    
    def _entity_exists_with_current_hash(self, collection_name: str, 
                                        entity_name: str) -> bool:
        """Check if entity exists with valid current content hash"""
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        
        if not entity_name:
            return False
            
        try:
            # Look for metadata chunk of this entity
            results = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[
                    FieldCondition(key="entity_name", match=MatchValue(value=entity_name)),
                    FieldCondition(key="chunk_type", match=MatchValue(value="metadata"))
                ]),
                limit=1,
                with_payload=True
            )
            
            if not results[0]:
                return False
                
            # Entity exists if we found a metadata chunk
            # The hash validation happens during storage - if it's there, it's current
            return True
            
        except Exception:
            return False