"""Qdrant vector store implementation."""

import hashlib
import time
import warnings
from typing import TYPE_CHECKING, Any

from ..indexer_logging import get_logger
from .base import ManagedVectorStore, StorageResult, VectorPoint, HybridVectorPoint

if TYPE_CHECKING:
    from ..analysis.entities import EntityChunk, Relation, RelationChunk
    from ..chat.parser import ChatChunk

logger = get_logger()

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        IsNullCondition,
        MatchValue,
        PayloadField,
        PointStruct,
        SparseVector,
        SparseVectorParams,
        VectorParams,
        VectorsConfig,
    )

    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

    # Create mock classes for development - use Any to avoid redefinition errors
    Distance = Any
    QdrantClient = Any
    VectorParams = Any
    SparseVectorParams = Any
    PointStruct = Any
    Filter = Any
    FieldCondition = Any
    MatchValue = Any
    IsNullCondition = Any
    PayloadField = Any
    SparseVector = Any
    VectorsConfig = Any


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
                logger.debug(
                    f"Collection {collection_name} doesn't exist, content hash check returns False"
                )
                return False

            results = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="content_hash", match=MatchValue(value=content_hash)
                        )
                    ]
                ),
                limit=1,
            )
            return len(results[0]) > 0
        except Exception as e:
            logger.debug(f"Error checking content hash existence: {e}")
            # On connection errors, fall back to processing (safer than skipping)
            return False


