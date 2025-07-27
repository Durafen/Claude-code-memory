"""BM25 sparse embeddings implementation for efficient keyword-based search."""

import hashlib
import json
import math
import pickle
import time
from pathlib import Path
from typing import Any, cast

from ..indexer_logging import get_logger
from .base import EmbeddingResult, Embedder

try:
    import bm25s
    import numpy as np

    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False

logger = get_logger()


class BM25Embedder(Embedder):
    """BM25 sparse embeddings for keyword-based semantic search.
    
    Generates sparse vectors compatible with Qdrant format using the bm25s library.
    Maintains separate models for different collections and supports incremental updates.
    """

    def __init__(
        self,
        model_name: str = "bm25",
        k1: float = 1.2,
        b: float = 0.75,
        delta: float = 0.0,
        method: str = "robertson",
        corpus_size_limit: int = 100000,
        cache_dir: str | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        """Initialize BM25 embedder with configurable parameters.
        
        Args:
            model_name: Identifier for the BM25 model
            k1: Controls term frequency saturation (default: 1.2)
            b: Controls field length normalization (default: 0.75)  
            delta: Adds constant to term frequencies (default: 0.0)
            method: BM25 variant ('robertson', 'lucene', 'atire', 'bm25l', 'bm25plus')
            corpus_size_limit: Maximum corpus size for memory management
            cache_dir: Directory for caching trained models
        """
        if not BM25_AVAILABLE:
            raise ImportError(
                "bm25s package not available. Install with: pip install bm25s"
            )

        self.model_name = model_name
        self.k1 = k1
        self.b = b
        self.delta = delta
        self.method = method
        self.corpus_size_limit = corpus_size_limit
        
        # Set up cache directory
        if cache_dir is None:
            cache_dir = Path.home() / ".claude-indexer" / "bm25_cache"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Model state
        self.model: bm25s.BM25 | None = None
        self.vocabulary: dict[str, int] = {}
        self.corpus: list[str] = []
        self.is_fitted = False
        
        # Performance tracking
        self._fit_time = 0.0
        self._total_texts_processed = 0
        
        logger.debug(f"Initialized BM25Embedder with method={method}, k1={k1}, b={b}")

    def _get_cache_key(self, corpus_hash: str) -> str:
        """Generate cache key for model state."""
        config_str = f"{self.method}_{self.k1}_{self.b}_{self.delta}_{corpus_hash}"
        return hashlib.sha256(config_str.encode()).hexdigest()[:16]

    def _get_corpus_hash(self, corpus: list[str]) -> str:
        """Generate hash of corpus for caching."""
        corpus_str = "\n".join(sorted(corpus))
        return hashlib.sha256(corpus_str.encode()).hexdigest()[:16]

    def _save_model_cache(self, cache_key: str) -> None:
        """Save trained model to cache."""
        try:
            cache_file = self.cache_dir / f"{cache_key}.pkl"
            cache_data = {
                "model": self.model,
                "vocabulary": self.vocabulary,
                "corpus": self.corpus,
                "config": {
                    "method": self.method,
                    "k1": self.k1,
                    "b": self.b,
                    "delta": self.delta,
                },
                "fit_time": self._fit_time,
                "total_texts": self._total_texts_processed,
            }
            
            with open(cache_file, "wb") as f:
                pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)
                
            logger.debug(f"Saved BM25 model cache to {cache_file}")
            
        except Exception as e:
            logger.warning(f"Failed to save model cache: {e}")

    def _load_model_cache(self, cache_key: str) -> bool:
        """Load trained model from cache."""
        try:
            cache_file = self.cache_dir / f"{cache_key}.pkl"
            if not cache_file.exists():
                return False
                
            with open(cache_file, "rb") as f:
                cache_data = pickle.load(f)
            
            # Verify config matches
            cached_config = cache_data.get("config", {})
            if (
                cached_config.get("method") != self.method
                or cached_config.get("k1") != self.k1
                or cached_config.get("b") != self.b
                or cached_config.get("delta") != self.delta
            ):
                logger.debug("Cache config mismatch, rebuilding model")
                return False
            
            self.model = cache_data["model"]
            self.vocabulary = cache_data["vocabulary"]
            self.corpus = cache_data["corpus"]
            self._fit_time = cache_data.get("fit_time", 0.0)
            self._total_texts_processed = cache_data.get("total_texts", 0)
            self.is_fitted = True
            
            logger.debug(f"Loaded BM25 model from cache with {len(self.corpus)} documents")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to load model cache: {e}")
            return False

    def _preprocess_text(self, text: str) -> list[str]:
        """Preprocess text into tokens."""
        # Simple tokenization - can be enhanced with proper tokenizers
        import re
        
        # Convert to lowercase and extract alphanumeric tokens
        text = text.lower()
        tokens = re.findall(r'\b[a-zA-Z0-9]+\b', text)
        
        # Remove very short tokens
        tokens = [token for token in tokens if len(token) > 1]
        
        return tokens

    def _fit_corpus(self, corpus: list[str]) -> None:
        """Fit BM25 model on corpus."""
        start_time = time.time()
        
        # Check cache first
        corpus_hash = self._get_corpus_hash(corpus)
        cache_key = self._get_cache_key(corpus_hash)
        
        if self._load_model_cache(cache_key):
            logger.debug(f"Using cached BM25 model for {len(corpus)} documents")
            return
        
        # Preprocess corpus
        tokenized_corpus = [self._preprocess_text(text) for text in corpus]
        
        # Initialize BM25 model
        self.model = bm25s.BM25(
            method=self.method,
            k1=self.k1,
            b=self.b,
            delta=self.delta
        )
        
        # Fit the model
        logger.debug(f"Fitting BM25 model on {len(corpus)} documents...")
        self.model.index(tokenized_corpus)
        
        # Store corpus and extract vocabulary
        self.corpus = corpus.copy()
        
        # Extract vocabulary from the fitted model
        if hasattr(self.model, 'vocab'):
            self.vocabulary = {term: idx for idx, term in enumerate(self.model.vocab)}
        elif hasattr(self.model, 'vocabulary'):
            self.vocabulary = getattr(self.model, 'vocabulary', {})
        else:
            # Create vocabulary from tokenized corpus if model doesn't provide it
            unique_tokens = set()
            for tokens in tokenized_corpus:
                unique_tokens.update(tokens)
            self.vocabulary = {term: idx for idx, term in enumerate(sorted(unique_tokens))}
        
        self.is_fitted = True
        
        self._fit_time = time.time() - start_time
        self._total_texts_processed = len(corpus)
        
        # Save to cache
        self._save_model_cache(cache_key)
        
        logger.debug(
            f"BM25 model fitted in {self._fit_time:.2f}s, "
            f"vocabulary size: {len(self.vocabulary)}"
        )

    def _generate_sparse_vector(self, text: str) -> list[float]:
        """Generate sparse vector for a single text using proper IDF-based term weighting."""
        if not self.is_fitted or self.model is None:
            raise RuntimeError("BM25 model not fitted. Call fit_corpus or embed_batch first.")
        
        # Tokenize query text
        query_tokens = self._preprocess_text(text)
        
        if not query_tokens:
            # Return zero vector for empty queries
            vocab_size = max(len(self.vocabulary), 100)  # minimum size
            return [0.0] * vocab_size
        
        try:
            # Create vocabulary mapping if missing
            if not self.vocabulary and hasattr(self.model, 'vocab'):
                self.vocabulary = {term: idx for idx, term in enumerate(self.model.vocab)}
            
            # Determine vector dimension
            vocab_size = max(len(self.vocabulary), 100)
            sparse_vector = np.zeros(vocab_size, dtype=np.float32)
            
            # Calculate document frequencies for vocabulary terms
            doc_freq = {}
            for term in self.vocabulary:
                doc_freq[term] = sum(1 for doc in self.corpus 
                                   if term in self._preprocess_text(doc))
            
            # Calculate IDF weights for query terms
            N = len(self.corpus)
            for token in query_tokens:
                if token in self.vocabulary:
                    vocab_idx = self.vocabulary[token]
                    if vocab_idx < len(sparse_vector):
                        df = doc_freq.get(token, 0)
                        if df > 0:
                            # Standard IDF formula: log((N - df + 0.5) / (df + 0.5))
                            idf = math.log((N - df + 0.5) / (df + 0.5))
                            sparse_vector[vocab_idx] = float(max(0.0, idf))
                        else:
                            # Term not in corpus, give it a small positive weight
                            sparse_vector[vocab_idx] = 0.1
            
            return sparse_vector.tolist()
            
        except Exception as e:
            logger.warning(f"BM25 scoring failed: {e}, returning zero vector")
            vocab_size = max(len(self.vocabulary), 100)
            return [0.0] * vocab_size

    def embed_text(self, text: str) -> EmbeddingResult:
        """Generate BM25 sparse embedding for a single text."""
        start_time = time.time()
        
        try:
            # Handle empty or very short text
            tokens = self._preprocess_text(text)
            if not tokens:
                # Return zero vector for empty text
                default_dim = max(len(self.vocabulary), 100) if self.vocabulary else 100
                return EmbeddingResult(
                    text=text,
                    embedding=[0.0] * default_dim,
                    model=f"bm25_{self.method}",
                    token_count=0,
                    processing_time=time.time() - start_time,
                    cost_estimate=0.0,
                )
            
            # If model not fitted, fit on single text corpus
            if not self.is_fitted:
                logger.debug("BM25 model not fitted, using single-text corpus")
                # For single text, add some default corpus to avoid empty vocabulary
                corpus = [text] if tokens else ["default corpus text for initialization"]
                self._fit_corpus(corpus)
            
            # Generate sparse vector
            embedding = self._generate_sparse_vector(text)
            
            return EmbeddingResult(
                text=text,
                embedding=embedding,
                model=f"bm25_{self.method}",
                token_count=len(tokens),
                processing_time=time.time() - start_time,
                cost_estimate=0.0,  # BM25 is free
            )
            
        except Exception as e:
            logger.error(f"BM25 embedding failed: {e}")
            # Return zero vector on error
            default_dim = max(len(self.vocabulary), 100) if self.vocabulary else 100
            return EmbeddingResult(
                text=text,
                embedding=[0.0] * default_dim,
                model=f"bm25_{self.method}",
                processing_time=time.time() - start_time,
                error=str(e),
            )

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate BM25 sparse embeddings for multiple texts."""
        if not texts:
            return []
        
        start_time = time.time()
        
        try:
            # Fit model on the entire corpus first
            if not self.is_fitted:
                # Limit corpus size for memory management
                corpus = texts[:self.corpus_size_limit]
                self._fit_corpus(corpus)
            
            # Generate embeddings for all texts
            results = []
            for text in texts:
                embedding = self._generate_sparse_vector(text)
                
                results.append(
                    EmbeddingResult(
                        text=text,
                        embedding=embedding,
                        model=f"bm25_{self.method}",
                        token_count=len(self._preprocess_text(text)),
                        processing_time=(time.time() - start_time) / len(texts),
                        cost_estimate=0.0,  # BM25 is free
                    )
                )
            
            logger.debug(f"Generated {len(results)} BM25 embeddings")
            return results
            
        except Exception as e:
            logger.error(f"BM25 batch embedding failed: {e}")
            error_msg = str(e)
            return [
                EmbeddingResult(
                    text=text,
                    embedding=[],
                    model=f"bm25_{self.method}",
                    processing_time=0.0,
                    error=error_msg,
                )
                for text in texts
            ]

    def fit_corpus(self, corpus: list[str]) -> None:
        """Explicitly fit the BM25 model on a corpus."""
        self._fit_corpus(corpus)

    def get_model_info(self) -> dict[str, Any]:
        """Get information about the BM25 model."""
        vocab_size = len(self.vocabulary) if self.vocabulary else 0
        
        return {
            "provider": "bm25",
            "model": f"bm25_{self.method}",
            "method": self.method,
            "parameters": {
                "k1": self.k1,
                "b": self.b,
                "delta": self.delta,
            },
            "dimensions": vocab_size,
            "max_tokens": float("inf"),  # No token limit for BM25
            "cost_per_1k_tokens": 0.0,
            "supports_batch": True,
            "sparse_vectors": True,
            "corpus_size": len(self.corpus),
            "vocabulary_size": vocab_size,
            "is_fitted": self.is_fitted,
            "fit_time": self._fit_time,
            "total_texts_processed": self._total_texts_processed,
        }

    def get_max_tokens(self) -> int:
        """Get maximum token limit for input text."""
        return 2**31 - 1  # No practical limit for BM25

    def get_vocabulary(self) -> dict[str, int]:
        """Get the current vocabulary mapping."""
        return self.vocabulary.copy() if self.vocabulary else {}

    def get_corpus_stats(self) -> dict[str, Any]:
        """Get statistics about the fitted corpus."""
        if not self.is_fitted:
            return {"fitted": False}
        
        token_counts = [len(self._preprocess_text(text)) for text in self.corpus]
        
        return {
            "fitted": True,
            "corpus_size": len(self.corpus),
            "vocabulary_size": len(self.vocabulary),
            "avg_tokens_per_doc": sum(token_counts) / len(token_counts) if token_counts else 0,
            "min_tokens_per_doc": min(token_counts) if token_counts else 0,
            "max_tokens_per_doc": max(token_counts) if token_counts else 0,
            "fit_time": self._fit_time,
            "cache_dir": str(self.cache_dir),
        }

    def clear_cache(self) -> int:
        """Clear all cached models and return number of files removed."""
        try:
            cache_files = list(self.cache_dir.glob("*.pkl"))
            for cache_file in cache_files:
                cache_file.unlink()
            
            logger.debug(f"Cleared {len(cache_files)} BM25 cache files")
            return len(cache_files)
            
        except Exception as e:
            logger.warning(f"Failed to clear cache: {e}")
            return 0

    def reset_model(self) -> None:
        """Reset the model state for retraining."""
        self.model = None
        self.vocabulary = {}
        self.corpus = []
        self.is_fitted = False
        self._fit_time = 0.0
        self._total_texts_processed = 0
        
        logger.debug("BM25 model state reset")