"""Embeddings package for generating vector representations of text."""

from .base import Embedder, EmbeddingResult
from .openai import OpenAIEmbedder
from .registry import EmbedderRegistry

__all__ = [
    "Embedder",
    "EmbeddingResult",
    "OpenAIEmbedder",
    "EmbedderRegistry",
]
