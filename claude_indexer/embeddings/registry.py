"""Registry for managing embedder instances and configurations."""

from typing import Any

from .base import CachingEmbedder, Embedder, RetryableEmbedder
from .openai import OPENAI_AVAILABLE, OpenAIEmbedder
from .voyage import VOYAGE_AVAILABLE, VoyageEmbedder


class EmbedderRegistry:
    """Registry for creating and managing embedders."""

    def __init__(self) -> None:
        self._embedders: dict[str, type[Embedder]] = {}
        self._register_default_embedders()

    def _register_default_embedders(self) -> None:
        """Register default embedder implementations."""
        if OPENAI_AVAILABLE:
            self.register("openai", OpenAIEmbedder)
        if VOYAGE_AVAILABLE:
            self.register("voyage", VoyageEmbedder)

    def register(self, name: str, embedder_class: type[Embedder]) -> None:
        """Register an embedder class."""
        self._embedders[name] = embedder_class

    def create_embedder(
        self, provider: str, config: dict[str, Any], enable_caching: bool = True
    ) -> Embedder:
        """Create an embedder instance from configuration."""
        if provider not in self._embedders:
            available = list(self._embedders.keys())
            raise ValueError(
                f"Unknown embedder provider: {provider}. Available: {available}"
            )

        embedder_class = self._embedders[provider]

        try:
            # Create base embedder
            embedder = embedder_class(**config)

            # Wrap with caching if enabled
            if enable_caching:
                cache_size = config.get("cache_size", 10000)
                embedder = CachingEmbedder(embedder, max_cache_size=cache_size)

            return embedder

        except Exception as e:
            raise RuntimeError(f"Failed to create {provider} embedder: {e}") from None

    def get_available_providers(self) -> list[str]:
        """Get list of available embedder providers."""
        return list(self._embedders.keys())

    def get_provider_info(self, provider: str) -> dict[str, Any]:
        """Get information about a specific provider."""
        if provider not in self._embedders:
            raise ValueError(f"Unknown provider: {provider}")

        embedder_class = self._embedders[provider]

        # Try to get model info without instantiating
        if hasattr(embedder_class, "MODELS"):
            return {
                "provider": provider,
                "class": embedder_class.__name__,
                "available_models": list(embedder_class.MODELS.keys()),
                "supports_batch": True,
                "supports_retry": issubclass(embedder_class, RetryableEmbedder),
            }

        return {
            "provider": provider,
            "class": embedder_class.__name__,
            "available_models": ["unknown"],
            "supports_batch": hasattr(embedder_class, "embed_batch"),
            "supports_retry": False,
        }


def create_embedder_from_config(config: Any) -> Embedder:
    """Create embedder from configuration (IndexerConfig or dict)."""
    registry = EmbedderRegistry()

    # Handle both IndexerConfig objects and dicts
    if hasattr(config, "embedding_provider"):
        # IndexerConfig object
        provider = config.embedding_provider
        enable_caching = True  # Default for IndexerConfig
        if provider == "voyage":
            provider_config = {
                "api_key": config.voyage_api_key,
                "model": config.voyage_model,
            }
        else:  # openai
            provider_config = {
                "api_key": config.openai_api_key,
                "model": "text-embedding-3-small",
            }
    else:
        # Dict config (backward compatibility)
        provider = config.get("provider", "openai")
        enable_caching = config.get("enable_caching", True)
        provider_config = {
            k: v
            for k, v in config.items()
            if k not in ["provider", "enable_caching", "cache_size"]
        }

    return registry.create_embedder(provider, provider_config, enable_caching)


# For backward compatibility
def create_openai_embedder(
    api_key: str,
    model: str = "text-embedding-3-small",
    enable_caching: bool = True,
    **kwargs: Any,
) -> Embedder:
    """Create OpenAI embedder with default configuration."""
    config = {
        "provider": "openai",
        "api_key": api_key,
        "model": model,
        "enable_caching": enable_caching,
        **kwargs,
    }
    return create_embedder_from_config(config)


def create_voyage_embedder(
    api_key: str, model: str = "voyage-3-lite", enable_caching: bool = True, **kwargs: Any
) -> Embedder:
    """Create Voyage AI embedder with default configuration."""
    config = {
        "provider": "voyage",
        "api_key": api_key,
        "model": model,
        "enable_caching": enable_caching,
        **kwargs,
    }
    return create_embedder_from_config(config)
