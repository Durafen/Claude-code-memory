"""Qdrant vector store implementation."""

import time
import warnings
from typing import List, Dict, Any, Optional, Union
from .base import VectorStore, StorageResult, VectorPoint, ManagedVectorStore
from ..indexer_logging import get_logger

logger = get_logger()

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, IsNullCondition, PayloadField
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False
    # Create mock classes for development
    class Distance:
        COSINE = "cosine"
        EUCLID = "euclidean" 
        DOT = "dot"
    
    class QdrantClient:
        pass
    
    class VectorParams:
        pass
    
    class PointStruct:
        pass
    
    class Filter:
        pass
    
    class FieldCondition:
        pass
    
    class MatchValue:
        pass
    
    class IsNullCondition:
        pass
    
    class PayloadField:
        pass


class QdrantStore(ManagedVectorStore):
    """Qdrant vector database implementation."""
    
    DISTANCE_METRICS = {
        "cosine": Distance.COSINE,
        "euclidean": Distance.EUCLID,
        "dot": Distance.DOT
    }
    
    def __init__(self, url: str = "http://localhost:6333", api_key: str = None,
                 timeout: float = 60.0, auto_create_collections: bool = True, **kwargs):
        
        if not QDRANT_AVAILABLE:
            raise ImportError("Qdrant client not available. Install with: pip install qdrant-client")
        
        super().__init__(auto_create_collections=auto_create_collections)
        
        self.url = url
        self.api_key = api_key
        self.timeout = timeout
        
        # Initialize client
        try:
            # Suppress insecure connection warning for development
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")
                self.client = QdrantClient(
                    url=url,
                    api_key=api_key,
                    timeout=timeout
                )
            # Test connection
            self.client.get_collections()
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Qdrant at {url}: {e}")
    
    def create_collection(self, collection_name: str, vector_size: int, 
                         distance_metric: str = "cosine") -> StorageResult:
        """Create a new Qdrant collection."""
        start_time = time.time()
        
        try:
            if distance_metric not in self.DISTANCE_METRICS:
                available = list(self.DISTANCE_METRICS.keys())
                return StorageResult(
                    success=False,
                    operation="create_collection",
                    processing_time=time.time() - start_time,
                    errors=[f"Invalid distance metric: {distance_metric}. Available: {available}"]
                )
            
            distance = self.DISTANCE_METRICS[distance_metric]
            
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vector_size, distance=distance),
                optimizers_config={
                    "indexing_threshold": 100
                }
            )
            
            return StorageResult(
                success=True,
                operation="create_collection",
                items_processed=1,
                processing_time=time.time() - start_time
            )
            
        except Exception as e:
            return StorageResult(
                success=False,
                operation="create_collection",
                processing_time=time.time() - start_time,
                errors=[f"Failed to create collection {collection_name}: {e}"]
            )
    
    def collection_exists(self, collection_name: str) -> bool:
        """Check if collection exists."""
        try:
            collections = self.client.get_collections()
            return any(col.name == collection_name for col in collections.collections)
        except Exception:
            return False
    
    def delete_collection(self, collection_name: str) -> StorageResult:
        """Delete a collection."""
        start_time = time.time()
        
        try:
            self.client.delete_collection(collection_name=collection_name)
            
            return StorageResult(
                success=True,
                operation="delete_collection",
                items_processed=1,
                processing_time=time.time() - start_time
            )
            
        except Exception as e:
            return StorageResult(
                success=False,
                operation="delete_collection",
                processing_time=time.time() - start_time,
                errors=[f"Failed to delete collection {collection_name}: {e}"]
            )
    
    def upsert_points(self, collection_name: str, points: List[VectorPoint]) -> StorageResult:
        """Insert or update points in the collection."""
        start_time = time.time()
        
        if not points:
            return StorageResult(
                success=True,
                operation="upsert",
                items_processed=0,
                processing_time=time.time() - start_time
            )
        
        try:
            # Ensure collection exists
            if not self.ensure_collection(collection_name, len(points[0].vector)):
                return StorageResult(
                    success=False,
                    operation="upsert",
                    processing_time=time.time() - start_time,
                    errors=[f"Collection {collection_name} does not exist"]
                )
            
            # Convert to Qdrant points
            qdrant_points = []
            for point in points:
                qdrant_point = PointStruct(
                    id=point.id,
                    vector=point.vector,
                    payload=point.payload
                )
                qdrant_points.append(qdrant_point)
            
            # Upsert to Qdrant
            self.client.upsert(
                collection_name=collection_name,
                points=qdrant_points
            )
            
            return StorageResult(
                success=True,
                operation="upsert",
                items_processed=len(points),
                processing_time=time.time() - start_time
            )
            
        except Exception as e:
            return StorageResult(
                success=False,
                operation="upsert",
                items_failed=len(points),
                processing_time=time.time() - start_time,
                errors=[f"Failed to upsert points: {e}"]
            )
    
    def delete_points(self, collection_name: str, point_ids: List[Union[str, int]]) -> StorageResult:
        """Delete points by their IDs."""
        start_time = time.time()
        
        try:
            delete_response = self.client.delete(
                collection_name=collection_name,
                points_selector=point_ids
            )
            
            return StorageResult(
                success=True,
                operation="delete",
                items_processed=len(point_ids),
                processing_time=time.time() - start_time
            )
            
        except Exception as e:
            logger.error(f"❌ Exception in delete_points: {e}")
            return StorageResult(
                success=False,
                operation="delete",
                items_failed=len(point_ids),
                processing_time=time.time() - start_time,
                errors=[f"Failed to delete points: {e}"]
            )
    
    def search_similar(self, collection_name: str, query_vector: List[float],
                      limit: int = 10, score_threshold: float = 0.0,
                      filter_conditions: Dict[str, Any] = None) -> StorageResult:
        """Search for similar vectors."""
        start_time = time.time()
        
        try:
            # Build filter if provided
            query_filter = None
            if filter_conditions:
                query_filter = self._build_filter(filter_conditions)
                logger.debug(f"🔍 search_similar debug:")
                logger.debug(f"   Collection: {collection_name}")
                logger.debug(f"   Filter conditions: {filter_conditions}")
                logger.debug(f"   Query filter: {query_filter}")
                logger.debug(f"   Limit: {limit}, Score threshold: {score_threshold}")
            
            # Perform search
            search_results = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=limit,
                score_threshold=score_threshold,
                query_filter=query_filter
            )
            
            if filter_conditions:
                logger.debug(f"   Raw search results count: {len(search_results)}")
                for i, result in enumerate(search_results):
                    logger.debug(f"   Result {i}: ID={result.id}, score={result.score}")
                    logger.debug(f"      Payload: {result.payload}")
            
            # Convert results
            results = []
            for result in search_results:
                results.append({
                    "id": result.id,
                    "score": result.score,
                    "payload": result.payload
                })
            
            return StorageResult(
                success=True,
                operation="search",
                processing_time=time.time() - start_time,
                results=results,
                total_found=len(results)
            )
            
        except Exception as e:
            logger.debug(f"❌ search_similar exception: {e}")
            return StorageResult(
                success=False,
                operation="search",
                processing_time=time.time() - start_time,
                errors=[f"Search failed: {e}"]
            )
    
    def _build_filter(self, filter_conditions: Dict[str, Any]) -> Filter:
        """Build Qdrant filter from conditions."""
        conditions = []
        
        for field, value in filter_conditions.items():
            if isinstance(value, (str, int, float, bool)):
                condition = FieldCondition(
                    key=field,
                    match=MatchValue(value=value)
                )
                conditions.append(condition)
        
        return Filter(must=conditions) if conditions else None
    
    def get_collection_info(self, collection_name: str) -> Dict[str, Any]:
        """Get information about a collection."""
        try:
            collection_info = self.client.get_collection(collection_name)
            
            return {
                "name": collection_name,
                "status": collection_info.status.value,
                "vector_size": collection_info.config.params.vectors.size,
                "distance_metric": collection_info.config.params.vectors.distance.value,
                "points_count": collection_info.points_count,
                "indexed_vectors_count": collection_info.indexed_vectors_count,
                "segments_count": collection_info.segments_count
            }
            
        except Exception as e:
            return {
                "name": collection_name,
                "error": str(e)
            }
    
    def count(self, collection_name: str) -> int:
        """Count total points in collection - test compatibility method."""
        try:
            collection_info = self.client.get_collection(collection_name)
            return collection_info.points_count
        except Exception:
            return 0
    
    def search(self, collection_name: str, query_vector, top_k: int = 10):
        """Legacy search interface for test compatibility."""
        try:
            if hasattr(query_vector, 'tolist'):
                query_vector = query_vector.tolist()
            elif isinstance(query_vector, list):
                pass
            else:
                query_vector = list(query_vector)
                
            search_results = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=top_k
            )
            
            # Return results in expected format for tests
            class SearchHit:
                def __init__(self, id, score, payload):
                    self.id = id
                    self.score = score
                    self.payload = payload
            
            return [SearchHit(result.id, result.score, result.payload) for result in search_results]
            
        except Exception as e:
            logger.debug(f"Search failed: {e}")
            return []
    
    def list_collections(self) -> List[str]:
        """List all collections."""
        try:
            collections = self.client.get_collections()
            return [col.name for col in collections.collections]
        except Exception:
            return []
    
    def _scroll_collection(
        self, 
        collection_name: str,
        scroll_filter: Optional[Any] = None,
        limit: int = 1000,
        with_vectors: bool = False,
        handle_pagination: bool = True
    ) -> List[Any]:
        """
        Unified scroll method for retrieving points from a collection.
        
        Args:
            collection_name: Name of the collection to scroll
            scroll_filter: Optional filter to apply during scrolling
            limit: Maximum number of points per page (default: 1000)
            with_vectors: Whether to include vectors in results (default: False)
            handle_pagination: If True, retrieves all pages; if False, only first page
            
        Returns:
            List of points matching the criteria
        """
        try:
            all_points = []
            offset = None
            seen_offsets = set()  # Track seen offsets to prevent infinite loops
            max_iterations = 1000  # Safety limit to prevent runaway loops
            iteration = 0
            
            logger.debug(f"Starting scroll operation for collection {collection_name}, limit={limit}, handle_pagination={handle_pagination}")
            
            while True:
                iteration += 1
                
                # Safety check: prevent infinite loops with iteration limit
                if iteration > max_iterations:
                    logger.warning(f"Scroll operation hit max iterations ({max_iterations}) for collection {collection_name}")
                    break
                
                logger.debug(f"Scroll iteration {iteration}, offset={offset}")
                
                scroll_result = self.client.scroll(
                    collection_name=collection_name,
                    scroll_filter=scroll_filter,
                    limit=limit,
                    offset=offset,
                    with_payload=True,
                    with_vectors=with_vectors
                )
                
                points, next_offset = scroll_result
                all_points.extend(points)
                
                logger.debug(f"Retrieved {len(points)} points, next_offset={next_offset}, total_points={len(all_points)}")
                
                # Handle pagination if requested and more results exist
                if handle_pagination and next_offset is not None:
                    # CRITICAL FIX: Infinite loop protection - check if we've seen this offset before
                    offset_key = str(next_offset)  # Convert to string for set membership
                    if offset_key in seen_offsets:
                        logger.warning(f"Detected offset loop in collection {collection_name} at iteration {iteration}. "
                                     f"Offset {next_offset} already seen. Breaking pagination to prevent infinite loop.")
                        break
                    
                    seen_offsets.add(offset_key)
                    offset = next_offset
                    logger.debug(f"Advancing to next page with offset {next_offset}")
                else:
                    logger.debug(f"Pagination complete: handle_pagination={handle_pagination}, next_offset={next_offset}")
                    break
                    
            logger.debug(f"Scroll operation completed for collection {collection_name}: "
                        f"{len(all_points)} total points retrieved in {iteration} iterations")
            return all_points
            
        except Exception as e:
            # Check if collection doesn't exist
            if "doesn't exist" in str(e) or "Not found" in str(e):
                logger.warning(f"Collection '{collection_name}' doesn't exist - returning empty result")
                return []
            # Log error and return empty list
            logger.error(f"Error in _scroll_collection for {collection_name}: {e}")
            return []
    
    def clear_collection(self, collection_name: str, preserve_manual: bool = True) -> StorageResult:
        """Clear collection data. By default, preserves manually-added memories.
        
        Args:
            collection_name: Name of the collection
            preserve_manual: If True, only delete auto-generated memories (entities with file_path or relations with from/to/relationType)
        """
        start_time = time.time()
        
        try:
            # Check if collection exists
            if not self.collection_exists(collection_name):
                return StorageResult(
                    success=True,
                    operation="clear_collection",
                    processing_time=time.time() - start_time,
                    warnings=[f"Collection {collection_name} doesn't exist - nothing to clear"]
                )
            
            if preserve_manual:
                # Delete only auto-generated memories (entities with file_path or relations)
                from qdrant_client import models
                
                # Count points before deletion for reporting
                count_before = self.client.count(collection_name=collection_name).count
                
                # Get all points to identify auto-generated content
                # Use helper to get all points with pagination
                all_points = self._scroll_collection(
                    collection_name=collection_name,
                    limit=10000,  # Large page size for efficiency
                    with_vectors=False,
                    handle_pagination=True
                )
                
                # Find points that are auto-generated (code-indexed entities or relations)
                auto_generated_ids = []
                for point in all_points:
                    # Auto-generated entities have file_path
                    if 'file_path' in point.payload and point.payload['file_path']:
                        auto_generated_ids.append(point.id)
                    # Auto-generated relations have from/to/relationType structure
                    elif ('from' in point.payload and 'to' in point.payload and 
                          'relationType' in point.payload):
                        auto_generated_ids.append(point.id)
                
                # Delete auto-generated points by ID if any found
                if auto_generated_ids:
                    self.client.delete(
                        collection_name=collection_name,
                        points_selector=auto_generated_ids,
                        wait=True
                    )
                
                    # Clean up orphaned relations after deletion
                    orphaned_deleted = self._cleanup_orphaned_relations(collection_name, verbose=False)
                    if orphaned_deleted > 0:
                        logger.debug(f"🗑️ Cleaned up {orphaned_deleted} orphaned relations after --clear")
                
                # Count points after deletion
                count_after = self.client.count(collection_name=collection_name).count
                deleted_count = count_before - count_after
                
                return StorageResult(
                    success=True,
                    operation="clear_collection",
                    items_processed=deleted_count,
                    processing_time=time.time() - start_time,
                    warnings=[f"Preserved {count_after} manual memories"]
                )
            else:
                # Delete the entire collection (--clear-all behavior)
                # No orphan cleanup needed since entire collection is deleted
                self.client.delete_collection(collection_name=collection_name)
                
                return StorageResult(
                    success=True,
                    operation="clear_collection",
                    items_processed=1,
                    processing_time=time.time() - start_time
                )
            
        except Exception as e:
            return StorageResult(
                success=False,
                operation="clear_collection",
                processing_time=time.time() - start_time,
                errors=[f"Failed to clear collection {collection_name}: {e}"]
            )
    
    def get_client_info(self) -> Dict[str, Any]:
        """Get Qdrant client information."""
        try:
            info = self.client.get_telemetry()
            return {
                "url": self.url,
                "version": getattr(info, 'version', 'unknown'),
                "status": "connected",
                "timeout": self.timeout,
                "has_api_key": self.api_key is not None
            }
        except Exception as e:
            return {
                "url": self.url,
                "status": "error",
                "error": str(e),
                "timeout": self.timeout,
                "has_api_key": self.api_key is not None
            }
    
    def create_entity_point(self, entity: 'Entity', embedding: List[float], 
                           collection_name: str) -> VectorPoint:
        """Create a vector point from an entity."""
        from ..analysis.entities import Entity
        
        # Generate deterministic ID using file path + entity name to prevent collisions
        entity_key = f"{entity.file_path}::{entity.name}" if entity.file_path else entity.name
        point_id = self.generate_deterministic_id(entity_key)
        
        # Create payload - v2.4 format
        payload = {
            "entity_name": entity.name,
            "entity_type": entity.entity_type.value,
            "observations": entity.observations,
            "collection": collection_name,
            "type": "chunk",
            "chunk_type": "metadata"
        }
        
        # Add optional metadata
        if entity.file_path:
            payload["file_path"] = str(entity.file_path)
        if entity.line_number:
            payload["line_number"] = entity.line_number
        if entity.docstring:
            payload["docstring"] = entity.docstring
        if entity.signature:
            payload["signature"] = entity.signature
        
        return VectorPoint(
            id=point_id,
            vector=embedding,
            payload=payload
        )
    
    def create_chunk_point(self, chunk: 'EntityChunk', embedding: List[float], 
                          collection_name: str) -> VectorPoint:
        """Create a vector point from an EntityChunk for progressive disclosure."""
        from ..analysis.entities import EntityChunk
        
        # Use the chunk's pre-defined ID format: "{file_id}::{entity_name}::{chunk_type}"
        point_id = self.generate_deterministic_id(chunk.id)
        
        # Create payload using the chunk's to_vector_payload method
        payload = chunk.to_vector_payload()
        payload["collection"] = collection_name
        payload["type"] = "chunk"  # Pure v2.4 format
        
        return VectorPoint(
            id=point_id,
            vector=embedding,
            payload=payload
        )
    
    def create_relation_chunk_point(self, chunk: 'RelationChunk', embedding: List[float], 
                                   collection_name: str) -> VectorPoint:
        """Create a vector point from a RelationChunk for v2.4 pure architecture."""
        from ..analysis.entities import RelationChunk
        
        # Use the chunk's pre-defined ID format: "{from_entity}::{relation_type}::{to_entity}"
        point_id = self.generate_deterministic_id(chunk.id)
        
        # Create payload using the chunk's to_vector_payload method
        payload = chunk.to_vector_payload()
        payload["collection"] = collection_name
        payload["type"] = "chunk"  # Pure v2.4 format
        
        return VectorPoint(
            id=point_id,
            vector=embedding,
            payload=payload
        )
    
    def create_chat_chunk_point(self, chunk: 'ChatChunk', embedding: List[float], 
                               collection_name: str) -> VectorPoint:
        """Create a vector point from a ChatChunk for v2.4 pure architecture."""
        from ..analysis.entities import ChatChunk
        
        # Use the chunk's pre-defined ID format: "chat::{chat_id}::{chunk_type}"
        point_id = self.generate_deterministic_id(chunk.id)
        
        # Create payload using the chunk's to_vector_payload method
        payload = chunk.to_vector_payload()
        payload["collection"] = collection_name
        payload["type"] = "chunk"  # Pure v2.4 format
        
        return VectorPoint(
            id=point_id,
            vector=embedding,
            payload=payload
        )
    
    def create_relation_point(self, relation: 'Relation', embedding: List[float],
                            collection_name: str) -> VectorPoint:
        """Create a vector point from a relation."""
        from ..analysis.entities import Relation
        
        # Generate deterministic ID
        relation_key = f"{relation.from_entity}-{relation.relation_type.value}-{relation.to_entity}"
        point_id = self.generate_deterministic_id(relation_key)
        
        # Create payload - v2.4 format
        payload = {
            "from": relation.from_entity,
            "to": relation.to_entity,
            "relationType": relation.relation_type.value,
            "collection": collection_name,
            "type": "chunk",
            "chunk_type": "relation",
            "entity_type": "relation"
        }
        
        # Add optional metadata
        if relation.context:
            payload["context"] = relation.context
        if relation.confidence != 1.0:
            payload["confidence"] = relation.confidence
        
        return VectorPoint(
            id=point_id,
            vector=embedding,
            payload=payload
        )
    
    def _get_all_entity_names(self, collection_name: str) -> set:
        """Get all entity names from the collection.
        
        Returns:
            Set of entity names currently in the collection.
        """
        entity_names = set()
        
        try:
            # Check if collection exists
            if not self.collection_exists(collection_name):
                return entity_names
            
            # Use helper to get all entities with pagination
            from qdrant_client import models
            
            # Get all entities (type != "relation")
            points = self._scroll_collection(
                collection_name=collection_name,
                scroll_filter=models.Filter(
                    must_not=[
                        models.FieldCondition(
                            key="type",
                            match=models.MatchValue(value="relation")
                        )
                    ]
                ),
                limit=1000,
                with_vectors=False,
                handle_pagination=True
            )
            
            for point in points:
                name = point.payload.get('entity_name', point.payload.get('name', ''))
                if name:
                    entity_names.add(name)
                    
        except Exception as e:
            # Log error but continue - empty set means no entities found
            pass
        
        return entity_names
    
    def _get_all_relations(self, collection_name: str) -> List:
        """Get all relations from the collection.
        
        Returns:
            List of relation points from the collection.
        """
        relations = []
        
        try:
            # Check if collection exists
            if not self.collection_exists(collection_name):
                return relations
            
            # Use helper to get all relations with pagination  
            from qdrant_client import models
            
            # Get all relations (type = "relation")
            relations = self._scroll_collection(
                collection_name=collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="type",
                            match=models.MatchValue(value="relation")
                        )
                    ]
                ),
                limit=1000,
                with_vectors=False,
                handle_pagination=True
            )
            
        except Exception as e:
            # Log error but continue - empty list means no relations found
            pass
        
        return relations
    
    def find_entities_for_file(self, collection_name: str, file_path: str) -> List[Dict[str, Any]]:
        """Find all entities associated with a file path using OR logic.
        
        Searches for:
        - Entities with file_path matching the given path
        - File entities where name equals the given path
        
        Returns:
            List of matching entities with id, name, type, and full payload
        """
        try:
            from qdrant_client import models
            
            # Use helper to get all matching entities with pagination
            points = self._scroll_collection(
                collection_name=collection_name,
                scroll_filter=models.Filter(
                    should=[
                        # Find entities with file_path matching
                        models.FieldCondition(
                            key="file_path",
                            match=models.MatchValue(value=file_path)
                        ),
                        # Find File entities where entity_name = file_path (with fallback to name)
                        models.FieldCondition(
                            key="entity_name", 
                            match=models.MatchValue(value=file_path)
                        ),
                    ]
                ),
                limit=1000,
                with_vectors=False,
                handle_pagination=True
            )
            
            results = []
            for point in points:
                results.append({
                    "id": point.id,
                    "name": point.payload.get('entity_name', point.payload.get('name', 'Unknown')),
                    "type": point.payload.get('entity_type', point.payload.get('entityType', point.payload.get('type', 'unknown'))),
                    "payload": point.payload
                })
            
            return results
            
        except Exception as e:
            # Fallback to search_similar if scroll is not available
            return self._find_entities_for_file_fallback(collection_name, file_path)
    
    def _find_entities_for_file_fallback(self, collection_name: str, file_path: str) -> List[Dict[str, Any]]:
        """Fallback implementation using search_similar."""
        dummy_vector = [0.1] * 1536
        results = []
        
        # Search for entities with file_path matching
        filter_path = {"file_path": file_path}
        search_result = self.search_similar(
            collection_name=collection_name,
            query_vector=dummy_vector,
            limit=1000,
            score_threshold=0.0,
            filter_conditions=filter_path
        )
        if search_result.success:
            results.extend(search_result.results)
        
        # Search for File entities where entity_name = file_path
        filter_name = {"entity_name": file_path}
        search_result = self.search_similar(
            collection_name=collection_name,
            query_vector=dummy_vector,
            limit=1000,
            score_threshold=0.0,
            filter_conditions=filter_name
        )
        if search_result.success:
            # Only add if not already in results (deduplication)
            existing_ids = {r["id"] for r in results}
            for result in search_result.results:
                if result["id"] not in existing_ids:
                    results.append(result)
        
        return results
    
    def _cleanup_orphaned_relations(self, collection_name: str, verbose: bool = False) -> int:
        """Clean up relations that reference non-existent entities.
        
        Uses a single atomic query to get a consistent snapshot of the database,
        avoiding race conditions between entity and relation queries.
        
        Args:
            collection_name: Name of the collection to clean
            verbose: Whether to log detailed information about orphaned relations
            
        Returns:
            Number of orphaned relations deleted
        """
        if verbose:
            logger.debug("🔍 Scanning collection for orphaned relations...")
        
        try:
            # Check if collection exists
            if not self.collection_exists(collection_name):
                if verbose:
                    logger.debug("   Collection doesn't exist - nothing to clean")
                return 0
            
            # Get ALL data in a single atomic query to ensure consistency
            all_points = self._scroll_collection(
                collection_name=collection_name,
                limit=10000,  # Large batch size for efficiency
                with_vectors=False,
                handle_pagination=True
            )
            
            # Process in-memory to ensure consistency
            entity_names = set()
            relations = []
            
            for point in all_points:
                # Handle both legacy v2.3 ("type": "relation") and v2.4 ("type": "chunk", "chunk_type": "relation")
                if (point.payload.get('type') == 'relation' or 
                    (point.payload.get('type') == 'chunk' and point.payload.get('chunk_type') == 'relation')):
                    relations.append(point)
                else:
                    name = point.payload.get('entity_name', point.payload.get('name', ''))
                    if name:
                        entity_names.add(name)
            
            
            if not relations:
                if verbose:
                    logger.debug("   ✅ No relations found in collection - nothing to clean")
                return 0
            
            
            # Check each relation for orphaned references with consistent snapshot
            orphaned_relations = []
            valid_relations = 0
            
            for relation in relations:
                from_entity = relation.payload.get('from', '')
                to_entity = relation.payload.get('to', '')
                relation_type = relation.payload.get('relationType', 'unknown')
                
                # Check if either end of the relation references a non-existent entity
                from_missing = from_entity not in entity_names
                to_missing = to_entity not in entity_names
                
                if from_missing or to_missing:
                    orphaned_relations.append(relation)
                else:
                    valid_relations += 1
            
            if verbose:
                logger.debug(f"   🧹 Orphan cleanup: {len(relations)} relations checked, {len(orphaned_relations)} orphans found")
            
            # Batch delete orphaned relations if found
            if orphaned_relations:
                relation_ids = [r.id for r in orphaned_relations]
                delete_result = self.delete_points(collection_name, relation_ids)
                
                if delete_result.success:
                    if verbose:
                        logger.debug(f"🗑️  Deleted {len(orphaned_relations)} orphaned relations")
                    return len(orphaned_relations)
                else:
                    logger.debug(f"❌ Failed to delete orphaned relations: {delete_result.errors}")
                    return 0
            else:
                if verbose:
                    logger.debug("   No orphaned relations found")
                return 0
                
        except Exception as e:
            logger.debug(f"❌ Error during orphaned relation cleanup: {e}")
            return 0