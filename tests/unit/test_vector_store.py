"""Unit tests for vector storage functionality."""

import pytest
import numpy as np
from unittest.mock import Mock, patch, MagicMock
from typing import List, Dict, Any

from claude_indexer.storage.qdrant import QdrantStore
from claude_indexer.storage.base import VectorPoint, StorageResult


class TestQdrantStore:
    """Test Qdrant vector store functionality."""
    
    def test_initialization_with_connection(self):
        """Test QdrantStore initialization with successful connection."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                # Mock successful connection
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client_class.return_value = mock_client
                
                # Load real API key from settings.txt
                from claude_indexer.config import load_config
                real_config = load_config()
                store = QdrantStore(url="http://localhost:6333", api_key=real_config.qdrant_api_key)
                
                assert store.url == "http://localhost:6333"
                assert store.api_key == real_config.qdrant_api_key
                mock_client_class.assert_called_once_with(
                    url="http://localhost:6333",
                    api_key=real_config.qdrant_api_key, 
                    timeout=60.0
                )
    
    def test_initialization_connection_error(self):
        """Test initialization with connection error."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                # Mock connection failure
                mock_client_class.side_effect = Exception("Connection failed")
                
                with pytest.raises(ConnectionError, match="Failed to connect to Qdrant"):
                    QdrantStore(url="http://localhost:6333")
    
    def test_initialization_qdrant_unavailable(self):
        """Test initialization when Qdrant client unavailable."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', False):
            with pytest.raises(ImportError, match="Qdrant client not available"):
                QdrantStore()
    
    def test_distance_metrics_configuration(self):
        """Test distance metrics mapping."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient'):
                store = QdrantStore()
                
                assert "cosine" in store.DISTANCE_METRICS
                assert "euclidean" in store.DISTANCE_METRICS
                assert "dot" in store.DISTANCE_METRICS
    
    def test_create_collection_success(self):
        """Test successful collection creation."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client.create_collection.return_value = True
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                result = store.create_collection("test_collection", 1536, "cosine")
                
                assert result.success
                assert result.operation == "create_collection"
                assert result.items_processed == 1
                mock_client.create_collection.assert_called_once()
    
    def test_create_collection_invalid_distance_metric(self):
        """Test collection creation with invalid distance metric."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient'):
                store = QdrantStore()
                result = store.create_collection("test_collection", 1536, "invalid_metric")
                
                assert not result.success
                assert "Invalid distance metric" in result.errors[0]
    
    def test_create_collection_api_error(self):
        """Test collection creation with API error."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client.create_collection.side_effect = Exception("API Error")
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                result = store.create_collection("test_collection", 1536)
                
                assert not result.success
                assert "Failed to create collection" in result.errors[0]
    
    def test_collection_exists(self):
        """Test checking if collection exists."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_collections = MagicMock()
                # Create mock collection objects with name attributes
                mock_collection1 = MagicMock()
                mock_collection1.name = "existing_collection"
                mock_collection2 = MagicMock()
                mock_collection2.name = "another_collection"
                mock_collections.collections = [mock_collection1, mock_collection2]
                mock_client.get_collections.return_value = mock_collections
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                
                assert store.collection_exists("existing_collection")
                assert not store.collection_exists("nonexistent_collection")
    
    def test_collection_exists_error_handling(self):
        """Test collection existence check with API error."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                # First call (initialization) succeeds, second call (collection_exists) fails
                mock_client.get_collections.side_effect = [MagicMock(), Exception("API Error")]
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                
                # Should return False on error
                assert not store.collection_exists("any_collection")
    
    def test_delete_collection_success(self):
        """Test successful collection deletion."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client.delete_collection.return_value = True
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                result = store.delete_collection("test_collection")
                
                assert result.success
                assert result.operation == "delete_collection"
                mock_client.delete_collection.assert_called_once_with(
                    collection_name="test_collection"
                )
    
    def test_upsert_points_success(self):
        """Test successful point insertion."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client.upsert.return_value = True
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                
                # Mock collection existence
                store.ensure_collection = MagicMock(return_value=True)
                
                # Create test points
                points = [
                    VectorPoint(
                        id="test_1",
                        vector=list(np.random.rand(1536).astype(np.float32)),
                        payload={"text": "test text 1", "type": "entity"}
                    ),
                    VectorPoint(
                        id="test_2", 
                        vector=list(np.random.rand(1536).astype(np.float32)),
                        payload={"text": "test text 2", "type": "relation"}
                    )
                ]
                
                result = store.upsert_points("test_collection", points)
                
                assert result.success
                assert result.operation == "upsert"
                assert result.items_processed == 2
                mock_client.upsert.assert_called_once()
    
    def test_upsert_points_empty_list(self):
        """Test upserting empty list of points."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient'):
                store = QdrantStore()
                
                result = store.upsert_points("test_collection", [])
                
                assert result.success
                assert result.items_processed == 0
    
    def test_upsert_points_collection_not_exists(self):
        """Test upserting when collection doesn't exist."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient'):
                store = QdrantStore()
                
                # Mock collection doesn't exist
                store.ensure_collection = MagicMock(return_value=False)
                
                points = [VectorPoint(id=1, vector=[1.0] * 1536, payload={})]
                result = store.upsert_points("test_collection", points)
                
                assert not result.success
                assert "does not exist" in result.errors[0]
    
    def test_delete_points_success(self):
        """Test successful point deletion."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client.delete.return_value = True
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                point_ids = ["test_1", "test_2", "test_3"]
                
                result = store.delete_points("test_collection", point_ids)
                
                assert result.success
                assert result.operation == "delete"
                assert result.items_processed == 3
                mock_client.delete.assert_called_once_with(
                    collection_name="test_collection",
                    points_selector=point_ids
                )
    
    def test_search_similar_success(self):
        """Test successful similarity search."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                
                # Mock search results
                mock_result1 = MagicMock()
                mock_result1.id = "result_1"
                mock_result1.score = 0.95
                mock_result1.payload = {"text": "similar text 1", "type": "entity"}
                
                mock_result2 = MagicMock()
                mock_result2.id = "result_2"
                mock_result2.score = 0.87
                mock_result2.payload = {"text": "similar text 2", "type": "relation"}
                
                mock_client.search.return_value = [mock_result1, mock_result2]
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                query_vector = list(np.random.rand(1536).astype(np.float32))
                
                result = store.search_similar("test_collection", query_vector, limit=5)
                
                assert result.success
                assert result.operation == "search"
                assert result.total_found == 2
                assert len(result.results) == 2
                
                # Check first result
                first_result = result.results[0]
                assert first_result["id"] == "result_1"
                assert first_result["score"] == 0.95
                assert first_result["payload"]["text"] == "similar text 1"
    
    def test_search_similar_with_filter(self):
        """Test similarity search with filter conditions."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client.search.return_value = []
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                query_vector = list(np.random.rand(1536).astype(np.float32))
                filter_conditions = {"type": "entity", "collection": "test"}
                
                result = store.search_similar(
                    "test_collection", 
                    query_vector, 
                    limit=10,
                    filter_conditions=filter_conditions
                )
                
                assert result.success
                # Verify filter was passed to search
                call_args = mock_client.search.call_args
                assert call_args.kwargs["query_filter"] is not None
    
    def test_get_collection_info_success(self):
        """Test getting collection information."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                
                # Mock collection info
                mock_info = MagicMock()
                mock_info.status.value = "green"
                mock_info.config.params.vectors.size = 1536
                mock_info.config.params.vectors.distance.value = "cosine"
                mock_info.points_count = 1000
                mock_info.indexed_vectors_count = 1000
                mock_info.segments_count = 2
                
                mock_client.get_collection.return_value = mock_info
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                info = store.get_collection_info("test_collection")
                
                assert info["name"] == "test_collection"
                assert info["status"] == "green"
                assert info["vector_size"] == 1536
                assert info["distance_metric"] == "cosine"
                assert info["points_count"] == 1000
    
    def test_get_collection_info_error(self):
        """Test getting collection info with error."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client.get_collection.side_effect = Exception("Collection not found")
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                info = store.get_collection_info("nonexistent_collection")
                
                assert info["name"] == "nonexistent_collection"
                assert "error" in info
                assert "Collection not found" in info["error"]
    
    def test_list_collections(self):
        """Test listing all collections."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                
                # Mock collections list
                mock_collections = MagicMock()
                # Create mock collections with .name attributes
                mock_col1 = MagicMock()
                mock_col1.name = "collection_1"
                mock_col2 = MagicMock()
                mock_col2.name = "collection_2"
                mock_col3 = MagicMock()
                mock_col3.name = "collection_3"
                mock_collections.collections = [mock_col1, mock_col2, mock_col3]
                # First call (initialization) returns empty list, second call returns collections
                mock_client.get_collections.side_effect = [MagicMock(), mock_collections]
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                collections = store.list_collections()
                
                assert len(collections) == 3
                assert "collection_1" in collections
                assert "collection_2" in collections
                assert "collection_3" in collections
    
    def test_list_collections_error(self):
        """Test listing collections with error."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                # First call (initialization) succeeds, second call (list_collections) fails
                mock_client.get_collections.side_effect = [MagicMock(), Exception("API Error")]
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                collections = store.list_collections()
                
                assert collections == []
    
    def test_get_client_info_success(self):
        """Test getting client information."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                
                mock_telemetry = MagicMock()
                mock_telemetry.version = "1.9.0"
                mock_client.get_telemetry.return_value = mock_telemetry
                mock_client_class.return_value = mock_client
                
                # Load real API key from settings.txt
                from claude_indexer.config import load_config
                real_config = load_config()
                store = QdrantStore(url="http://test:6333", api_key=real_config.qdrant_api_key)
                info = store.get_client_info()
                
                assert info["url"] == "http://test:6333"
                assert info["version"] == "1.9.0"
                assert info["status"] == "connected"
                assert info["has_api_key"] is True
    
    def test_get_client_info_error(self):
        """Test getting client info with error."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client.get_telemetry.side_effect = Exception("Connection error")
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                info = store.get_client_info()
                
                assert info["status"] == "error"
                assert "Connection error" in info["error"]
    
    def test_clear_collection_success(self):
        """Test clearing collection successfully."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                mock_client.delete_collection.return_value = True
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                
                # Mock collection exists
                store.collection_exists = MagicMock(return_value=True)
                
                result = store.clear_collection("test_collection", preserve_manual=False)
                
                assert result.success
                assert result.operation == "clear_collection"
                mock_client.delete_collection.assert_called_once_with(
                    collection_name="test_collection"
                )
    
    def test_clear_collection_not_exists(self):
        """Test clearing non-existent collection."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient'):
                store = QdrantStore()
                
                # Mock collection doesn't exist
                store.collection_exists = MagicMock(return_value=False)
                
                result = store.clear_collection("nonexistent_collection")
                
                assert result.success
                assert len(result.warnings) > 0
                assert "doesn't exist" in result.warnings[0]
    
    def test_clear_collection_preserve_manual(self):
        """Test clearing collection while preserving manual memories."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                
                # Mock count operations - before: 100, after: 25 (preserved manual memories)
                mock_count = MagicMock()
                mock_count.count = 100  # Initial count
                mock_client.count.side_effect = [mock_count, MagicMock(count=25)]  # Before and after
                
                # Mock scroll to return points with file_path (code-indexed) and without (manual)
                mock_code_point = MagicMock()
                mock_code_point.id = "code_point_1"
                mock_code_point.payload = {"file_path": "/path/to/file.py", "name": "function"}
                
                mock_manual_point = MagicMock()
                mock_manual_point.id = "manual_point_1" 
                mock_manual_point.payload = {"name": "manual_memory"}  # No file_path
                
                # scroll() returns (points, next_page_offset)
                mock_client.scroll.return_value = ([mock_code_point, mock_manual_point], None)
                
                mock_client.delete.return_value = True
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                
                # Mock collection exists
                store.collection_exists = MagicMock(return_value=True)
                
                result = store.clear_collection("test_collection", preserve_manual=True)
                
                assert result.success
                assert result.operation == "clear_collection"
                assert result.items_processed == 75  # 100 - 25 = 75 deleted
                assert len(result.warnings) > 0
                assert "Preserved 25 manual memories" in result.warnings[0]
                
                # Verify selective deletion was called (not full collection deletion)
                mock_client.delete.assert_called_once()
                mock_client.delete_collection.assert_not_called()
                
                # Verify that only the code-indexed point was deleted
                call_args = mock_client.delete.call_args
                assert call_args[1]['collection_name'] == "test_collection"
                assert call_args[1]['points_selector'] == ["code_point_1"]
                assert call_args[1]['wait'] is True
    
    def test_clear_collection_preserve_manual_no_code_points(self):
        """Test clearing collection when there are no code-indexed points."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            with patch('claude_indexer.storage.qdrant.QdrantClient') as mock_client_class:
                mock_client = MagicMock()
                mock_client.get_collections.return_value = MagicMock()
                
                # Mock count operations - same count before and after (no deletions)
                mock_count = MagicMock()
                mock_count.count = 25  # All manual memories
                mock_client.count.side_effect = [mock_count, MagicMock(count=25)]  # Before and after
                
                # Mock scroll to return only manual points (no file_path)
                mock_manual_point1 = MagicMock()
                mock_manual_point1.id = "manual_point_1" 
                mock_manual_point1.payload = {"name": "manual_memory_1"}  # No file_path
                
                mock_manual_point2 = MagicMock()
                mock_manual_point2.id = "manual_point_2" 
                mock_manual_point2.payload = {"name": "manual_memory_2"}  # No file_path
                
                # scroll() returns (points, next_page_offset) 
                mock_client.scroll.return_value = ([mock_manual_point1, mock_manual_point2], None)
                
                mock_client.delete.return_value = True
                mock_client_class.return_value = mock_client
                
                store = QdrantStore()
                
                # Mock collection exists
                store.collection_exists = MagicMock(return_value=True)
                
                result = store.clear_collection("test_collection", preserve_manual=True)
                
                assert result.success
                assert result.operation == "clear_collection"
                assert result.items_processed == 0  # No items deleted
                assert len(result.warnings) > 0
                assert "Preserved 25 manual memories" in result.warnings[0]
                
                # Verify delete was NOT called since no code-indexed points were found
                mock_client.delete.assert_not_called()
                mock_client.delete_collection.assert_not_called()

    @patch('claude_indexer.storage.qdrant.QdrantClient')
    def test_cleanup_orphaned_relations_success(self, mock_client_class):
        """Test successful cleanup of orphaned relations."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.get_collections.return_value = MagicMock()
            
            store = QdrantStore()
            
            # Mock existing entities (v2.4 format)
            mock_entity_points = [
                MagicMock(payload={"entity_name": "entity1", "type": "chunk", "chunk_type": "metadata"}),
                MagicMock(payload={"entity_name": "entity2", "type": "chunk", "chunk_type": "metadata"}),
                MagicMock(payload={"entity_name": "entity3", "type": "chunk", "chunk_type": "metadata"})
            ]
            
            # Mock relations - some orphaned, some valid (v2.4 format)
            mock_relation_points = [
                MagicMock(id="rel1", payload={"type": "chunk", "chunk_type": "relation", "entity_name": "entity1", "relation_target": "entity2"}),  # Valid
                MagicMock(id="rel2", payload={"type": "chunk", "chunk_type": "relation", "entity_name": "entity1", "relation_target": "deleted_entity"}),  # Orphaned
                MagicMock(id="rel3", payload={"type": "chunk", "chunk_type": "relation", "entity_name": "deleted_entity2", "relation_target": "entity3"}),  # Orphaned
                MagicMock(id="rel4", payload={"type": "chunk", "chunk_type": "relation", "entity_name": "entity2", "relation_target": "entity3"})  # Valid
            ]
            
            # Mock the _scroll_collection method directly
            with patch.object(store, '_scroll_collection') as mock_scroll_collection, \
                 patch.object(store, 'collection_exists', return_value=True), \
                 patch.object(store, 'delete_points') as mock_delete_points:
                
                # The new implementation gets all points in one call and processes them
                all_points = mock_entity_points + mock_relation_points
                mock_scroll_collection.return_value = all_points
                
                mock_delete_points.return_value = StorageResult(
                    success=True,
                    operation="delete",
                    items_processed=2
                )
                
                # Execute cleanup
                result = store._cleanup_orphaned_relations("test_collection", verbose=True)
                
                # Verify results
                assert result == 2  # 2 orphaned relations deleted
                
                # Verify delete was called with orphaned relation IDs
                mock_delete_points.assert_called_once_with("test_collection", ["rel2", "rel3"])
                
                # Verify scroll was called once (unified approach)
                assert mock_scroll_collection.call_count == 1

    @patch('claude_indexer.storage.qdrant.QdrantClient')
    def test_cleanup_orphaned_relations_no_orphans(self, mock_client_class):
        """Test cleanup when no orphaned relations exist."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.get_collections.return_value = MagicMock()
            
            store = QdrantStore()
            
            # Mock existing entities
            mock_entity_points = [
                MagicMock(payload={"name": "entity1"}),
                MagicMock(payload={"name": "entity2"})
            ]
            
            # Mock valid relations only
            mock_relation_points = [
                MagicMock(id="rel1", payload={"from": "entity1", "to": "entity2"}),
                MagicMock(id="rel2", payload={"from": "entity2", "to": "entity1"})
            ]
            
            mock_client.scroll.side_effect = [
                (mock_entity_points, None),  # Entities
                (mock_relation_points, None)  # Relations
            ]
            
            # Mock collection exists
            with patch.object(store, 'collection_exists', return_value=True):
                # Execute cleanup
                result = store._cleanup_orphaned_relations("test_collection", verbose=True)
                
                # Verify no deletions occurred
                assert result == 0
                mock_client.delete.assert_not_called()

    @patch('claude_indexer.storage.qdrant.QdrantClient')
    def test_cleanup_orphaned_relations_no_entities(self, mock_client_class):
        """Test cleanup when no entities exist."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.get_collections.return_value = MagicMock()
            
            store = QdrantStore()
            
            # Mock no entities
            mock_client.scroll.return_value = ([], None)
            
            # Mock collection exists
            with patch.object(store, 'collection_exists', return_value=True):
                # Execute cleanup
                result = store._cleanup_orphaned_relations("test_collection", verbose=True)
                
                # Verify no deletions occurred
                assert result == 0
                # Only one scroll call should happen (for entities)
                assert mock_client.scroll.call_count == 1

    @patch('claude_indexer.storage.qdrant.QdrantClient')
    def test_cleanup_orphaned_relations_collection_not_exists(self, mock_client_class):
        """Test cleanup when collection doesn't exist."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.get_collections.return_value = MagicMock()
            
            store = QdrantStore()
            
            # Mock collection doesn't exist
            with patch.object(store, 'collection_exists', return_value=False):
                # Execute cleanup
                result = store._cleanup_orphaned_relations("nonexistent_collection", verbose=True)
                
                # Verify no operations occurred
                assert result == 0
                mock_client.scroll.assert_not_called()
                mock_client.delete.assert_not_called()

    @patch('claude_indexer.storage.qdrant.QdrantClient')
    def test_cleanup_orphaned_relations_error_handling(self, mock_client_class):
        """Test error handling during orphan cleanup."""
        with patch('claude_indexer.storage.qdrant.QDRANT_AVAILABLE', True):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.get_collections.return_value = MagicMock()
            
            store = QdrantStore()
            
            # Mock scroll to raise exception
            mock_client.scroll.side_effect = Exception("Connection error")
            
            # Mock collection exists
            with patch.object(store, 'collection_exists', return_value=True):
                # Execute cleanup
                result = store._cleanup_orphaned_relations("test_collection", verbose=True)
                
                # Verify error handled gracefully
                assert result == 0


