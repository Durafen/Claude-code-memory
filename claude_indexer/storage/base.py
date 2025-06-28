"""Base classes and interfaces for vector storage."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Union
from pathlib import Path
import hashlib


@dataclass
class StorageResult:
    """Result of a storage operation."""
    
    success: bool
    operation: str  # "create", "update", "delete", "search"
    
    # Operation-specific data
    items_processed: int = 0
    items_failed: int = 0
    processing_time: float = 0.0
    
    # For search operations
    results: List[Dict[str, Any]] = None
    total_found: int = 0
    
    # Error information
    errors: List[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.results is None:
            self.results = []
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
    
    @property
    def has_errors(self) -> bool:
        """Check if there were any errors."""
        return len(self.errors) > 0 or self.items_failed > 0
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate for batch operations."""
        total = self.items_processed + self.items_failed
        if total == 0:
            return 1.0
        return self.items_processed / total


@dataclass
class VectorPoint:
    """Represents a point in vector space."""
    
    id: Union[str, int]
    vector: List[float]
    payload: Dict[str, Any]
    
    def __post_init__(self):
        # Handle both lists and numpy arrays
        if hasattr(self.vector, '__len__'):
            if len(self.vector) == 0:
                raise ValueError("Vector cannot be empty")
        else:
            if not self.vector:
                raise ValueError("Vector cannot be empty")
        if not isinstance(self.payload, dict):
            raise ValueError("Payload must be a dictionary")


class VectorStore(ABC):
    """Abstract base class for vector storage backends."""
    
    @abstractmethod
    def create_collection(self, collection_name: str, vector_size: int, 
                         distance_metric: str = "cosine") -> StorageResult:
        """Create a new collection."""
        pass
    
    @abstractmethod
    def collection_exists(self, collection_name: str) -> bool:
        """Check if collection exists."""
        pass
    
    @abstractmethod
    def delete_collection(self, collection_name: str) -> StorageResult:
        """Delete a collection."""
        pass
    
    @abstractmethod
    def upsert_points(self, collection_name: str, points: List[VectorPoint]) -> StorageResult:
        """Insert or update points in the collection."""
        pass
    
    @abstractmethod
    def delete_points(self, collection_name: str, point_ids: List[Union[str, int]]) -> StorageResult:
        """Delete points by their IDs."""
        pass
    
    @abstractmethod
    def search_similar(self, collection_name: str, query_vector: List[float],
                      limit: int = 10, score_threshold: float = 0.0,
                      filter_conditions: Dict[str, Any] = None) -> StorageResult:
        """Search for similar vectors."""
        pass
    
    @abstractmethod
    def get_collection_info(self, collection_name: str) -> Dict[str, Any]:
        """Get information about a collection."""
        pass
    
    @abstractmethod
    def list_collections(self) -> List[str]:
        """List all collections."""
        pass
    
    def generate_deterministic_id(self, content: str) -> int:
        """Generate deterministic ID from content."""
        hash_hex = hashlib.sha256(content.encode()).hexdigest()[:8]
        return int(hash_hex, 16)
    
    def batch_upsert(self, collection_name: str, points: List[VectorPoint],
                    batch_size: int = 100) -> StorageResult:
        """Upsert points in batches."""
        import time
        
        start_time = time.time()
        total_processed = 0
        total_failed = 0
        all_errors = []
        
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            
            try:
                result = self.upsert_points(collection_name, batch)
                total_processed += result.items_processed
                total_failed += result.items_failed
                all_errors.extend(result.errors)
            except Exception as e:
                total_failed += len(batch)
                all_errors.append(f"Batch {i//batch_size + 1} failed: {e}")
        
        return StorageResult(
            success=total_failed == 0,
            operation="batch_upsert",
            items_processed=total_processed,
            items_failed=total_failed,
            processing_time=time.time() - start_time,
            errors=all_errors
        )


class ManagedVectorStore(VectorStore):
    """Vector store with automatic collection management."""
    
    def __init__(self, auto_create_collections: bool = True, 
                 default_vector_size: int = 1536):
        self.auto_create_collections = auto_create_collections
        self.default_vector_size = default_vector_size
    
    def ensure_collection(self, collection_name: str, vector_size: int = None) -> bool:
        """Ensure collection exists, create if necessary."""
        if self.collection_exists(collection_name):
            return True
        
        if not self.auto_create_collections:
            return False
        
        vector_size = vector_size or self.default_vector_size
        result = self.create_collection(collection_name, vector_size)
        return result.success
    
    def upsert_points(self, collection_name: str, points: List[VectorPoint]) -> StorageResult:
        """Upsert points with automatic collection creation."""
        # Ensure collection exists
        if points and not self.ensure_collection(collection_name, len(points[0].vector)):
            return StorageResult(
                success=False,
                operation="upsert",
                errors=[f"Collection {collection_name} does not exist and auto-creation is disabled"]
            )
        
        return super().upsert_points(collection_name, points)


