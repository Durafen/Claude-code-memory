"""OpenAI embeddings implementation with retry logic and rate limiting."""

import time
from typing import Any, cast

from .base import EmbeddingResult, RetryableEmbedder, TiktokenMixin

try:
    import openai

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class OpenAIEmbedder(TiktokenMixin, RetryableEmbedder):
    """OpenAI embeddings with retry logic and rate limiting."""

    # Model configurations with current 2025 pricing
    MODELS = {
        "text-embedding-3-small": {
            "dimensions": 1536,
            "max_tokens": 8191,
            "cost_per_1k_tokens": 0.00002,  # $0.00002/1K tokens (current 2025)
        },
        "text-embedding-3-large": {
            "dimensions": 3072,
            "max_tokens": 8191,
            "cost_per_1k_tokens": 0.00013,  # $0.00013/1K tokens (current 2025)
        },
        "text-embedding-ada-002": {
            "dimensions": 1536,
            "max_tokens": 8191,
            "cost_per_1k_tokens": 0.0001,  # $0.0001/1K tokens (legacy model)
        },
    }

    def __init__(
        self,
        api_key: str | None = None,
        openai_api_key: str | None = None,
        model: str = "text-embedding-3-small",
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI package not available. Install with: pip install openai"
            )

        # Support both parameter names for backward compatibility
        final_api_key = api_key or openai_api_key
        if not final_api_key:
            raise ValueError("Valid OpenAI API key required")

        # Allow test keys for testing
        is_test_key = final_api_key.startswith("test-")
        if not is_test_key and not final_api_key.startswith("sk-"):
            raise ValueError("Valid OpenAI API key required")

        if model not in self.MODELS:
            raise ValueError(
                f"Unsupported model: {model}. Available: {list(self.MODELS.keys())}"
            )

        self.model = model
        self.model_config = self.MODELS[model]

        super().__init__(max_retries=max_retries, base_delay=base_delay)

        self.client = openai.OpenAI(api_key=final_api_key, timeout=30.0)

        # Rate limiting
        self._requests_per_minute = 3000  # Conservative limit
        self._tokens_per_minute = 1000000
        self._request_times: list[float] = []
        self._token_counts: list[tuple[float, int]] = []

    def _check_rate_limits(self, estimated_tokens: int = 1000) -> None:
        """Check and enforce rate limits."""
        current_time = time.time()

        # Clean old entries (older than 1 minute)
        self._request_times = [t for t in self._request_times if current_time - t < 60]
        self._token_counts = [
            (t, tokens) for t, tokens in self._token_counts if current_time - t < 60
        ]

        # Check request rate limit
        if len(self._request_times) >= self._requests_per_minute:
            sleep_time = 60 - (current_time - self._request_times[0]) + 1
            if sleep_time > 0:
                print(f"Rate limit reached. Sleeping for {sleep_time:.1f} seconds...")
                time.sleep(sleep_time)

        # Check token rate limit
        total_tokens = (
            sum(tokens for _, tokens in self._token_counts) + estimated_tokens
        )
        if total_tokens >= self._tokens_per_minute:
            sleep_time = 60 - (current_time - self._token_counts[0][0]) + 1
            if sleep_time > 0:
                print(
                    f"Token rate limit reached. Sleeping for {sleep_time:.1f} seconds..."
                )
                time.sleep(sleep_time)

    def _estimate_tokens(self, text: str) -> int:
        """Accurate token estimation using tiktoken."""
        return self._estimate_tokens_with_tiktoken(text)

    def _calculate_cost(self, token_count: int) -> float:
        """Calculate estimated cost for token count."""
        cost_per_token = self.model_config["cost_per_1k_tokens"] / 1000
        return token_count * cost_per_token

    def embed_text(self, text: str) -> EmbeddingResult:
        """Generate embedding for a single text."""
        start_time = time.time()
        estimated_tokens = self._estimate_tokens(text)

        # Truncate if necessary
        text = self.truncate_text(text)

        def _embed() -> EmbeddingResult:
            self._check_rate_limits(estimated_tokens)

            response = self.client.embeddings.create(
                model=self.model, input=text, encoding_format="float"
            )

            # Record request for rate limiting
            current_time = time.time()
            self._request_times.append(current_time)

            usage = response.usage
            actual_tokens = usage.total_tokens
            self._token_counts.append((current_time, actual_tokens))

            return EmbeddingResult(
                text=text,
                embedding=response.data[0].embedding,
                model=self.model,
                token_count=actual_tokens,
                processing_time=time.time() - start_time,
                cost_estimate=self._calculate_cost(actual_tokens),
            )

        try:
            result = self._embed_with_retry(_embed)
            return cast(EmbeddingResult, result)
        except Exception as e:
            return EmbeddingResult(
                text=text,
                embedding=[],
                model=self.model,
                processing_time=time.time() - start_time,
                error=str(e),
            )

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate embeddings for multiple texts with enhanced token-based batching."""
        if not texts:
            return []

        # Model-specific token limits with tiktoken accuracy
        token_limit = self._get_effective_token_limit()
        text_count_limit = self._get_text_count_limit()

        results = []
        current_batch: list[str] = []
        current_tokens = 0

        for text in texts:
            # Use accurate tiktoken counting for batch optimization
            text_tokens = self._estimate_tokens(text)

            if (
                current_tokens + text_tokens > token_limit
                or len(current_batch) >= text_count_limit
            ) and current_batch:
                # Process current batch
                batch_results = self._embed_batch(current_batch)
                results.extend(batch_results)
                current_batch = []
                current_tokens = 0

            current_batch.append(text)
            current_tokens += text_tokens

        # Process final batch
        if current_batch:
            batch_results = self._embed_batch(current_batch)
            results.extend(batch_results)

        return results

    def _get_effective_token_limit(self) -> int:
        """Get effective token limit for batching."""
        # Conservative limit for OpenAI embeddings - leave room for overhead
        return 120_000  # Well below the ~8K limit per text, allows for multiple texts

    def _get_text_count_limit(self) -> int:
        """Get text count limit for batching."""
        return 2048  # OpenAI's batch size limit

    def _embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed a single batch of texts."""
        start_time = time.time()

        # Truncate texts if necessary
        truncated_texts = [self.truncate_text(text) for text in texts]
        estimated_tokens = sum(self._estimate_tokens(text) for text in truncated_texts)

        def _embed() -> list[EmbeddingResult]:
            self._check_rate_limits(estimated_tokens)

            response = self.client.embeddings.create(
                model=self.model, input=truncated_texts, encoding_format="float"
            )

            # Record request for rate limiting
            current_time = time.time()
            self._request_times.append(current_time)

            usage = response.usage
            actual_tokens = usage.total_tokens
            self._token_counts.append((current_time, actual_tokens))

            # Create results for each text
            processing_time = time.time() - start_time
            total_cost = self._calculate_cost(actual_tokens)
            cost_per_text = total_cost / len(texts)
            tokens_per_text = actual_tokens // len(texts)

            results = []
            for _i, (text, embedding_data) in enumerate(
                zip(texts, response.data, strict=False)
            ):
                results.append(
                    EmbeddingResult(
                        text=text,
                        embedding=embedding_data.embedding,
                        model=self.model,
                        token_count=tokens_per_text,
                        processing_time=processing_time / len(texts),
                        cost_estimate=cost_per_text,
                    )
                )

            return results

        try:
            return cast(list[EmbeddingResult], self._embed_with_retry(_embed))
        except Exception as e:
            # Return error results for all texts
            error_msg = str(e)
            return [
                EmbeddingResult(
                    text=text,
                    embedding=[],
                    model=self.model,
                    processing_time=0.0,
                    error=error_msg,
                )
                for text in texts
            ]

    def get_model_info(self) -> dict[str, Any]:
        """Get information about the embedding model."""
        return {
            "provider": "openai",
            "model": self.model,
            "dimensions": self.model_config["dimensions"],
            "max_tokens": self.model_config["max_tokens"],
            "cost_per_1k_tokens": self.model_config["cost_per_1k_tokens"],
            "supports_batch": True,
            "rate_limits": {
                "requests_per_minute": self._requests_per_minute,
                "tokens_per_minute": self._tokens_per_minute,
            },
        }

    def get_max_tokens(self) -> int:
        """Get maximum token limit for input text."""
        return int(self.model_config["max_tokens"])

    def get_usage_stats(self) -> dict[str, Any]:
        """Get usage statistics."""
        current_time = time.time()

        # Recent requests (last minute)
        recent_requests = [t for t in self._request_times if current_time - t < 60]
        recent_tokens = sum(
            tokens for t, tokens in self._token_counts if current_time - t < 60
        )

        # Total usage
        total_requests = len(self._request_times)
        total_tokens = sum(tokens for _, tokens in self._token_counts)
        total_cost = sum(
            self._calculate_cost(tokens) for _, tokens in self._token_counts
        )

        return {
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "total_cost_estimate": total_cost,
            "recent_requests_per_minute": len(recent_requests),
            "recent_tokens_per_minute": recent_tokens,
            "average_tokens_per_request": total_tokens / max(total_requests, 1),
        }