class TestVectorPoint:
    """Test VectorPoint data structure."""
    
    def test_vector_point_creation(self):
        """Test creating VectorPoint instances."""
        vector = list(np.random.rand(1536).astype(np.float32))
        payload = {"text": "test text", "type": "entity"}
        
        point = VectorPoint(id=1, vector=vector, payload=payload)
        
        assert point.id == 1
        assert point.vector == vector
        assert point.payload == payload
    
    def test_vector_point_with_numpy_array(self):
        """Test VectorPoint with numpy array."""
        vector_array = np.random.rand(1536).astype(np.float32)
        payload = {"text": "test", "type": "entity"}
        
        point = VectorPoint(id=2, vector=vector_array, payload=payload)
        
        # Should handle numpy arrays
        assert len(point.vector) == 1536
        assert point.payload == payload


class TestStorageResult:
    """Test StorageResult data structure."""
    
    def test_storage_result_success(self):
        """Test successful storage result."""
        result = StorageResult(
            success=True,
            operation="upsert",
            items_processed=5,
            processing_time=1.5
        )
        
        assert result.success
        assert result.operation == "upsert"
        assert result.items_processed == 5
        assert result.processing_time == 1.5
        assert result.items_failed == 0
        assert result.errors == []
    
    def test_storage_result_with_errors(self):
        """Test storage result with errors."""
        errors = ["Error 1", "Error 2"]
        
        result = StorageResult(
            success=False,
            operation="search",
            processing_time=0.5,
            errors=errors
        )
        
        assert not result.success
        assert result.errors == errors
        assert result.items_processed == 0
    
    def test_storage_result_with_results(self):
        """Test storage result with search results."""
        search_results = [
            {"id": "1", "score": 0.95, "payload": {"text": "result 1"}},
            {"id": "2", "score": 0.87, "payload": {"text": "result 2"}}
        ]
        
        result = StorageResult(
            success=True,
            operation="search",
            processing_time=0.3,
            results=search_results,
            total_found=2
        )
        
        assert result.success
        assert len(result.results) == 2
        assert result.total_found == 2
        assert result.results[0]["score"] == 0.95