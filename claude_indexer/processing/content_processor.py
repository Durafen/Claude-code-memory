"""Base content processor classes."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ..storage.qdrant import ContentHashMixin
from ..embeddings.registry import create_bm25_embedder
from .context import ProcessingContext
from .results import ProcessingResult

if TYPE_CHECKING:
    pass


class ContentProcessor(ContentHashMixin, ABC):
    """Base class for content processing with deduplication."""

    def __init__(self, vector_store, embedder, logger=None):
        self.vector_store = vector_store
        self.embedder = embedder
        self.logger = logger
        # Lazy-loaded BM25 embedder for sparse vectors
        self._bm25_embedder = None
        self._bm25_corpus_fitted = False

    def _get_bm25_embedder(self):
        """Lazy initialize BM25 embedder for sparse vectors."""
        if self._bm25_embedder is None:
            try:
                self._bm25_embedder = create_bm25_embedder()
                if self.logger:
                    self.logger.debug("🔤 Initialized BM25 embedder for sparse vectors")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to create BM25 embedder: {e}")
                self._bm25_embedder = False  # Mark as failed to avoid retrying
        return self._bm25_embedder if self._bm25_embedder is not False else None

    @abstractmethod
    def process_batch(
        self, items: list, context: ProcessingContext
    ) -> ProcessingResult:
        """Process a batch of content items."""
        pass

    def check_deduplication(
        self, items: list, collection_name: str
    ) -> tuple[list, list]:
        """Universal deduplication logic using content hashes."""
        to_embed = []
        to_skip = []

        for item in items:
            content_hash = self._get_content_hash(item)
            if self.vector_store.check_content_exists(collection_name, content_hash):
                to_skip.append(item)
                # if self.logger:
                # self.logger.debug(f"⚡ Skipping unchanged item: {getattr(item, 'entity_name', item)}")
            else:
                to_embed.append(item)

        return to_embed, to_skip

    def _get_content_hash(self, item) -> str:
        """Get content hash from item."""
        if hasattr(item, "to_vector_payload"):
            return item.to_vector_payload().get("content_hash", "")
        return ""

    def _get_existing_entities_for_file(
        self, collection_name: str, file_path: str, chunk_type: str
    ) -> list[str]:
        """Get existing entity IDs for file and chunk type."""
        if hasattr(self.vector_store, 'find_entities_for_file_by_type'):
            entities_by_type = self.vector_store.find_entities_for_file_by_type(
                collection_name, file_path, [chunk_type]
            )
            entity_ids = [entity["id"] for entity in entities_by_type.get(chunk_type, [])]
            if hasattr(self, 'logger') and self.logger:
                self.logger.debug(f"🔍 DEBUG: _get_existing_entities_for_file found {len(entity_ids)} entities for {file_path}::{chunk_type}")
                for eid in entity_ids[:5]:  # Show first 5
                    self.logger.debug(f"🔍 DEBUG: Entity ID: {eid}")
            return entity_ids
        return []

    def _should_replace_file_entities(self, entity_file_path: str, context: "ProcessingContext") -> bool:
        """Determine if file entities should be replaced."""
        from pathlib import Path

        # Convert string path to Path for comparison since files_being_processed contains Path objects
        entity_path = Path(entity_file_path)

        return (
            context.replacement_mode and
            entity_path in context.files_being_processed
        )

    def process_embeddings(self, items: list, item_name: str) -> tuple[list, dict]:  # noqa: ARG002
        """Generate embeddings with error handling and cost tracking."""
        if not items:
            return [], {"tokens": 0, "cost": 0.0, "requests": 0}

        # Extract content for embedding
        texts = [getattr(item, "content", str(item)) for item in items]

        # Generate dense embeddings (primary)
        embedding_results = self.embedder.embed_batch(texts)

        # Generate BM25 sparse embeddings ONLY for entity metadata (90% of searches)
        if item_name == "entity":
            bm25_embedder = self._get_bm25_embedder()
            if bm25_embedder:
                try:
                    # Fit corpus on first use for this batch
                    if not self._bm25_corpus_fitted:
                        bm25_embedder.fit_corpus(texts)
                        self._bm25_corpus_fitted = True
                        if self.logger:
                            self.logger.debug(f"🔤 Fitted BM25 corpus on {len(texts)} entity texts")
                    
                    # Generate sparse embeddings
                    bm25_results = bm25_embedder.embed_batch(texts)
                    
                    # Add sparse vectors to dense embedding results
                    for dense_result, sparse_result in zip(embedding_results, bm25_results, strict=False):
                        if dense_result.success and sparse_result.success:
                            dense_result.sparse_embedding = sparse_result.embedding
                            
                    if self.logger:
                        successful_sparse = sum(1 for r in bm25_results if r.success)
                        self.logger.debug(f"🔤 Generated {successful_sparse}/{len(bm25_results)} BM25 entity sparse vectors")
                        
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"BM25 entity embedding failed: {e}")

        # Collect cost data (from dense embeddings)
        cost_data = self._collect_embedding_cost_data(embedding_results)

        return embedding_results, cost_data

    def create_points(
        self,
        items: list,
        embedding_results: list,
        collection_name: str,
        point_creation_method: str = "create_chunk_point",
    ) -> tuple[list, int]:
        """Create vector points from items and embeddings."""
        points = []
        failed_count = 0

        for item, embedding_result in zip(items, embedding_results, strict=False):
            if embedding_result.success:
                # Check if sparse embedding is available for hybrid point creation
                if hasattr(embedding_result, 'sparse_embedding') and embedding_result.sparse_embedding is not None:
                    # Get the backend vector store (handles CachingVectorStore wrapper)
                    backend_store = getattr(self.vector_store, 'backend', self.vector_store)
                    
                    # Use hybrid point creation method
                    hybrid_method = f"create_hybrid_{point_creation_method.replace('create_', '')}"
                    if hasattr(backend_store, hybrid_method):
                        point_creator = getattr(backend_store, hybrid_method)
                        point = point_creator(
                            item, embedding_result.embedding, embedding_result.sparse_embedding, collection_name
                        )
                        points.append(point)
                    elif hasattr(backend_store, 'create_hybrid_chunk_point'):
                        # Fallback to hybrid chunk point
                        point = backend_store.create_hybrid_chunk_point(
                            item, embedding_result.embedding, embedding_result.sparse_embedding, collection_name
                        )
                        points.append(point)
                    else:
                        # No hybrid support, use dense-only
                        if hasattr(self.vector_store, point_creation_method):
                            point_creator = getattr(self.vector_store, point_creation_method)
                            point = point_creator(
                                item, embedding_result.embedding, collection_name
                            )
                            points.append(point)
                        else:
                            point = self.vector_store.create_chunk_point(
                                item, embedding_result.embedding, collection_name
                            )
                            points.append(point)
                else:
                    # Standard dense-only point creation
                    if hasattr(self.vector_store, point_creation_method):
                        point_creator = getattr(self.vector_store, point_creation_method)
                        point = point_creator(
                            item, embedding_result.embedding, collection_name
                        )
                        points.append(point)
                    else:
                        # Fallback to default chunk point creation
                        point = self.vector_store.create_chunk_point(
                            item, embedding_result.embedding, collection_name
                        )
                        points.append(point)
            else:
                failed_count += 1
                if self.logger:
                    error_msg = getattr(embedding_result, "error", "Unknown error")
                    item_name = getattr(item, "entity_name", str(item))
                    self.logger.warning(
                        f"❌ Embedding failed: {item_name} - {error_msg}"
                    )

        return points, failed_count

    def _collect_embedding_cost_data(
        self, embedding_results: list[Any]
    ) -> dict[str, Any]:
        """Collect cost data from embedding results."""
        total_tokens = 0
        total_cost = 0.0
        total_requests = 0

        for embedding_result in embedding_results:
            if (
                hasattr(embedding_result, "token_count")
                and embedding_result.token_count
            ):
                total_tokens += embedding_result.token_count
            if (
                hasattr(embedding_result, "cost_estimate")
                and embedding_result.cost_estimate
            ):
                total_cost += embedding_result.cost_estimate

        # Count successful requests
        if hasattr(self.embedder, "get_usage_stats"):
            stats_before = getattr(self, "_last_usage_stats", {"total_requests": 0})
            current_stats = self.embedder.get_usage_stats()
            total_requests += max(
                0,
                current_stats.get("total_requests", 0)
                - stats_before.get("total_requests", 0),
            )
            self._last_usage_stats = current_stats

        return {"tokens": total_tokens, "cost": total_cost, "requests": total_requests}