class CachingVectorStore(VectorStore):
    """Vector store with result caching."""
    
    def __init__(self, backend: VectorStore, max_cache_size: int = 1000):
        self.backend = backend
        self.max_cache_size = max_cache_size
        self._search_cache: Dict[str, StorageResult] = {}
    
    def _get_search_cache_key(self, collection_name: str, query_vector: List[float],
                             limit: int, score_threshold: float, 
                             filter_conditions: Dict[str, Any]) -> str:
        """Generate cache key for search query."""
        # Create a simplified hash of the query parameters
        query_str = f"{collection_name}:{len(query_vector)}:{limit}:{score_threshold}"
        if filter_conditions:
            query_str += f":{hash(str(sorted(filter_conditions.items())))}"
        return hashlib.sha256(query_str.encode()).hexdigest()[:16]
    
    def search_similar(self, collection_name: str, query_vector: List[float],
                      limit: int = 10, score_threshold: float = 0.0,
                      filter_conditions: Dict[str, Any] = None) -> StorageResult:
        """Search with caching."""
        cache_key = self._get_search_cache_key(
            collection_name, query_vector, limit, score_threshold, filter_conditions
        )
        
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]
        
        result = self.backend.search_similar(
            collection_name, query_vector, limit, score_threshold, filter_conditions
        )
        
        # Cache successful results
        if result.success and len(self._search_cache) < self.max_cache_size:
            self._search_cache[cache_key] = result
        
        return result
    
    # Delegate all other methods to backend
    def create_collection(self, collection_name: str, vector_size: int, 
                         distance_metric: str = "cosine") -> StorageResult:
        return self.backend.create_collection(collection_name, vector_size, distance_metric)
    
    def collection_exists(self, collection_name: str) -> bool:
        return self.backend.collection_exists(collection_name)
    
    def delete_collection(self, collection_name: str) -> StorageResult:
        # Clear cache when collection is deleted
        self._search_cache.clear()
        return self.backend.delete_collection(collection_name)
    
    def upsert_points(self, collection_name: str, points: List[VectorPoint]) -> StorageResult:
        # Clear cache when data is modified
        self._search_cache.clear()
        return self.backend.upsert_points(collection_name, points)
    
    def delete_points(self, collection_name: str, point_ids: List[Union[str, int]]) -> StorageResult:
        # Clear cache when data is modified
        self._search_cache.clear()
        return self.backend.delete_points(collection_name, point_ids)
    
    def get_collection_info(self, collection_name: str) -> Dict[str, Any]:
        return self.backend.get_collection_info(collection_name)
    
    def list_collections(self) -> List[str]:
        return self.backend.list_collections()
    
    # Delegate custom Qdrant methods
    def create_entity_point(self, entity, embedding: List[float], collection_name: str):
        """Delegate entity point creation to backend."""
        if hasattr(self.backend, 'create_entity_point'):
            return self.backend.create_entity_point(entity, embedding, collection_name)
        else:
            raise AttributeError(f"Backend {type(self.backend)} does not support create_entity_point")
    
    def create_relation_point(self, relation, embedding: List[float], collection_name: str):
        """Delegate relation point creation to backend."""  
        if hasattr(self.backend, 'create_relation_point'):
            return self.backend.create_relation_point(relation, embedding, collection_name)
        else:
            raise AttributeError(f"Backend {type(self.backend)} does not support create_relation_point")
    
    def create_chunk_point(self, chunk, embedding: List[float], collection_name: str):
        """Delegate chunk point creation to backend for progressive disclosure."""
        if hasattr(self.backend, 'create_chunk_point'):
            return self.backend.create_chunk_point(chunk, embedding, collection_name)
        else:
            raise AttributeError(f"Backend {type(self.backend)} does not support create_chunk_point")
    
    def create_relation_chunk_point(self, chunk, embedding: List[float], collection_name: str):
        """Delegate relation chunk point creation to backend for v2.4 pure architecture."""
        if hasattr(self.backend, 'create_relation_chunk_point'):
            return self.backend.create_relation_chunk_point(chunk, embedding, collection_name)
        else:
            raise AttributeError(f"Backend {type(self.backend)} does not support create_relation_chunk_point")
    
    def create_chat_chunk_point(self, chunk, embedding: List[float], collection_name: str):
        """Delegate chat chunk point creation to backend for v2.4 pure architecture."""
        if hasattr(self.backend, 'create_chat_chunk_point'):
            return self.backend.create_chat_chunk_point(chunk, embedding, collection_name)
        else:
            raise AttributeError(f"Backend {type(self.backend)} does not support create_chat_chunk_point")
    
    def generate_deterministic_id(self, content: str) -> str:
        """Delegate deterministic ID generation to backend."""
        if hasattr(self.backend, 'generate_deterministic_id'):
            return self.backend.generate_deterministic_id(content)
        else:
            raise AttributeError(f"Backend {type(self.backend)} does not support generate_deterministic_id")
    
    def clear_collection(self, collection_name: str, preserve_manual: bool = False):
        """Delegate collection clearing to backend."""
        # Clear cache when collection is cleared
        self._search_cache.clear()
        if hasattr(self.backend, 'clear_collection'):
            return self.backend.clear_collection(collection_name, preserve_manual=preserve_manual)
        else:
            raise AttributeError(f"Backend {type(self.backend)} does not support clear_collection")
    
    def find_entities_for_file(self, collection_name: str, file_path: str):
        """Delegate find entities for file to backend"""
        if hasattr(self.backend, 'find_entities_for_file'):
            return self.backend.find_entities_for_file(collection_name, file_path)
        else:
            # Fallback implementation using search_similar
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
            
            # Search for File entities where name = file_path
            filter_name = {"name": file_path}
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
    
    def _cleanup_orphaned_relations(self, collection_name: str, verbose: bool = False):
        """Delegate orphaned relation cleanup to backend"""
        if hasattr(self.backend, '_cleanup_orphaned_relations'):
            return self.backend._cleanup_orphaned_relations(collection_name, verbose)
        else:
            raise AttributeError(f"Backend {type(self.backend)} does not support _cleanup_orphaned_relations")