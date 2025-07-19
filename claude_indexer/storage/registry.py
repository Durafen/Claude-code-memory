"""Registry for managing storage backend instances."""

from typing import Any

from .base import CachingVectorStore, VectorStore
from .qdrant import QDRANT_AVAILABLE, QdrantStore


class StorageRegistry:
    """Registry for creating and managing vector store instances."""

    def __init__(self) -> None:
        self._stores: dict[str, type[VectorStore]] = {}
        self._register_default_stores()

    def _register_default_stores(self) -> None:
        """Register default storage implementations."""
        if QDRANT_AVAILABLE:
            self.register("qdrant", QdrantStore)

    def register(self, name: str, store_class: type[VectorStore]) -> None:
        """Register a storage backend."""
        self._stores[name] = store_class

    def create_store(
        self, backend: str, config: dict[str, Any], enable_caching: bool = True
    ) -> VectorStore:
        """Create a vector store instance from configuration."""
        if backend not in self._stores:
            available = list(self._stores.keys())
            raise ValueError(
                f"Unknown storage backend: {backend}. Available: {available}"
            )

        store_class = self._stores[backend]

        try:
            # Create base store
            store = store_class(**config)

            # Wrap with caching if enabled
            if enable_caching:
                cache_size = config.get("cache_size", 1000)
                store = CachingVectorStore(store, max_cache_size=cache_size)

            return store

        except Exception as e:
            raise RuntimeError(f"Failed to create {backend} store: {e}")

    def get_available_backends(self) -> list[str]:
        """Get list of available storage backends."""
        return list(self._stores.keys())


def create_store_from_config(config: Any) -> VectorStore:
    """Create vector store from configuration (IndexerConfig or dict)."""
    registry = StorageRegistry()

    # Handle both IndexerConfig objects and dicts
    if hasattr(config, "storage_type"):
        # IndexerConfig object
        backend = config.storage_type
        enable_caching = True  # Default for IndexerConfig
        backend_config = {
            "url": config.qdrant_url,
            "api_key": config.qdrant_api_key,
            "collection_name": config.collection_name,
        }
    else:
        # Dict config (backward compatibility)
        backend = config.get("backend", "qdrant")
        enable_caching = config.get("enable_caching", True)
        backend_config = {
            k: v
            for k, v in config.items()
            if k not in ["backend", "enable_caching", "cache_size"]
        }

    return registry.create_store(backend, backend_config, enable_caching)
