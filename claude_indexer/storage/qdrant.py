"""Qdrant vector store implementation."""

import time
import warnings
import hashlib
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


class ContentHashMixin:
    """Mixin for content-addressable storage functionality"""
    
    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Generate SHA256 hash of content"""
        return hashlib.sha256(content.encode()).hexdigest()
    
    def check_content_exists(self, collection_name: str, content_hash: str) -> bool:
        """Check if content hash already exists in storage"""
        try:
            # Check if collection exists first
            if not self.collection_exists(collection_name):
                logger.debug(f"Collection {collection_name} doesn't exist, content hash check returns False")
                return False
                
            results = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(
                    must=[FieldCondition(key="content_hash", match=MatchValue(value=content_hash))]
                ),
                limit=1
            )
            return len(results[0]) > 0
        except Exception as e:
            logger.debug(f"Error checking content hash existence: {e}")
            # On connection errors, fall back to processing (safer than skipping)
            return False


class QdrantStore(ManagedVectorStore, ContentHashMixin):
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
        except (ConnectionError, TimeoutError) as e:
            logger = get_logger()
            logger.warning(f"Failed to check if collection {collection_name} exists: {e}")
            return False
        except Exception as e:
            logger = get_logger()
            logger.error(f"Unexpected error checking collection {collection_name}: {e}")
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
        """Insert or update points in the collection with improved reliability."""
        start_time = time.time()
        
        if not points:
            return StorageResult(
                success=True,
                operation="upsert",
                items_processed=0,
                processing_time=time.time() - start_time
            )
        
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
        
        # Use improved batch upsert for reliability
        return self._reliable_batch_upsert(
            collection_name=collection_name,
            qdrant_points=qdrant_points,
            start_time=start_time,
            max_batch_size=1000,  # Configurable batch size
            max_retries=3
        )
    
    def _reliable_batch_upsert(self, collection_name: str, qdrant_points: List[PointStruct], 
                              start_time: float, max_batch_size: int = 1000, 
                              max_retries: int = 3) -> StorageResult:
        """Reliable batch upsert with splitting, timeout handling, and retry logic."""
        from qdrant_client.http.exceptions import ResponseHandlingException
        
        # Split into batches
        batches = self._split_into_batches(qdrant_points, max_batch_size)
        
        if len(batches) > 1:
            logger.debug(f"🔄 Splitting {len(qdrant_points)} points into {len(batches)} batches")
        
        # Check for ID collisions before processing
        point_ids = [point.id for point in qdrant_points]
        unique_ids = set(point_ids)
        if len(unique_ids) != len(point_ids):
            id_collision_count = len(point_ids) - len(unique_ids)
            collision_percentage = (id_collision_count / len(point_ids)) * 100
            
            # Enhanced logging with collision details
            logger.warning(f"⚠️ ID collision detected: {id_collision_count} duplicate IDs found")
            logger.warning(f"   Total points: {len(point_ids)}, Unique IDs: {len(unique_ids)}")
            logger.warning(f"   Collision rate: {collision_percentage:.1f}%")
            
            # Log specific colliding IDs and their details
            from collections import Counter
            id_counts = Counter(point_ids)
            colliding_ids = {id_val: count for id_val, count in id_counts.items() if count > 1}
            
            logger.warning(f"   Colliding chunk IDs ({len(colliding_ids)} unique IDs):")
            for chunk_id, count in sorted(colliding_ids.items(), key=lambda x: x[1], reverse=True):
                logger.warning(f"     • {chunk_id}: {count} duplicates")
                
                # Show entity details for this colliding ID
                colliding_points = [p for p in qdrant_points if p.id == chunk_id]
                for i, point in enumerate(colliding_points[:3]):  # Limit to first 3 examples
                    entity_name = point.payload.get('entity_name', 'unknown')
                    entity_type = point.payload.get('entity_type', 'unknown')
                    chunk_type = point.payload.get('chunk_type', 'unknown')
                    file_path = point.payload.get('file_path', 'unknown')
                    logger.warning(f"       - {chunk_type} {entity_type}: {entity_name} ({file_path})")
                if len(colliding_points) > 3:
                    logger.warning(f"       - ... and {len(colliding_points) - 3} more")
        
        # Process each batch with retry logic
        total_processed = 0
        total_failed = 0
        all_errors = []
        
        for i, batch in enumerate(batches):
            if len(batches) > 1:
                logger.debug(f"📦 Processing batch {i+1}/{len(batches)} ({len(batch)} points)")
            
            batch_result = self._upsert_batch_with_retry(
                collection_name, batch, batch_num=i+1, max_retries=max_retries
            )
            
            if batch_result.success:
                total_processed += batch_result.items_processed
                if len(batches) > 1:
                    logger.debug(f"✅ Batch {i+1} succeeded: {batch_result.items_processed} points")
                    # Check for batch-level discrepancies
                    if batch_result.items_processed != len(batch):
                        batch_discrepancy = len(batch) - batch_result.items_processed
                        logger.warning(f"⚠️ Batch {i+1} discrepancy: {batch_discrepancy} points missing")
            else:
                total_failed += len(batch)
                all_errors.extend(batch_result.errors)
                logger.error(f"❌ Batch {i+1} failed: {batch_result.errors}")
        
        # Verify storage count
        verification_result = self._verify_storage_count(
            collection_name, total_processed, len(qdrant_points)
        )
        
        processing_time = time.time() - start_time
        
        # Determine overall success
        overall_success = total_failed == 0 and verification_result['success']
        
        if not verification_result['success']:
            all_errors.append(verification_result['error'])
        
        return StorageResult(
            success=overall_success,
            operation="upsert",
            items_processed=total_processed,
            items_failed=total_failed,
            processing_time=processing_time,
            errors=all_errors if all_errors else None
        )
    
    def _split_into_batches(self, points: List[PointStruct], batch_size: int) -> List[List[PointStruct]]:
        """Split points into batches of specified size."""
        batches = []
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            batches.append(batch)
        return batches
    
    def _upsert_batch_with_retry(self, collection_name: str, batch: List[PointStruct], 
                                batch_num: int, max_retries: int) -> StorageResult:
        """Upsert a single batch with retry logic."""
        from qdrant_client.http.exceptions import ResponseHandlingException
        start_time = time.time()
        
        for attempt in range(max_retries):
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")
                    self.client.upsert(
                        collection_name=collection_name,
                        points=batch
                    )
                
                # Success!
                return StorageResult(
                    success=True,
                    operation="upsert_batch",
                    items_processed=len(batch),
                    processing_time=time.time() - start_time
                )
                
            except ResponseHandlingException as e:
                error_msg = str(e)
                if "timed out" in error_msg.lower():
                    logger.warning(f"⚠️ Batch {batch_num} attempt {attempt+1} timed out")
                    
                    if attempt < max_retries - 1:
                        # Wait before retry (exponential backoff)
                        wait_time = 2 ** attempt
                        logger.debug(f"🔄 Retrying batch {batch_num} in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        # Final timeout - return failure
                        return StorageResult(
                            success=False,
                            operation="upsert_batch",
                            items_failed=len(batch),
                            processing_time=time.time() - start_time,
                            errors=[f"Batch {batch_num} timed out after {max_retries} attempts"]
                        )
                else:
                    # Non-timeout ResponseHandlingException - fail immediately
                    return StorageResult(
                        success=False,
                        operation="upsert_batch",
                        items_failed=len(batch),
                        processing_time=time.time() - start_time,
                        errors=[f"Batch {batch_num} failed: {error_msg}"]
                    )
                    
            except Exception as e:
                # Other unexpected errors - fail immediately
                return StorageResult(
                    success=False,
                    operation="upsert_batch",
                    items_failed=len(batch),
                    processing_time=time.time() - start_time,
                    errors=[f"Batch {batch_num} failed with unexpected error: {str(e)}"]
                )
        
        # Should not reach here
        return StorageResult(
            success=False,
            operation="upsert_batch",
            items_failed=len(batch),
            processing_time=time.time() - start_time,
            errors=[f"Batch {batch_num} exhausted all retry attempts"]
        )
    
    def _verify_storage_count(self, collection_name: str, expected_new: int, 
                            total_attempted: int) -> dict:
        """Verify that storage count matches expectations."""
        try:
            # Get current count
            current_count = self.client.count(collection_name=collection_name).count
            
            # Enhanced verification: check exact count matches
            if expected_new > 0:
                logger.debug(f"✅ Storage verification: {current_count} total points in collection")
                
                # Check for storage discrepancy
                if expected_new != total_attempted:
                    discrepancy = total_attempted - expected_new
                    logger.warning(f"⚠️ Storage discrepancy detected: {discrepancy} points missing")
                    logger.warning(f"   Expected to store: {total_attempted}")
                    logger.warning(f"   Actually stored: {expected_new}")
                    logger.warning(f"   Possible causes: ID collisions, content deduplication, or silent Qdrant filtering")
                
                success = True
                error = None
            else:
                success = False
                error = f"No points were successfully stored out of {total_attempted} attempted"
                logger.error(f"❌ Storage verification failed: {error}")
            
            return {
                'success': success,
                'current_count': current_count,
                'expected_new': expected_new,
                'total_attempted': total_attempted,
                'error': error
            }
            
        except Exception as e:
            error = f"Storage verification failed: {str(e)}"
            logger.error(f"❌ {error}")
            return {
                'success': False,
                'current_count': 0,
                'expected_new': expected_new,
                'total_attempted': total_attempted,
                'error': error
            }
    
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
        except (ConnectionError, TimeoutError) as e:
            logger = get_logger()
            logger.warning(f"Failed to count points in collection {collection_name}: {e}")
            return 0
        except Exception as e:
            logger = get_logger()
            logger.error(f"Unexpected error counting points in collection {collection_name}: {e}")
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
        except (ConnectionError, TimeoutError) as e:
            logger = get_logger()
            logger.warning(f"Failed to list collections: {e}")
            return []
        except Exception as e:
            logger = get_logger()
            logger.error(f"Unexpected error listing collections: {e}")
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
            preserve_manual: If True, only delete auto-generated memories (entities with file_path or relations with entity_name/relation_target/relation_type)
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
                    # Auto-generated relations have entity_name/relation_target/relation_type structure
                    elif ('entity_name' in point.payload and 'relation_target' in point.payload and 
                          'relation_type' in point.payload):
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
    
    
    def generate_deterministic_id(self, content: str) -> int:
        """Generate deterministic ID from content (same as base.py)."""
        import hashlib
        hash_hex = hashlib.sha256(content.encode()).hexdigest()[:16]  # 16 chars = 64 bits
        return int(hash_hex, 16)
    
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
        
        # Generate deterministic ID - include import_type to prevent deduplication
        # Check if relation has import_type in metadata
        import_type = relation.metadata.get('import_type', '') if relation.metadata else ''
        logger.debug(f"🔗 Relation metadata: {relation.metadata}")
        logger.debug(f"🔗 Import type extracted: '{import_type}'")
        
        if import_type:
            relation_key = f"{relation.from_entity}-{relation.relation_type.value}-{relation.to_entity}-{import_type}"
            logger.debug(f"🔗 Creating relation WITH import_type: {relation_key}")
        else:
            # Fallback for relations without import_type
            relation_key = f"{relation.from_entity}-{relation.relation_type.value}-{relation.to_entity}"
            logger.debug(f"🔗 Creating relation WITHOUT import_type: {relation_key}")
        point_id = self.generate_deterministic_id(relation_key)
        logger.debug(f"   → Generated ID: {point_id}")
        
        # Create payload - v2.4 format matching RelationChunk
        payload = {
            "entity_name": relation.from_entity,
            "relation_target": relation.to_entity,
            "relation_type": relation.relation_type.value,
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
        # Add import_type if present
        if import_type:
            payload["import_type"] = import_type
        
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
            
            # Get all relations (chunk_type = "relation")
            relations = self._scroll_collection(
                collection_name=collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="chunk_type",
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
                    "type": point.payload.get('entity_type', 'unknown'),
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
            entity_count = 0
            other_count = 0
            
            for point in all_points:
                # v2.4 format only: "type": "chunk", "chunk_type": "relation"
                if (point.payload.get('type') == 'chunk' and point.payload.get('chunk_type') == 'relation'):
                    relations.append(point)
                else:
                    name = point.payload.get('entity_name', point.payload.get('name', ''))
                    if name:
                        entity_names.add(name)
                        entity_count += 1
                    else:
                        other_count += 1
                        if verbose:
                            logger.debug(f"   ⚠️ Point without name: type={point.payload.get('type')}, chunk_type={point.payload.get('chunk_type')}, keys={list(point.payload.keys())[:5]}")
            
            if verbose:
                logger.debug(f"   📊 Found {entity_count} entities, {len(relations)} relations, {other_count} other points")
                logger.debug(f"   📊 Sample entity names: {list(entity_names)[:5]}...")
            
            if not relations:
                if verbose:
                    logger.debug("   ✅ No relations found in collection - nothing to clean")
                return 0
            
            
            # Check each relation for orphaned references with consistent snapshot
            orphaned_relations = []
            valid_relations = 0
            file_ref_relations = 0
            
            # Module resolution helper - convert module names to possible file paths
            def resolve_module_name(module_name: str) -> bool:
                """Check if module name resolves to any existing entity."""
                if module_name in entity_names:
                    return True
                
                # Handle relative imports (.chat.parser, ..config, etc.)
                if module_name.startswith('.'):
                    clean_name = module_name.lstrip('.')
                    for entity_name in entity_names:
                        # Direct pattern match first
                        if entity_name.endswith(f"/{clean_name}.py") or entity_name.endswith(f"\\{clean_name}.py"):
                            return True
                        # Handle dot notation (chat.parser -> chat/parser.py)
                        if '.' in clean_name:
                            path_version = clean_name.replace('.', '/')
                            if entity_name.endswith(f"/{path_version}.py") or entity_name.endswith(f"\\{path_version}.py"):
                                return True
                        # Fallback: contains check
                        if clean_name in entity_name and entity_name.endswith('.py'):
                            return True
                
                # Handle absolute module paths (claude_indexer.analysis.entities)
                elif '.' in module_name:
                    path_parts = module_name.split('.')
                    for entity_name in entity_names:
                        # Check if entity path contains module structure and ends with .py
                        if (all(part in entity_name for part in path_parts) and 
                            entity_name.endswith('.py') and path_parts[-1] in entity_name):
                            return True
                
                # Handle package-level imports (claude_indexer -> any /path/claude_indexer/* files)
                else:
                    # Single package name without dots
                    for entity_name in entity_names:
                        # Check if entity path contains the package name as a directory
                        if f"/{module_name}/" in entity_name or f"\\{module_name}\\" in entity_name:
                            return True
                        # Also check if entity path ends with the package name as a directory
                        if entity_name.endswith(f"/{module_name}") or entity_name.endswith(f"\\{module_name}"):
                            return True
                
                return False
            
            # Debug: Log first few relations to understand the data
            if verbose and len(relations) > 0:
                logger.debug("   📊 Sample relations being checked:")
                for i, rel in enumerate(relations[:3]):
                    from_e = rel.payload.get('entity_name', '')
                    to_e = rel.payload.get('relation_target', '')
                    imp_type = rel.payload.get('import_type', 'none')
                    logger.debug(f"      Relation {i}: {from_e} -> {to_e} [import_type: {imp_type}]")
            
            logger.debug(f"🔍 DEBUG: Checking {len(relations)} relations against {len(entity_names)} entities")
            
            for relation in relations:
                # v2.4 relation format only
                from_entity = relation.payload.get('entity_name', '')
                to_entity = relation.payload.get('relation_target', '')
                
                # Check if either end of the relation references a non-existent entity
                # Use module resolution for better accuracy
                from_missing = from_entity not in entity_names and not resolve_module_name(from_entity)
                to_missing = to_entity not in entity_names and not resolve_module_name(to_entity)
                
                # Determine if this is a file operation relation (target is external file)
                # Check for common file extensions to identify external file references
                is_file_reference = False
                if to_entity and '.' in to_entity:
                    extension = to_entity.split('.')[-1].lower()
                    file_extensions = {
                        'json', 'csv', 'txt', 'xml', 'yaml', 'yml', 'xlsx', 'xls',
                        'ini', 'toml', 'html', 'css', 'log', 'md', 'pdf', 'doc',
                        'docx', 'png', 'jpg', 'jpeg', 'gif', 'svg', 'bin', 'dat'
                    }
                    is_file_reference = extension in file_extensions
                
                # Only mark as orphaned if:
                # 1. Source entity is missing (always invalid)
                # 2. Target is missing AND it's an internal entity (not external file)
                if from_missing:
                    orphaned_relations.append(relation)
                    # ALWAYS log orphan deletions for investigation
                    logger.info(f"   🔍 ORPHAN (source missing): {from_entity} -> {to_entity}")
                elif to_missing and not is_file_reference:
                    orphaned_relations.append(relation)
                    # ALWAYS log orphan deletions for investigation
                    imp_type = relation.payload.get('import_type', 'none')
                    logger.info(f"   🔍 ORPHAN (target missing): {from_entity} -> {to_entity} [import_type: {imp_type}]")
                else:
                    valid_relations += 1
                    if is_file_reference:
                        file_ref_relations += 1
                        if verbose and file_ref_relations <= 5:  # Log first few file refs
                            imp_type = relation.payload.get('import_type', 'none')
                            logger.debug(f"   ✅ VALID file ref: {from_entity} -> {to_entity} [import_type: {imp_type}]")
            
            if verbose:
                logger.debug(f"   🧹 Orphan cleanup summary:")
                logger.debug(f"      Total relations: {len(relations)}")
                logger.debug(f"      Valid relations: {valid_relations}")
                logger.debug(f"      File references: {file_ref_relations}")
                logger.debug(f"      Orphans found: {len(orphaned_relations)}")
            
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