class QdrantStore(ManagedVectorStore, ContentHashMixin):
    """Qdrant vector database implementation."""

    def __init__(
        self,
        url: str = "http://localhost:6333",
        api_key: str = None,
        timeout: float = 60.0,
        auto_create_collections: bool = True,
        **kwargs,  # noqa: ARG002
    ):
        if not QDRANT_AVAILABLE:
            raise ImportError(
                "Qdrant client not available. Install with: pip install qdrant-client"
            )

        # Define distance metrics after import validation
        self.DISTANCE_METRICS = {
            "cosine": Distance.COSINE,
            "euclidean": Distance.EUCLID,
            "dot": Distance.DOT,
        }

        super().__init__(auto_create_collections=auto_create_collections)

    def __init__(
        self,
        url: str = "http://localhost:6333",
        api_key: str = None,
        timeout: float = 60.0,
        auto_create_collections: bool = True,
        **kwargs,  # noqa: ARG002
    ):
        if not QDRANT_AVAILABLE:
            raise ImportError(
                "Qdrant client not available. Install with: pip install qdrant-client"
            )

        super().__init__(auto_create_collections=auto_create_collections)

        # Define distance metrics after import validation
        self.DISTANCE_METRICS = {
            "cosine": Distance.COSINE,
            "euclidean": Distance.EUCLID,
            "dot": Distance.DOT,
        }

        self.url = url
        self.api_key = api_key
        self.timeout = timeout

        # Initialize client
        try:
            # Suppress insecure connection warning for development
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", message="Api key is used with an insecure connection"
                )
                self.client = QdrantClient(url=url, api_key=api_key, timeout=timeout)
            # Test connection
            self.client.get_collections()
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Qdrant at {url}: {e}") from None

    def create_collection(
        self, collection_name: str, vector_size: int, distance_metric: str = "cosine"
    ) -> StorageResult:
        """Create a new Qdrant collection."""
        start_time = time.time()

        try:
            if distance_metric not in self.DISTANCE_METRICS:
                available = list(self.DISTANCE_METRICS.keys())
                return StorageResult(
                    success=False,
                    operation="create_collection",
                    processing_time=time.time() - start_time,
                    errors=[
                        f"Invalid distance metric: {distance_metric}. Available: {available}"
                    ],
                )

            distance = self.DISTANCE_METRICS[distance_metric]

            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vector_size, distance=distance),
                optimizers_config={"indexing_threshold": 100},
            )

            return StorageResult(
                success=True,
                operation="create_collection",
                items_processed=1,
                processing_time=time.time() - start_time,
            )

        except Exception as e:
            return StorageResult(
                success=False,
                operation="create_collection",
                processing_time=time.time() - start_time,
                errors=[f"Failed to create collection {collection_name}: {e}"],
            )

    def create_collection_with_sparse_vectors(
        self,
        collection_name: str,
        dense_vector_size: int,
        sparse_vector_size: int = 10000,
        distance_metric: str = "cosine",
    ) -> StorageResult:
        """Create a new Qdrant collection with both dense and sparse vector support.
        
        Args:
            collection_name: Name of the collection to create
            dense_vector_size: Size of dense vectors (e.g., 1536 for OpenAI embeddings)
            sparse_vector_size: Maximum size for sparse vectors (default: 10000)
            distance_metric: Distance metric for dense vectors
            
        Returns:
            StorageResult indicating success or failure
        """
        start_time = time.time()

        try:
            if distance_metric not in self.DISTANCE_METRICS:
                available = list(self.DISTANCE_METRICS.keys())
                return StorageResult(
                    success=False,
                    operation="create_collection_with_sparse",
                    processing_time=time.time() - start_time,
                    errors=[
                        f"Invalid distance metric: {distance_metric}. Available: {available}"
                    ],
                )

            distance = self.DISTANCE_METRICS[distance_metric]

            # Create collection with named dense and sparse vector support
            vectors_config = {
                "dense": VectorParams(size=dense_vector_size, distance=distance)
            }
            sparse_vectors_config = {
                "bm25": SparseVectorParams()  # Named sparse vector for BM25
            }

            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=vectors_config,
                sparse_vectors_config=sparse_vectors_config,
                optimizers_config={"indexing_threshold": 100},
            )

            logger.debug(
                f"Created collection {collection_name} with dense ({dense_vector_size}D) "
                f"and sparse ({sparse_vector_size}D) vectors"
            )

            return StorageResult(
                success=True,
                operation="create_collection_with_sparse",
                items_processed=1,
                processing_time=time.time() - start_time,
            )

        except Exception as e:
            return StorageResult(
                success=False,
                operation="create_collection_with_sparse",
                processing_time=time.time() - start_time,
                errors=[f"Failed to create sparse vector collection {collection_name}: {e}"],
            )

    def collection_exists(self, collection_name: str) -> bool:
        """Check if collection exists."""
        try:
            self.client.get_collection(collection_name)
            return True
        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "does not exist" in error_msg:
                return False
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
                processing_time=time.time() - start_time,
            )

        except Exception as e:
            return StorageResult(
                success=False,
                operation="delete_collection",
                processing_time=time.time() - start_time,
                errors=[f"Failed to delete collection {collection_name}: {e}"],
            )

    def upsert_points(
        self, collection_name: str, points: list[VectorPoint | HybridVectorPoint]
    ) -> StorageResult:
        """Insert or update points in the collection with improved reliability."""
        start_time = time.time()

        if not points:
            return StorageResult(
                success=True,
                operation="upsert",
                items_processed=0,
                processing_time=time.time() - start_time,
            )

        # Ensure collection exists first - determine vector size from first point
        if isinstance(points[0], HybridVectorPoint):
            vector_size = len(points[0].dense_vector)
        else:
            vector_size = len(points[0].vector)
            
        if not self.ensure_collection(collection_name, vector_size):
            return StorageResult(
                success=False,
                operation="upsert",
                processing_time=time.time() - start_time,
                errors=[f"Collection {collection_name} does not exist"],
            )

        # AFTER collection creation, check if it has sparse vector support
        has_sparse_vectors = self._collection_has_sparse_vectors(collection_name)
        logger.debug(f"🔍 SPARSE DEBUG: Collection {collection_name} has_sparse_vectors = {has_sparse_vectors} (checked after creation)")

        # Pre-segregate points by type to avoid per-point isinstance calls
        hybrid_points = []
        regular_points = []
        for point in points:
            if isinstance(point, HybridVectorPoint):
                hybrid_points.append(point)
            else:
                regular_points.append(point)
        
        # Convert to Qdrant points
        qdrant_points = []
        
        # Process hybrid points in batch (optimized - no per-point type checking)
        if has_sparse_vectors:
            # Collection supports sparse vectors - create named vector format
            for point in hybrid_points:
                # Handle BM25 sparse vector - could be SparseVector object or list
                if hasattr(point.sparse_vector, 'indices'):
                    # Already a SparseVector object from BM25
                    sparse_vector = point.sparse_vector
                else:
                    # Convert list to SparseVector (optimized single-pass)
                    indices = []
                    values = []
                    for i, val in enumerate(point.sparse_vector):
                        if val > 0:
                            indices.append(i)
                            values.append(val)
                    sparse_vector = SparseVector(indices=indices, values=values)
                
                # Pre-create named vectors dictionary (avoid per-point dict creation)
                qdrant_points.append(PointStruct(
                    id=point.id, 
                    vector={"dense": point.dense_vector, "bm25": sparse_vector},
                    payload=point.payload
                ))
        else:
            # Collection doesn't support sparse vectors - fallback to dense only
            if hybrid_points:
                logger.warning(f"Collection {collection_name} doesn't support sparse vectors, using dense only for {len(hybrid_points)} hybrid points")
            for point in hybrid_points:
                qdrant_points.append(PointStruct(
                    id=point.id, 
                    vector={"dense": point.dense_vector},
                    payload=point.payload
                ))
        
        # Process regular points in batch (optimized - no per-point branching)
        if has_sparse_vectors:
            # Collection expects named vectors, so wrap the dense vector
            for point in regular_points:
                qdrant_points.append(PointStruct(
                    id=point.id, 
                    vector={"dense": point.vector},
                    payload=point.payload
                ))
        else:
            # Old-style collection with unnamed vectors
            for point in regular_points:
                qdrant_points.append(PointStruct(
                    id=point.id, 
                    vector=point.vector,
                    payload=point.payload
                ))

        # Use improved batch upsert for reliability
        return self._reliable_batch_upsert(
            collection_name=collection_name,
            qdrant_points=qdrant_points,
            start_time=start_time,
            max_batch_size=1000,  # Configurable batch size
            max_retries=3,
        )

    def _reliable_batch_upsert(
        self,
        collection_name: str,
        qdrant_points: list[PointStruct],
        start_time: float,
        max_batch_size: int = 1000,
        max_retries: int = 3,
    ) -> StorageResult:
        """Reliable batch upsert with splitting, timeout handling, and retry logic."""

        # Split into batches
        batches = self._split_into_batches(qdrant_points, max_batch_size)

        if len(batches) > 1:
            logger.debug(
                f"🔄 Splitting {len(qdrant_points)} points into {len(batches)} batches"
            )

        # Check for ID collisions before processing
        point_ids = [point.id for point in qdrant_points]
        unique_ids = set(point_ids)
        if len(unique_ids) != len(point_ids):
            id_collision_count = len(point_ids) - len(unique_ids)
            collision_percentage = (id_collision_count / len(point_ids)) * 100

            # Enhanced logging with collision details
            logger.warning(
                f"⚠️ ID collision detected: {id_collision_count} duplicate IDs found"
            )
            logger.warning(
                f"   Total points: {len(point_ids)}, Unique IDs: {len(unique_ids)}"
            )
            logger.warning(f"   Collision rate: {collision_percentage:.1f}%")

            # Log specific colliding IDs and their details
            from collections import Counter

            id_counts = Counter(point_ids)
            colliding_ids = {
                id_val: count for id_val, count in id_counts.items() if count > 1
            }

            logger.warning(f"   Colliding chunk IDs ({len(colliding_ids)} unique IDs):")
            for chunk_id, count in sorted(
                colliding_ids.items(), key=lambda x: x[1], reverse=True
            ):
                logger.warning(f"     • {chunk_id}: {count} duplicates")

                # Show entity details for this colliding ID
                colliding_points = [p for p in qdrant_points if p.id == chunk_id]
                for _i, point in enumerate(
                    colliding_points[:3]
                ):  # Limit to first 3 examples
                    entity_name = point.payload.get("entity_name", "unknown")
                    entity_type = point.payload.get("metadata", {}).get("entity_type", "unknown")
                    chunk_type = point.payload.get("chunk_type", "unknown")
                    file_path = point.payload.get("metadata", {}).get("file_path", "unknown")
                    logger.warning(
                        f"       - {chunk_type} {entity_type}: {entity_name} ({file_path})"
                    )
                if len(colliding_points) > 3:
                    logger.warning(f"       - ... and {len(colliding_points) - 3} more")

        # Process each batch with retry logic
        total_processed = 0
        total_failed = 0
        all_errors = []

        for i, batch in enumerate(batches):
            if len(batches) > 1:
                logger.debug(
                    f"📦 Processing batch {i + 1}/{len(batches)} ({len(batch)} points)"
                )

            batch_result = self._upsert_batch_with_retry(
                collection_name, batch, batch_num=i + 1, max_retries=max_retries
            )

            if batch_result.success:
                total_processed += batch_result.items_processed
                if len(batches) > 1:
                    logger.debug(
                        f"✅ Batch {i + 1} succeeded: {batch_result.items_processed} points"
                    )
                    # Check for batch-level discrepancies
                    if batch_result.items_processed != len(batch):
                        batch_discrepancy = len(batch) - batch_result.items_processed
                        logger.warning(
                            f"⚠️ Batch {i + 1} discrepancy: {batch_discrepancy} points missing"
                        )
            else:
                total_failed += len(batch)
                all_errors.extend(batch_result.errors)
                logger.error(f"❌ Batch {i + 1} failed: {batch_result.errors}")

        # Verify storage count
        verification_result = self._verify_storage_count(
            collection_name, total_processed, len(qdrant_points)
        )

        processing_time = time.time() - start_time

        # Determine overall success
        overall_success = total_failed == 0 and verification_result["success"]

        if not verification_result["success"]:
            all_errors.append(verification_result["error"])

        return StorageResult(
            success=overall_success,
            operation="upsert",
            items_processed=total_processed,
            items_failed=total_failed,
            processing_time=processing_time,
            errors=all_errors if all_errors else None,
        )

    def _split_into_batches(
        self, points: list[PointStruct], batch_size: int
    ) -> list[list[PointStruct]]:
        """Split points into batches of specified size."""
        batches = []
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            batches.append(batch)
        return batches

    def _upsert_batch_with_retry(
        self,
        collection_name: str,
        batch: list[PointStruct],
        batch_num: int,
        max_retries: int,
    ) -> StorageResult:
        """Upsert a single batch with retry logic."""
        from qdrant_client.http.exceptions import ResponseHandlingException

        start_time = time.time()

        for attempt in range(max_retries):
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore", message="Api key is used with an insecure connection"
                    )
                    self.client.upsert(collection_name=collection_name, points=batch)

                # Success!
                return StorageResult(
                    success=True,
                    operation="upsert_batch",
                    items_processed=len(batch),
                    processing_time=time.time() - start_time,
                )

            except ResponseHandlingException as e:
                error_msg = str(e)
                if "timed out" in error_msg.lower():
                    logger.warning(
                        f"⚠️ Batch {batch_num} attempt {attempt + 1} timed out"
                    )

                    if attempt < max_retries - 1:
                        # Wait before retry (exponential backoff)
                        wait_time = 2**attempt
                        logger.debug(
                            f"🔄 Retrying batch {batch_num} in {wait_time}s..."
                        )
                        time.sleep(wait_time)
                    else:
                        # Final timeout - return failure
                        return StorageResult(
                            success=False,
                            operation="upsert_batch",
                            items_failed=len(batch),
                            processing_time=time.time() - start_time,
                            errors=[
                                f"Batch {batch_num} timed out after {max_retries} attempts"
                            ],
                        )
                else:
                    # Non-timeout ResponseHandlingException - fail immediately
                    return StorageResult(
                        success=False,
                        operation="upsert_batch",
                        items_failed=len(batch),
                        processing_time=time.time() - start_time,
                        errors=[f"Batch {batch_num} failed: {error_msg}"],
                    )

            except Exception as e:
                # Other unexpected errors - fail immediately
                return StorageResult(
                    success=False,
                    operation="upsert_batch",
                    items_failed=len(batch),
                    processing_time=time.time() - start_time,
                    errors=[
                        f"Batch {batch_num} failed with unexpected error: {str(e)}"
                    ],
                )

        # Should not reach here
        return StorageResult(
            success=False,
            operation="upsert_batch",
            items_failed=len(batch),
            processing_time=time.time() - start_time,
            errors=[f"Batch {batch_num} exhausted all retry attempts"],
        )

    def _verify_storage_count(
        self, collection_name: str, expected_new: int, total_attempted: int
    ) -> dict:
        """Verify that storage count matches expectations."""
        try:
            # Get current count
            current_count = self.client.count(collection_name=collection_name).count

            # Enhanced verification: check exact count matches
            if expected_new > 0:
                # logger.debug(f"✅ Storage verification: {current_count} total points in collection")

                # Check for storage discrepancy
                if expected_new != total_attempted:
                    discrepancy = total_attempted - expected_new
                    logger.warning(
                        f"⚠️ Storage discrepancy detected: {discrepancy} points missing"
                    )
                    logger.warning(f"   Expected to store: {total_attempted}")
                    logger.warning(f"   Actually stored: {expected_new}")
                    logger.warning(
                        "   Possible causes: ID collisions, content deduplication, or silent Qdrant filtering"
                    )

                success = True
                error = None
            else:
                success = False
                error = f"No points were successfully stored out of {total_attempted} attempted"
                logger.error(f"❌ Storage verification failed: {error}")

            return {
                "success": success,
                "current_count": current_count,
                "expected_new": expected_new,
                "total_attempted": total_attempted,
                "error": error,
            }

        except Exception as e:
            error = f"Storage verification failed: {str(e)}"
            logger.error(f"❌ {error}")
            return {
                "success": False,
                "current_count": 0,
                "expected_new": expected_new,
                "total_attempted": total_attempted,
                "error": error,
            }

    def delete_points(
        self, collection_name: str, point_ids: list[str | int]
    ) -> StorageResult:
        """Delete points by their IDs."""
        start_time = time.time()

        try:
            self.client.delete(
                collection_name=collection_name, points_selector=point_ids
            )

            return StorageResult(
                success=True,
                operation="delete",
                items_processed=len(point_ids),
                processing_time=time.time() - start_time,
            )

        except Exception as e:
            logger.error(f"❌ Exception in delete_points: {e}")
            return StorageResult(
                success=False,
                operation="delete",
                items_failed=len(point_ids),
                processing_time=time.time() - start_time,
                errors=[f"Failed to delete points: {e}"],
            )

    def search_similar_with_mode(
        self,
        collection_name: str,
        query_vector: list[float] = None,
        dense_vector: list[float] = None,
        sparse_vector: list[float] = None,
        search_mode: str = "semantic",
        limit: int = 10,
        score_threshold: float = 0.0,
        filter_conditions: dict[str, Any] = None,
        alpha: float = 0.5,
    ) -> StorageResult:
        """Search with support for semantic, keyword, and hybrid modes.
        
        Args:
            collection_name: Name of the collection to search
            query_vector: Vector for backward compatibility (alias for dense_vector)
            dense_vector: Dense vector for semantic search
            sparse_vector: Sparse vector for keyword search
            search_mode: "semantic", "keyword", or "hybrid"
            limit: Maximum number of results
            score_threshold: Minimum score threshold
            filter_conditions: Additional filters to apply
            alpha: Weight for hybrid search (0.0 = full sparse, 1.0 = full dense)
            
        Returns:
            StorageResult with search results
        """
        start_time = time.time()
        
        try:
            # Handle backward compatibility
            if query_vector is not None and dense_vector is None:
                dense_vector = query_vector
            
            # Validate inputs based on search mode
            if search_mode == "semantic" and dense_vector is None:
                return StorageResult(
                    success=False,
                    operation="search_hybrid",
                    processing_time=time.time() - start_time,
                    errors=["Dense vector required for semantic search"],
                )
            
            if search_mode == "keyword" and sparse_vector is None:
                return StorageResult(
                    success=False,
                    operation="search_hybrid",
                    processing_time=time.time() - start_time,
                    errors=["Sparse vector required for keyword search"],
                )
            
            if search_mode == "hybrid" and (dense_vector is None or sparse_vector is None):
                return StorageResult(
                    success=False,
                    operation="search_hybrid",
                    processing_time=time.time() - start_time,
                    errors=["Both dense and sparse vectors required for hybrid search"],
                )
            
            # Build filter if provided
            query_filter = None
            if filter_conditions:
                query_filter = self._build_filter(filter_conditions)
            
            if search_mode == "semantic":
                # Dense vector search only
                search_results = self.client.search(
                    collection_name=collection_name,
                    query_vector=dense_vector,
                    limit=limit,
                    score_threshold=score_threshold,
                    query_filter=query_filter,
                )
                
            elif search_mode == "keyword":
                # Sparse vector search only
                sparse_query = SparseVector(
                    indices=[i for i, val in enumerate(sparse_vector) if val > 0],
                    values=[val for val in sparse_vector if val > 0]
                )
                
                search_results = self.client.search(
                    collection_name=collection_name,
                    query_vector=("sparse", sparse_query),
                    limit=limit,
                    score_threshold=score_threshold,
                    query_filter=query_filter,
                )
                
            elif search_mode == "hybrid":
                # Hybrid search using RRF (Reciprocal Rank Fusion)
                return self._hybrid_search_rrf(
                    collection_name=collection_name,
                    dense_vector=dense_vector,
                    sparse_vector=sparse_vector,
                    limit=limit,
                    score_threshold=score_threshold,
                    query_filter=query_filter,
                    alpha=alpha,
                    start_time=start_time,
                )
            
            else:
                return StorageResult(
                    success=False,
                    operation="search_hybrid",
                    processing_time=time.time() - start_time,
                    errors=[f"Invalid search mode: {search_mode}. Use 'semantic', 'keyword', or 'hybrid'"],
                )
            
            # Convert results
            results = []
            for result in search_results:
                results.append(
                    {"id": result.id, "score": result.score, "payload": result.payload}
                )
            
            return StorageResult(
                success=True,
                operation=f"search_{search_mode}",
                processing_time=time.time() - start_time,
                results=results,
                total_found=len(results),
            )
            
        except Exception as e:
            logger.debug(f"❌ search_similar_with_mode exception: {e}")
            return StorageResult(
                success=False,
                operation="search_hybrid",
                processing_time=time.time() - start_time,
                errors=[f"Search failed: {e}"],
            )

    def search_similar(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 10,
        score_threshold: float = 0.0,
        filter_conditions: dict[str, Any] = None,
    ) -> StorageResult:
        """Search for similar vectors."""
        start_time = time.time()

        try:
            # Build filter if provided
            query_filter = None
            if filter_conditions:
                query_filter = self._build_filter(filter_conditions)
                logger.debug("🔍 search_similar debug:")
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
                query_filter=query_filter,
            )

            if filter_conditions:
                logger.debug(f"   Raw search results count: {len(search_results)}")
                for i, result in enumerate(search_results):
                    logger.debug(f"   Result {i}: ID={result.id}, score={result.score}")
                    logger.debug(f"      Payload: {result.payload}")

            # Convert results
            results = []
            for result in search_results:
                results.append(
                    {"id": result.id, "score": result.score, "payload": result.payload}
                )

            return StorageResult(
                success=True,
                operation="search",
                processing_time=time.time() - start_time,
                results=results,
                total_found=len(results),
            )

        except Exception as e:
            logger.debug(f"❌ search_similar exception: {e}")
            return StorageResult(
                success=False,
                operation="search",
                processing_time=time.time() - start_time,
                errors=[f"Search failed: {e}"],
            )

    def _hybrid_search_rrf(
        self,
        collection_name: str,
        dense_vector: list[float],
        sparse_vector: list[float],
        limit: int,
        score_threshold: float,
        query_filter: Filter = None,
        alpha: float = 0.5,
        start_time: float = None,
        k: int = 60,
    ) -> StorageResult:
        """Hybrid search using Reciprocal Rank Fusion (RRF).
        
        Args:
            collection_name: Name of the collection
            dense_vector: Dense vector for semantic search
            sparse_vector: Sparse vector for keyword search
            limit: Number of results to return
            score_threshold: Minimum score threshold
            query_filter: Optional filter conditions
            alpha: Weight for combining scores (0.0 = full sparse, 1.0 = full dense)
            start_time: Start time for timing calculations
            k: RRF parameter (typically 60)
            
        Returns:
            StorageResult with hybrid search results
        """
        if start_time is None:
            start_time = time.time()
        
        try:
            # Get more results from each search to improve fusion quality
            search_limit = max(limit * 3, 50)  # Get 3x more results for better fusion
            
            # Perform dense vector search
            dense_results = self.client.search(
                collection_name=collection_name,
                query_vector=dense_vector,
                limit=search_limit,
                score_threshold=0.0,  # Lower threshold for RRF
                query_filter=query_filter,
            )
            
            # Perform sparse vector search
            sparse_query = SparseVector(
                indices=[i for i, val in enumerate(sparse_vector) if val > 0],
                values=[val for val in sparse_vector if val > 0]
            )
            
            sparse_results = self.client.search(
                collection_name=collection_name,
                query_vector=("sparse", sparse_query),
                limit=search_limit,
                score_threshold=0.0,  # Lower threshold for RRF
                query_filter=query_filter,
            )
            
            # Apply RRF fusion
            fused_results = self._apply_rrf_fusion(
                dense_results=dense_results,
                sparse_results=sparse_results,
                alpha=alpha,
                k=k,
                limit=limit,
                score_threshold=score_threshold,
            )
            
            return StorageResult(
                success=True,
                operation="search_hybrid",
                processing_time=time.time() - start_time,
                results=fused_results,
                total_found=len(fused_results),
            )
            
        except Exception as e:
            logger.debug(f"❌ _hybrid_search_rrf exception: {e}")
            return StorageResult(
                success=False,
                operation="search_hybrid",
                processing_time=time.time() - start_time,
                errors=[f"Hybrid search failed: {e}"],
            )

    def _apply_rrf_fusion(
        self,
        dense_results: list,
        sparse_results: list,
        alpha: float = 0.5,
        k: int = 60,
        limit: int = 10,
        score_threshold: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Apply Reciprocal Rank Fusion to combine dense and sparse search results.
        
        Args:
            dense_results: Results from dense vector search
            sparse_results: Results from sparse vector search
            alpha: Weight for combining scores (0.0 = full sparse, 1.0 = full dense)
            k: RRF parameter for rank normalization
            limit: Maximum number of results to return
            score_threshold: Minimum score threshold for final results
            
        Returns:
            List of fused results with combined scores
        """
        # Create dictionaries for quick lookup by document ID
        dense_dict = {result.id: {"rank": i + 1, "score": result.score, "payload": result.payload}
                     for i, result in enumerate(dense_results)}
        sparse_dict = {result.id: {"rank": i + 1, "score": result.score, "payload": result.payload}
                      for i, result in enumerate(sparse_results)}
        
        # Get all unique document IDs
        all_ids = set(dense_dict.keys()) | set(sparse_dict.keys())
        
        fused_scores = {}
        
        for doc_id in all_ids:
            # Calculate RRF score for this document
            dense_rrf = 1.0 / (k + dense_dict[doc_id]["rank"]) if doc_id in dense_dict else 0.0
            sparse_rrf = 1.0 / (k + sparse_dict[doc_id]["rank"]) if doc_id in sparse_dict else 0.0
            
            # Combine using alpha weighting
            combined_score = alpha * dense_rrf + (1.0 - alpha) * sparse_rrf
            
            # Get payload from available result (prefer dense, then sparse)
            payload = None
            if doc_id in dense_dict:
                payload = dense_dict[doc_id]["payload"]
            elif doc_id in sparse_dict:
                payload = sparse_dict[doc_id]["payload"]
            
            fused_scores[doc_id] = {
                "id": doc_id,
                "score": combined_score,
                "payload": payload,
                "dense_score": dense_dict.get(doc_id, {}).get("score", 0.0),
                "sparse_score": sparse_dict.get(doc_id, {}).get("score", 0.0),
                "dense_rank": dense_dict.get(doc_id, {}).get("rank", None),
                "sparse_rank": sparse_dict.get(doc_id, {}).get("rank", None),
            }
        
        # Sort by combined score (descending)
        sorted_results = sorted(
            fused_scores.values(),
            key=lambda x: x["score"],
            reverse=True
        )
        
        # Apply score threshold and limit
        filtered_results = [
            result for result in sorted_results
            if result["score"] >= score_threshold
        ]
        
        return filtered_results[:limit]

    def _collection_has_sparse_vectors(self, collection_name: str) -> bool:
        """Check if a collection supports sparse vectors with retry for timing issues.
        
        Args:
            collection_name: Name of the collection to check
            
        Returns:
            True if collection has sparse vector support, False otherwise
        """
        import time
        
        max_retries = 3
        retry_delay = 0.1  # 100ms between retries
        
        for attempt in range(max_retries):
            try:
                collection_info = self.client.get_collection(collection_name)
                
                # Check for sparse_vectors configuration (our BM25 uses named sparse vectors)
                if hasattr(collection_info.config.params, 'sparse_vectors'):
                    sparse_config = collection_info.config.params.sparse_vectors
                    if sparse_config is not None:
                        # Look for 'bm25' named sparse vector
                        has_bm25 = False
                        if hasattr(sparse_config, 'get'):
                            has_bm25 = 'bm25' in sparse_config
                        elif hasattr(sparse_config, '__dict__'):
                            has_bm25 = 'bm25' in sparse_config.__dict__
                        else:
                            has_bm25 = True  # Any sparse vector config means support
                        
                        if has_bm25:
                            logger.debug(f"🔍 SPARSE DEBUG: Collection {collection_name} confirmed BM25 support on attempt {attempt + 1}")
                            return True
                        
                # If no sparse vectors found and we have retries left, wait and try again
                if attempt < max_retries - 1:
                    logger.debug(f"🔍 SPARSE DEBUG: Collection {collection_name} no sparse vectors on attempt {attempt + 1}, retrying...")
                    time.sleep(retry_delay)
                    continue
                    
                logger.debug(f"🔍 SPARSE DEBUG: Collection {collection_name} no sparse vectors after {max_retries} attempts")
                return False
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(f"Error checking sparse vector support for {collection_name} (attempt {attempt + 1}): {e}, retrying...")
                    time.sleep(retry_delay)
                else:
                    logger.debug(f"Error checking sparse vector support for {collection_name} after {max_retries} attempts: {e}")
                    return False
        
        return False

    def _build_filter(self, filter_conditions: dict[str, Any]) -> Filter:
        """Build Qdrant filter from conditions."""
        conditions = []

        for field, value in filter_conditions.items():
            if isinstance(value, str | int | float | bool):
                condition = FieldCondition(key=field, match=MatchValue(value=value))
                conditions.append(condition)

        return Filter(must=conditions) if conditions else None

    def get_collection_info(self, collection_name: str) -> dict[str, Any]:
        """Get information about a collection."""
        try:
            collection_info = self.client.get_collection(collection_name)

            # Handle both legacy single vector config and new multi-vector config (BM25)
            vectors_config = collection_info.config.params.vectors
            if isinstance(vectors_config, dict):
                # New multi-vector format (BM25) - get primary dense vector config
                dense_config = vectors_config.get("dense") or next(iter(vectors_config.values()))
                vector_size = dense_config.size if hasattr(dense_config, 'size') else 0
                distance_metric = dense_config.distance.value if hasattr(dense_config, 'distance') else "unknown"
            else:
                # Legacy single vector format
                vector_size = vectors_config.size
                distance_metric = vectors_config.distance.value
            
            return {
                "name": collection_name,
                "status": collection_info.status.value,
                "vector_size": vector_size,
                "distance_metric": distance_metric,
                "points_count": collection_info.points_count,
                "indexed_vectors_count": collection_info.indexed_vectors_count,
                "segments_count": collection_info.segments_count,
                "has_sparse_vectors": bool(getattr(collection_info.config.params, "sparse_vectors", None)),
            }

        except Exception as e:
            return {"name": collection_name, "error": str(e)}

    def count(self, collection_name: str) -> int:
        """Count total points in collection - test compatibility method."""
        try:
            collection_info = self.client.get_collection(collection_name)
            return collection_info.points_count
        except (ConnectionError, TimeoutError) as e:
            logger = get_logger()
            logger.warning(
                f"Failed to count points in collection {collection_name}: {e}"
            )
            return 0
        except Exception as e:
            logger = get_logger()
            logger.error(
                f"Unexpected error counting points in collection {collection_name}: {e}"
            )
            return 0

    def search(self, collection_name: str, query_vector, top_k: int = 10):
        """Legacy search interface for test compatibility."""
        try:
            if hasattr(query_vector, "tolist"):
                query_vector = query_vector.tolist()
            elif isinstance(query_vector, list):
                pass
            else:
                query_vector = list(query_vector)

            search_results = self.client.search(
                collection_name=collection_name, query_vector=query_vector, limit=top_k
            )

            # Return results in expected format for tests
            class SearchHit:
                def __init__(self, id, score, payload):
                    self.id = id
                    self.score = score
                    self.payload = payload

            return [
                SearchHit(result.id, result.score, result.payload)
                for result in search_results
            ]

        except Exception as e:
            logger.debug(f"Search failed: {e}")
            return []

    def list_collections(self) -> list[str]:
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
        scroll_filter: Any | None = None,
        limit: int = 1000,
        with_vectors: bool = False,
        handle_pagination: bool = True,
    ) -> list[Any]:
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

            # logger.debug(
            #     f"Starting scroll operation for collection {collection_name}, limit={limit}, handle_pagination={handle_pagination}"
            # )

            while True:
                iteration += 1

                # Safety check: prevent infinite loops with iteration limit
                if iteration > max_iterations:
                    logger.warning(
                        f"Scroll operation hit max iterations ({max_iterations}) for collection {collection_name}"
                    )
                    break

                # logger.debug(f"Scroll iteration {iteration}, offset={offset}")

                scroll_result = self.client.scroll(
                    collection_name=collection_name,
                    scroll_filter=scroll_filter,
                    limit=limit,
                    offset=offset,
                    with_payload=True,
                    with_vectors=with_vectors,
                )

                points, next_offset = scroll_result
                all_points.extend(points)

                logger.debug(
                    f"Retrieved {len(points)} points, next_offset={next_offset}, total_points={len(all_points)}"
                )

                # Handle pagination if requested and more results exist
                if handle_pagination and next_offset is not None:
                    # CRITICAL FIX: Infinite loop protection - check if we've seen this offset before
                    offset_key = str(
                        next_offset
                    )  # Convert to string for set membership
                    if offset_key in seen_offsets:
                        logger.warning(
                            f"Detected offset loop in collection {collection_name} at iteration {iteration}. "
                            f"Offset {next_offset} already seen. Breaking pagination to prevent infinite loop."
                        )
                        break

                    seen_offsets.add(offset_key)
                    offset = next_offset
                    logger.debug(f"Advancing to next page with offset {next_offset}")
                else:
                    # logger.debug(
                    #     f"Pagination complete: handle_pagination={handle_pagination}, next_offset={next_offset}"
                    # )
                    break

            # logger.debug(
            #     f"Scroll operation completed for collection {collection_name}: "
            #     f"{len(all_points)} total points retrieved in {iteration} iterations"
            # )
            return all_points

        except Exception as e:
            # Check if collection doesn't exist
            if "doesn't exist" in str(e) or "Not found" in str(e):
                logger.warning(
                    f"Collection '{collection_name}' doesn't exist - returning empty result"
                )
                return []
            # Log error and return empty list
            logger.error(f"Error in _scroll_collection for {collection_name}: {e}")
            return []

    def clear_collection(
        self, collection_name: str, preserve_manual: bool = True
    ) -> StorageResult:
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
                    warnings=[
                        f"Collection {collection_name} doesn't exist - nothing to clear"
                    ],
                )

            if preserve_manual:
                # Delete only auto-generated memories (entities with file_path or relations)

                # Count points before deletion for reporting
                count_before = self.client.count(collection_name=collection_name).count

                # Get all points to identify auto-generated content
                # Use helper to get all points with pagination
                all_points = self._scroll_collection(
                    collection_name=collection_name,
                    limit=10000,  # Large page size for efficiency
                    with_vectors=False,
                    handle_pagination=True,
                )

                # Find points that are auto-generated (code-indexed entities or relations)
                auto_generated_ids = []
                for point in all_points:
                    # Auto-generated entities have file_path
                    if (
                        point.payload.get("metadata", {}).get("file_path")
                        or (
                            "entity_name" in point.payload
                            and "relation_target" in point.payload
                            and "relation_type" in point.payload
                        )
                    ):
                        auto_generated_ids.append(point.id)

                # Delete auto-generated points by ID if any found
                if auto_generated_ids:
                    self.client.delete(
                        collection_name=collection_name,
                        points_selector=auto_generated_ids,
                        wait=True,
                    )

                    # Clean up orphaned relations after deletion
                    orphaned_deleted = self._cleanup_orphaned_relations(
                        collection_name, verbose=False
                    )
                    if orphaned_deleted > 0:
                        logger.debug(
                            f"🗑️ Cleaned up {orphaned_deleted} orphaned relations after --clear"
                        )

                # Count points after deletion
                count_after = self.client.count(collection_name=collection_name).count
                deleted_count = count_before - count_after

                return StorageResult(
                    success=True,
                    operation="clear_collection",
                    items_processed=deleted_count,
                    processing_time=time.time() - start_time,
                    warnings=[f"Preserved {count_after} manual memories"],
                )
            else:
                # Delete the entire collection (--clear-all behavior)
                # No orphan cleanup needed since entire collection is deleted
                self.client.delete_collection(collection_name=collection_name)

                return StorageResult(
                    success=True,
                    operation="clear_collection",
                    items_processed=1,
                    processing_time=time.time() - start_time,
                )

        except Exception as e:
            return StorageResult(
                success=False,
                operation="clear_collection",
                processing_time=time.time() - start_time,
                errors=[f"Failed to clear collection {collection_name}: {e}"],
            )

    def get_client_info(self) -> dict[str, Any]:
        """Get Qdrant client information."""
        try:
            info = self.client.get_telemetry()
            return {
                "url": self.url,
                "version": getattr(info, "version", "unknown"),
                "status": "connected",
                "timeout": self.timeout,
                "has_api_key": self.api_key is not None,
            }
        except Exception as e:
            return {
                "url": self.url,
                "status": "error",
                "error": str(e),
                "timeout": self.timeout,
                "has_api_key": self.api_key is not None,
            }

    def generate_deterministic_id(self, content: str) -> int:
        """Generate deterministic ID from content (same as base.py)."""
        import hashlib

        hash_hex = hashlib.sha256(content.encode()).hexdigest()[
            :16
        ]  # 16 chars = 64 bits
        return int(hash_hex, 16)

    def create_chunk_point(
        self, chunk: "EntityChunk", embedding: list[float], collection_name: str
    ) -> VectorPoint:
        """Create a vector point from an EntityChunk for progressive disclosure."""

        # Use the chunk's pre-defined ID format: "{file_id}::{entity_name}::{chunk_type}"
        point_id = self.generate_deterministic_id(chunk.id)

        # Create payload using the chunk's to_vector_payload method
        payload = chunk.to_vector_payload()
        payload["collection"] = collection_name
        payload["type"] = "chunk"  # Pure v2.4 format

        return VectorPoint(id=point_id, vector=embedding, payload=payload)

    def create_hybrid_chunk_point(
        self, 
        chunk: "EntityChunk", 
        dense_embedding: list[float], 
        sparse_embedding: list[float], 
        collection_name: str
    ) -> HybridVectorPoint:
        """Create a hybrid vector point from an EntityChunk with both dense and sparse embeddings.
        
        Args:
            chunk: EntityChunk to create point from
            dense_embedding: Dense vector embedding (e.g., from OpenAI/Voyage)
            sparse_embedding: Sparse vector embedding (e.g., from BM25)
            collection_name: Name of the collection
            
        Returns:
            HybridVectorPoint with both vector types
        """
        # Use the chunk's pre-defined ID format: "{file_id}::{entity_name}::{chunk_type}"
        point_id = self.generate_deterministic_id(chunk.id)

        # Create payload using the chunk's to_vector_payload method
        payload = chunk.to_vector_payload()
        payload["collection"] = collection_name
        payload["type"] = "chunk"  # Pure v2.4 format
        payload["vector_type"] = "hybrid"  # Mark as hybrid for identification

        return HybridVectorPoint(
            id=point_id, 
            dense_vector=dense_embedding, 
            sparse_vector=sparse_embedding, 
            payload=payload
        )

    def create_hybrid_relation_point(
        self, 
        relation: "Relation", 
        dense_embedding: list[float], 
        sparse_embedding: list[float], 
        collection_name: str
    ) -> HybridVectorPoint:
        """Create a hybrid vector point from a Relation with both dense and sparse embeddings.
        
        Args:
            relation: Relation to create point from
            dense_embedding: Dense vector embedding (e.g., from OpenAI/Voyage)
            sparse_embedding: Sparse vector embedding (e.g., from BM25)
            collection_name: Name of the collection
            
        Returns:
            HybridVectorPoint with both vector types
        """
        # Generate deterministic ID - include import_type to prevent deduplication
        import_type = (
            relation.metadata.get("import_type", "") if relation.metadata else ""
        )
        
        if import_type:
            relation_key = f"{relation.from_entity}-{relation.relation_type.value}-{relation.to_entity}-{import_type}"
        else:
            relation_key = f"{relation.from_entity}-{relation.relation_type.value}-{relation.to_entity}"
        
        point_id = self.generate_deterministic_id(relation_key)

        # Create payload - v2.4 format matching RelationChunk
        payload = {
            "entity_name": relation.from_entity,
            "relation_target": relation.to_entity,
            "relation_type": relation.relation_type.value,
            "collection": collection_name,
            "type": "chunk",
            "chunk_type": "relation",
            "entity_type": "relation",
            "vector_type": "hybrid",  # Mark as hybrid for identification
        }

        # Add optional metadata
        if relation.context:
            payload["context"] = relation.context
        if relation.confidence != 1.0:
            payload["confidence"] = relation.confidence
        if import_type:
            payload["import_type"] = import_type

        return HybridVectorPoint(
            id=point_id, 
            dense_vector=dense_embedding, 
            sparse_vector=sparse_embedding, 
            payload=payload
        )

    def create_relation_chunk_point(
        self, chunk: "RelationChunk", embedding: list[float], collection_name: str
    ) -> VectorPoint:
        """Create a vector point from a RelationChunk for v2.4 pure architecture."""

        # Use the chunk's pre-defined ID format: "{from_entity}::{relation_type}::{to_entity}"
        point_id = self.generate_deterministic_id(chunk.id)

        # Create payload using the chunk's to_vector_payload method
        payload = chunk.to_vector_payload()
        payload["collection"] = collection_name
        payload["type"] = "chunk"  # Pure v2.4 format

        return VectorPoint(id=point_id, vector=embedding, payload=payload)

    def create_chat_chunk_point(
        self, chunk: "ChatChunk", embedding: list[float], collection_name: str
    ) -> VectorPoint:
        """Create a vector point from a ChatChunk for v2.4 pure architecture."""

        # Use the chunk's pre-defined ID format: "chat::{chat_id}::{chunk_type}"
        point_id = self.generate_deterministic_id(chunk.id)

        # Create payload using the chunk's to_vector_payload method
        payload = chunk.to_vector_payload()
        payload["collection"] = collection_name
        payload["type"] = "chunk"  # Pure v2.4 format

        return VectorPoint(id=point_id, vector=embedding, payload=payload)

    def create_relation_point(
        self, relation: "Relation", embedding: list[float], collection_name: str
    ) -> VectorPoint:
        """Create a vector point from a relation."""

        # Generate deterministic ID - include import_type to prevent deduplication
        # Check if relation has import_type in metadata
        import_type = (
            relation.metadata.get("import_type", "") if relation.metadata else ""
        )
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
            "entity_type": "relation",
        }

        # Add optional metadata
        if relation.context:
            payload["context"] = relation.context
        if relation.confidence != 1.0:
            payload["confidence"] = relation.confidence
        # Add import_type if present
        if import_type:
            payload["import_type"] = import_type

        return VectorPoint(id=point_id, vector=embedding, payload=payload)

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
                            key="type", match=models.MatchValue(value="relation")
                        )
                    ]
                ),
                limit=1000,
                with_vectors=False,
                handle_pagination=True,
            )

            for point in points:
                name = point.payload.get("entity_name", point.payload.get("name", ""))
                if name:
                    entity_names.add(name)

        except Exception:
            # Log error but continue - empty set means no entities found
            pass

        return entity_names

    def _get_all_relations(self, collection_name: str) -> list:
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
                            key="chunk_type", match=models.MatchValue(value="relation")
                        )
                    ]
                ),
                limit=1000,
                with_vectors=False,
                handle_pagination=True,
            )

        except Exception:
            # Log error but continue - empty list means no relations found
            pass

        return relations

    def find_entities_for_file(
        self, collection_name: str, file_path: str
    ) -> list[dict[str, Any]]:
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
                            key="metadata.file_path", match=models.MatchValue(value=file_path)
                        ),
                        # Find File entities where entity_name = file_path (with fallback to name)
                        models.FieldCondition(
                            key="entity_name", match=models.MatchValue(value=file_path)
                        ),
                    ]
                ),
                limit=1000,
                with_vectors=False,
                handle_pagination=True,
            )

            results = []
            for point in points:
                results.append(
                    {
                        "id": point.id,
                        "name": point.payload.get(
                            "entity_name", point.payload.get("name", "Unknown")
                        ),
                        "type": point.payload.get("metadata", {}).get("entity_type", "unknown"),
                        "payload": point.payload,
                    }
                )

            return results

        except Exception:
            # Fallback to search_similar if scroll is not available
            return self._find_entities_for_file_fallback(collection_name, file_path)

    def _find_entities_for_file_fallback(
        self, collection_name: str, file_path: str
    ) -> list[dict[str, Any]]:
        """Fallback implementation using search_similar."""
        # Get actual vector size from collection info
        collection_info = self.get_collection_info(collection_name)
        vector_size = collection_info.get("vector_size", 1536)  # Default to 1536 if not found
        dummy_vector = [0.1] * vector_size
        results = []

        # Search for entities with file_path matching
        filter_path = {"file_path": file_path}
        search_result = self.search_similar(
            collection_name=collection_name,
            query_vector=dummy_vector,
            limit=1000,
            score_threshold=0.0,
            filter_conditions=filter_path,
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
            filter_conditions=filter_name,
        )
        if search_result.success:
            # Only add if not already in results (deduplication)
            existing_ids = {r["id"] for r in results}
            for result in search_result.results:
                if result["id"] not in existing_ids:
                    results.append(result)

        return results

    def find_entities_for_file_by_type(
        self, collection_name: str, file_path: str, chunk_types: list[str] = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Find entities for file grouped by chunk type for targeted replacement.

        Args:
            collection_name: Name of the collection to search
            file_path: Path of the file to find entities for
            chunk_types: List of chunk types to filter by. Defaults to ["metadata", "implementation", "relation"]

        Returns:
            Dictionary mapping chunk_type to list of entities:
            {"metadata": [...], "implementation": [...], "relation": [...]}
        """
        if chunk_types is None:
            chunk_types = ["metadata", "implementation", "relation"]

        results = {}

        try:
            from qdrant_client import models

            for chunk_type in chunk_types:
                filter_conditions = models.Filter(
                    must=[
                        models.FieldCondition(
                            key="chunk_type", match=models.MatchValue(value=chunk_type)
                        ),
                        models.FieldCondition(
                            key="metadata.file_path", match=models.MatchValue(value=file_path)
                        )
                    ]
                )

                points = self._scroll_collection(
                    collection_name=collection_name,
                    scroll_filter=filter_conditions,
                    limit=1000,
                    with_vectors=False,
                    handle_pagination=True,
                )

                results[chunk_type] = [
                    {
                        "id": point.id,
                        "entity_name": point.payload.get("entity_name", ""),
                        "entity_type": point.payload.get("metadata", {}).get("entity_type", "unknown"),
                        "chunk_type": chunk_type,
                        "payload": point.payload
                    }
                    for point in points
                ]

        except Exception as e:
            # Log error and return empty results for all chunk types
            if hasattr(self, 'logger') and self.logger:
                self.logger.error(f"Error in find_entities_for_file_by_type for {file_path}: {e}")
            results = {chunk_type: [] for chunk_type in chunk_types}

        return results

    def _should_run_cleanup(self, collection_name: str, force: bool = False) -> bool:
        """Check if orphan cleanup should run based on timer interval."""
        if force:
            return True

        # TEMPORARY FIX: Disable timer to always run cleanup (fixes stale relations bug)
        # TODO: Re-enable timer after fixing orphan cleanup scope limitations
        return True  # Always run cleanup for now

        # Load cleanup interval from config (default 1 minute) - COMMENTED OUT
        # try:
        #     from ..config.config_loader import ConfigLoader
        #     config = ConfigLoader().load()
        #     interval_minutes = getattr(config, "cleanup_interval_minutes", 1)
        # except Exception:
        #     interval_minutes = 1  # Fallback default
        #
        # # 0 means disabled timer (always run - original behavior)
        # if interval_minutes == 0:
        #     return True

        # Check last cleanup timestamp
        try:
            import json
            import time
            from pathlib import Path

            # Create a dummy indexer instance to access state methods
            # We need the project path - try to get it from config or use current dir
            project_path = Path.cwd()
            state_dir = project_path / ".claude-indexer"
            state_file = state_dir / f"{collection_name}.json"

            if not state_file.exists():
                return True  # No state file, run cleanup

            with open(state_file) as f:
                state = json.load(f)

            cleanup_state = state.get("_cleanup", {})
            last_cleanup = cleanup_state.get("last_cleanup_timestamp", 0)

            current_time = time.time()
            elapsed_minutes = (current_time - last_cleanup) / 60

            interval_minutes = 1  # Default cleanup interval
            return elapsed_minutes >= interval_minutes

        except Exception as e:
            logger.debug(
                f"Failed to check cleanup timer: {e}, defaulting to run cleanup"
            )
            return True  # On error, default to running cleanup

    def _update_cleanup_timestamp(self, collection_name: str):
        """Update the last cleanup timestamp in state file."""
        try:
            import json
            import time
            from pathlib import Path

            # Get state file path
            project_path = Path.cwd()
            state_dir = project_path / ".claude-indexer"
            state_file = state_dir / f"{collection_name}.json"

            # Load existing state or create new
            state = {}
            if state_file.exists():
                try:
                    with open(state_file) as f:
                        state = json.load(f)
                except (OSError, json.JSONDecodeError):
                    state = {}

            # Update cleanup timestamp
            if "_cleanup" not in state:
                state["_cleanup"] = {}
            state["_cleanup"]["last_cleanup_timestamp"] = time.time()

            # Atomic write
            state_dir.mkdir(parents=True, exist_ok=True)
            temp_file = state_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(state, f, indent=2)
            temp_file.rename(state_file)

        except Exception as e:
            logger.debug(f"Failed to update cleanup timestamp: {e}")

    def _cleanup_orphaned_relations(
        self, collection_name: str, verbose: bool = False, force: bool = False
    ) -> int:
        """Clean up relations that reference non-existent entities.

        Uses a single atomic query to get a consistent snapshot of the database,
        avoiding race conditions between entity and relation queries.

        Args:
            collection_name: Name of the collection to clean
            verbose: Whether to log detailed information about orphaned relations
            force: Whether to bypass timer and force cleanup

        Returns:
            Number of orphaned relations deleted
        """
        # Check timer first - skip cleanup if interval hasn't elapsed
        if not self._should_run_cleanup(collection_name, force):
            if verbose:
                logger.debug("⏱️ Skipping orphan cleanup - timer interval not elapsed")
            return 0

        if verbose:
            logger.debug("🔍 Scanning collection for orphaned relations...")

        # ENHANCED DEBUG: Always log key information for phantom relation debugging
        logger.debug(f"Starting cleanup for collection '{collection_name}' (force={force})")

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
                handle_pagination=True,
            )

            # Process in-memory to ensure consistency
            entity_names = set()
            relations = []
            entity_count = 0
            other_count = 0

            for point in all_points:
                # v2.4 format only: "type": "chunk", "chunk_type": "relation"
                if (
                    point.payload.get("type") == "chunk"
                    and point.payload.get("chunk_type") == "relation"
                ):
                    relations.append(point)
                else:
                    name = point.payload.get(
                        "entity_name", point.payload.get("name", "")
                    )
                    
                    if name:
                        entity_names.add(name)
                        entity_count += 1
                        
                        # For markdown entities with (+X more) suffix, add individual section names
                        if " (+" in name and name.endswith(" more)"):
                            sections = point.payload.get("headers", [])
                            metadata_headers = point.payload.get("metadata", {}).get("headers", [])
                            
                            # Try both locations for headers
                            actual_headers = sections or metadata_headers
                            if actual_headers:
                                for section in actual_headers:
                                    entity_names.add(section)
                    else:
                        other_count += 1
                        if verbose:
                            logger.debug(
                                f"   ⚠️ Point without name: type={point.payload.get('type')}, chunk_type={point.payload.get('chunk_type')}, keys={list(point.payload.keys())[:5]}"
                            )

            # ENHANCED DEBUG: Always log for debugging phantom relations
            logger.info(
                f"   📊 Found {entity_count} entities, {len(relations)} relations, {other_count} other points"
            )
            if entity_names:
                logger.debug(f"Sample entity names: {list(entity_names)[:5]}...")

            if verbose:
                logger.debug("   📊 Additional verbose details available")

            if not relations:
                if verbose:
                    logger.debug(
                        "   ✅ No relations found in collection - nothing to clean"
                    )
                return 0

            # Check each relation for orphaned references with consistent snapshot
            orphaned_relations = []
            phantom_relations = []  # NEW: Track phantom call relations
            valid_relations = 0
            file_ref_relations = 0

            # Build multiple indices for O(1) lookups
            # Index 1: Direct entity names
            entity_set = set(entity_names)

            # Index 2: File paths by basename (for module resolution)
            basename_to_paths = {}

            # Index 3: Directory paths (for package imports)
            directory_components = set()

            # Index 4: Full path components for complex module paths
            module_path_index = {}

            # Build indices from entity names
            for name in entity_names:
                if name.endswith(".py"):
                    # Extract basename without extension
                    import os

                    basename = os.path.basename(name)[:-3]  # Remove .py
                    if basename not in basename_to_paths:
                        basename_to_paths[basename] = []
                    basename_to_paths[basename].append(name)

                    # Extract all directory components
                    path_parts = name.replace("\\", "/").split("/")
                    directory_components.update(
                        path_parts[:-1]
                    )  # All parts except filename

                    # Build module path index
                    if len(path_parts) >= 2:
                        module_parts = [p for p in path_parts[:-1] if p]
                        if module_parts:
                            for i in range(len(module_parts)):
                                module_key = ".".join(module_parts[i:]) + "." + basename
                                if module_key not in module_path_index:
                                    module_path_index[module_key] = []
                                module_path_index[module_key].append(name)

            if verbose:
                logger.debug(
                    f"   📊 Built indices: {len(basename_to_paths)} basenames, {len(directory_components)} directories"
                )

            # Cache for module resolution results
            resolution_cache = {}
            resolve_call_count = 0

            def resolve_module_name(module_name: str) -> bool:
                """Optimized O(1) module resolution using pre-built indices."""
                nonlocal resolve_call_count
                resolve_call_count += 1

                # Check cache first
                if module_name in resolution_cache:
                    return resolution_cache[module_name]

                result = False

                # Direct entity name match
                if module_name in entity_set:
                    result = True

                # Handle relative imports (.chat.parser, ..config, etc.)
                elif module_name.startswith("."):
                    clean_name = module_name.lstrip(".")

                    # Check direct basename match
                    if clean_name in basename_to_paths:
                        result = True
                    elif "." in clean_name:
                        # Handle dot notation (chat.parser -> chat/parser.py)
                        last_part = clean_name.split(".")[-1]
                        if last_part in basename_to_paths:
                            # Check if any matching file has the expected path structure
                            path_pattern = clean_name.replace(".", "/")
                            for path in basename_to_paths[last_part]:
                                if path_pattern in path:
                                    result = True
                                    break

                # Handle absolute module paths (claude_indexer.analysis.entities)
                elif "." in module_name:
                    # Check module path index
                    if module_name in module_path_index:
                        result = True
                    else:
                        # Fallback: check if last part exists as a file
                        last_part = module_name.split(".")[-1]
                        if last_part in basename_to_paths:
                            result = True

                # Handle package-level imports (claude_indexer -> any /path/claude_indexer/* files)
                else:
                    # Single package name without dots
                    if module_name in directory_components:
                        result = True

                # Cache the result
                resolution_cache[module_name] = result
                return result

            # ENHANCED DEBUG: Always log sample relations for debugging
            if len(relations) > 0:
                logger.debug("Sample relations being checked:")
                for i, rel in enumerate(relations[:5]):  # Show 5 instead of 3
                    from_e = rel.payload.get("entity_name", "")
                    to_e = rel.payload.get("relation_target", "")
                    rel_type = rel.payload.get("relation_type", "")
                    imp_type = rel.payload.get("import_type", "none")
                    # logger.debug(
                    #     f"Relation {i+1}: {from_e} --{rel_type}--> {to_e} [import_type: {imp_type}]"
                    # )

            logger.debug(
                f"Checking {len(relations)} relations against {len(entity_names)} entities"
            )

            # Progress tracking
            import time

            start_time = time.time()
            last_log_time = start_time

            for idx, relation in enumerate(relations):
                # v2.4 relation format only
                from_entity = relation.payload.get("entity_name", "")
                to_entity = relation.payload.get("relation_target", "")

                # Check if either end of the relation references a non-existent entity
                # Use module resolution for better accuracy
                from_missing = (
                    from_entity not in entity_names
                    and not resolve_module_name(from_entity)
                )
                to_missing = to_entity not in entity_names and not resolve_module_name(
                    to_entity
                )

                # Determine if this is a file operation relation (target is external file)
                # Check for common file extensions to identify external file references
                is_file_reference = False
                if to_entity and "." in to_entity:
                    extension = to_entity.split(".")[-1].lower()
                    file_extensions = {
                        "json",
                        "csv",
                        "txt",
                        "xml",
                        "yaml",
                        "yml",
                        "xlsx",
                        "xls",
                        "ini",
                        "toml",
                        "html",
                        "css",
                        "log",
                        "md",
                        "pdf",
                        "doc",
                        "docx",
                        "png",
                        "jpg",
                        "jpeg",
                        "gif",
                        "svg",
                        "bin",
                        "dat",
                    }
                    is_file_reference = extension in file_extensions

                # Only mark as orphaned if:
                # 1. Source entity is missing (always invalid)
                # 2. Target is missing AND it's an internal entity (not external file)
                if from_missing:
                    orphaned_relations.append(relation)
                    # ALWAYS log orphan deletions for investigation
                    logger.info(
                        f"   🔍 ORPHAN (source missing): {from_entity} -> {to_entity}"
                    )
                elif to_missing and not is_file_reference:
                    orphaned_relations.append(relation)
                    # ALWAYS log orphan deletions for investigation
                    imp_type = relation.payload.get("import_type", "none")
                    logger.info(
                        f"   🔍 ORPHAN (target missing): {from_entity} -> {to_entity} [import_type: {imp_type}]"
                    )
                else:
                    # NEW: Check for phantom call relations (both entities exist but call is stale)
                    relation_type = relation.payload.get("relation_type", "")
                    if relation_type == "calls" and not from_missing and not to_missing:
                        # Both entities exist but we need to verify the call still exists in implementation
                        is_phantom = self._is_phantom_call_relation(
                            all_points, from_entity, to_entity, collection_name
                        )
                        if is_phantom:
                            phantom_relations.append(relation)
                            logger.debug(
                                f"PHANTOM (stale call): {from_entity} -> {to_entity}"
                            )
                        else:
                            valid_relations += 1
                    else:
                        valid_relations += 1
                        if is_file_reference:
                            file_ref_relations += 1
                            if (
                                verbose and file_ref_relations <= 5
                            ):  # Log first few file refs
                                imp_type = relation.payload.get("import_type", "none")
                                logger.debug(
                                    f"   ✅ VALID file ref: {from_entity} -> {to_entity} [import_type: {imp_type}]"
                                )

                # Log progress every 5 seconds or every 1000 relations
                current_time = time.time()
                if idx > 0 and (idx % 1000 == 0 or current_time - last_log_time > 5):
                    elapsed = current_time - start_time
                    rate = idx / elapsed if elapsed > 0 else 0
                    eta = (len(relations) - idx) / rate if rate > 0 else 0
                    logger.debug(
                        f"   ⏳ Progress: {idx}/{len(relations)} relations ({idx / len(relations) * 100:.1f}%) - "
                        f"{rate:.0f} relations/sec - ETA: {eta:.0f}s - resolve_calls: {resolve_call_count}"
                    )
                    last_log_time = current_time

            # Final timing stats
            total_time = time.time() - start_time

            if verbose:
                logger.debug("   🧹 Orphan cleanup summary:")
                logger.debug(f"      Total relations: {len(relations)}")
                logger.debug(f"      Valid relations: {valid_relations}")
                logger.debug(f"      File references: {file_ref_relations}")
                logger.debug(f"      Orphans found: {len(orphaned_relations)}")
                logger.debug(f"      Total time: {total_time:.2f}s")
                logger.debug(f"      Relations/sec: {len(relations) / total_time:.0f}")
                logger.debug(f"      resolve_module_name calls: {resolve_call_count}")
                logger.debug(
                    f"      Cache hit rate: {(len(resolution_cache) - resolve_call_count) / resolve_call_count * 100:.1f}%"
                    if resolve_call_count > 0
                    else "N/A"
                )

            # Combine orphaned and phantom relations for deletion
            all_stale_relations = orphaned_relations + phantom_relations
            total_to_delete = len(all_stale_relations)

            # Batch delete stale relations if found
            if all_stale_relations:
                relation_ids = [r.id for r in all_stale_relations]
                delete_result = self.delete_points(collection_name, relation_ids)

                if delete_result.success:
                    # ENHANCED DEBUG: Always log successful deletions
                    logger.info(
                        f"🗑️  Successfully deleted {total_to_delete} stale relations: {len(orphaned_relations)} orphaned + {len(phantom_relations)} phantom"
                    )
                    if verbose:
                        logger.debug(
                            f"🗑️  Deleted {total_to_delete} relations ({len(orphaned_relations)} orphaned + {len(phantom_relations)} phantom)"
                        )
                    # Update cleanup timestamp after successful cleanup
                    self._update_cleanup_timestamp(collection_name)
                    return total_to_delete
                else:
                    logger.debug(
                        f"❌ Failed to delete stale relations: {delete_result.errors}"
                    )
                    return 0
            else:
                if verbose:
                    logger.debug("   No stale relations found")
                # Update cleanup timestamp even when no stale relations found (successful scan)
                self._update_cleanup_timestamp(collection_name)
                return 0

        except Exception as e:
            logger.debug(f"❌ Error during orphaned relation cleanup: {e}")
            return 0

    def _is_phantom_call_relation(
        self, all_points: list, from_entity: str, to_entity: str, collection_name: str  # noqa: ARG002
    ) -> bool:
        """Check if a call relation is phantom (entities exist but call doesn't).

        Args:
            all_points: All points from the collection for efficient lookup
            from_entity: Source entity name
            to_entity: Target entity name
            collection_name: Collection name for debugging

        Returns:
            True if the call relation is phantom (stale), False if legitimate
        """
        try:
            # Find implementation chunks for the source entity
            source_implementations = [
                point for point in all_points
                if (point.payload.get("entity_name") == from_entity
                    and point.payload.get("chunk_type") == "implementation")
            ]

            if not source_implementations:
                # No implementation found - might be external entity, keep relation
                logger.debug(f"   🔍 No implementation found for {from_entity}, keeping relation")
                return False

            # Check if any implementation chunk contains the function call
            for impl_point in source_implementations:
                content = impl_point.payload.get("content", "")

                # Simple heuristic: look for the target function being called in the content
                # This catches most cases like: load_user_data(username, path)
                if to_entity in content:
                    # Additional check: make sure it's actually a function call, not just a comment
                    lines = content.split('\n')
                    for line in lines:
                        # Skip comments and strings
                        if '#' in line:
                            comment_index = line.find('#')
                            code_part = line[:comment_index]
                        else:
                            code_part = line

                        # Look for function call pattern: target_function(
                        if f"{to_entity}(" in code_part:
                            # logger.debug(f"   ✅ Found legitimate call: {from_entity} -> {to_entity}")
                            return False

            # No legitimate call found in any implementation
            logger.debug(f"   👻 Phantom call detected: {from_entity} -> {to_entity} (call not found in implementation)")
            return True

        except Exception as e:
            logger.debug(f"   ⚠️ Error checking phantom relation {from_entity} -> {to_entity}: {e}")
            # On error, keep the relation (safer than deleting)
            return False
