"""Unit tests for embedding generation functionality."""

import pytest
import numpy as np
from typing import List
from unittest.mock import Mock, patch, MagicMock

from claude_indexer.embeddings.openai import OpenAIEmbedder
from claude_indexer.embeddings.base import EmbeddingResult, RetryableEmbedder


class TestOpenAIEmbedder:
    """Test OpenAI embeddings functionality."""
    
    def test_initialization_valid_key(self):
        """Test OpenAI embedder initialization with valid key."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI') as mock_openai:
                embedder = OpenAIEmbedder(api_key="sk-test123")
                
                assert embedder.model == "text-embedding-3-small"
                assert embedder.model_config["dimensions"] == 1536
                mock_openai.assert_called_once_with(api_key="sk-test123")
    
    def test_initialization_invalid_key(self):
        """Test initialization with invalid API key."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with pytest.raises(ValueError, match="Valid OpenAI API key required"):
                OpenAIEmbedder(api_key="invalid-key")
            
            with pytest.raises(ValueError, match="Valid OpenAI API key required"):
                OpenAIEmbedder(api_key="")
    
    def test_initialization_unsupported_model(self):
        """Test initialization with unsupported model."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with pytest.raises(ValueError, match="Unsupported model"):
                OpenAIEmbedder(api_key="sk-test123", model="invalid-model")
    
    def test_initialization_openai_unavailable(self):
        """Test initialization when OpenAI package unavailable."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', False):
            with pytest.raises(ImportError, match="OpenAI package not available"):
                OpenAIEmbedder(api_key="sk-test123")
    
    def test_model_configurations(self):
        """Test that model configurations are properly defined."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI'):
                # Test different models
                models_to_test = [
                    ("text-embedding-3-small", 1536),
                    ("text-embedding-3-large", 3072),
                    ("text-embedding-ada-002", 1536)
                ]
                
                for model_name, expected_dims in models_to_test:
                    embedder = OpenAIEmbedder(api_key="sk-test123", model=model_name)
                    assert embedder.model == model_name
                    assert embedder.model_config["dimensions"] == expected_dims
                    assert "max_tokens" in embedder.model_config
                    assert "cost_per_1k_tokens" in embedder.model_config
    
    def test_estimate_tokens(self):
        """Test token estimation."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI'):
                embedder = OpenAIEmbedder(api_key="sk-test123")
                
                # Test token estimation (approximately 4 chars per token)
                assert embedder._estimate_tokens("hello") >= 1
                assert embedder._estimate_tokens("a" * 100) >= 25
                assert embedder._estimate_tokens("") == 1  # Minimum 1 token
    
    def test_calculate_cost(self):
        """Test cost calculation."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI'):
                embedder = OpenAIEmbedder(api_key="sk-test123")
                
                # Test cost calculation
                cost = embedder._calculate_cost(1000)
                expected_cost = embedder.model_config["cost_per_1k_tokens"]
                assert cost == expected_cost
                
                cost = embedder._calculate_cost(500)
                assert cost == expected_cost / 2
    
    def test_embed_text_success(self):
        """Test successful single text embedding."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI') as mock_openai:
                # Mock the API response
                mock_client = MagicMock()
                mock_response = MagicMock()
                mock_response.data = [MagicMock()]
                mock_response.data[0].embedding = list(np.random.rand(1536))
                mock_response.usage.total_tokens = 10
                mock_client.embeddings.create.return_value = mock_response
                mock_openai.return_value = mock_client
                
                embedder = OpenAIEmbedder(api_key="sk-test123")
                result = embedder.embed_text("test text")
                
                assert isinstance(result, EmbeddingResult)
                assert result.text == "test text"
                assert len(result.embedding) == 1536
                assert result.model == "text-embedding-3-small"
                assert result.token_count == 10
                assert result.processing_time > 0
                assert result.cost_estimate > 0
                assert result.error is None
    
    def test_embed_text_api_error(self):
        """Test handling of API errors."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI') as mock_openai:
                # Mock API error
                mock_client = MagicMock()
                mock_client.embeddings.create.side_effect = Exception("API Error")
                mock_openai.return_value = mock_client
                
                embedder = OpenAIEmbedder(api_key="sk-test123")
                result = embedder.embed_text("test text")
                
                assert isinstance(result, EmbeddingResult)
                assert result.text == "test text"
                assert result.embedding == []
                assert result.error is not None
                assert "API Error" in result.error
    
    def test_embed_batch_success(self):
        """Test successful batch embedding."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI') as mock_openai:
                # Mock the API response
                mock_client = MagicMock()
                mock_response = MagicMock()
                mock_response.data = [MagicMock(), MagicMock()]
                mock_response.data[0].embedding = list(np.random.rand(1536))
                mock_response.data[1].embedding = list(np.random.rand(1536))
                mock_response.usage.total_tokens = 20
                mock_client.embeddings.create.return_value = mock_response
                mock_openai.return_value = mock_client
                
                embedder = OpenAIEmbedder(api_key="sk-test123")
                texts = ["text 1", "text 2"]
                results = embedder.embed_batch(texts)
                
                assert len(results) == 2
                for i, result in enumerate(results):
                    assert isinstance(result, EmbeddingResult)
                    assert result.text == texts[i]
                    assert len(result.embedding) == 1536
                    assert result.model == "text-embedding-3-small"
                    assert result.error is None
    
    def test_embed_batch_empty_list(self):
        """Test batch embedding with empty list."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI'):
                embedder = OpenAIEmbedder(api_key="sk-test123")
                results = embedder.embed_batch([])
                
                assert results == []
    
    def test_embed_batch_large_batch(self):
        """Test batch embedding with large batch (should split)."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI') as mock_openai:
                # Mock the API response
                mock_client = MagicMock()
                mock_response = MagicMock()
                # Create responses for batches
                mock_response.data = [MagicMock() for _ in range(100)]
                for data in mock_response.data:
                    data.embedding = list(np.random.rand(1536))
                mock_response.usage.total_tokens = 1000
                mock_client.embeddings.create.return_value = mock_response
                mock_openai.return_value = mock_client
                
                embedder = OpenAIEmbedder(api_key="sk-test123")
                texts = [f"text {i}" for i in range(150)]  # More than batch size
                results = embedder.embed_batch(texts)
                
                assert len(results) == 150
                # Should have called API multiple times for large batch
                assert mock_client.embeddings.create.call_count >= 2
    
    def test_rate_limiting(self):
        """Test rate limiting functionality."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI'):
                embedder = OpenAIEmbedder(api_key="sk-test123")
                
                # Test rate limit checking (should not block with low usage)
                embedder._check_rate_limits(1000)
                
                # Test request tracking
                import time
                current_time = time.time()
                embedder._request_times = [current_time] * 3000  # Max requests
                embedder._token_counts = [(current_time, 1000000)]  # Max tokens
                
                # Should handle rate limits gracefully
                with patch('time.sleep') as mock_sleep:
                    embedder._check_rate_limits(1000)
                    # Should sleep when rate limit hit
                    mock_sleep.assert_called()
    
    def test_text_truncation(self):
        """Test text truncation for long inputs."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI'):
                embedder = OpenAIEmbedder(api_key="sk-test123")
                
                # Test with text that's too long
                long_text = "word " * 10000  # Very long text
                truncated = embedder.truncate_text(long_text)
                
                # Should be truncated but not empty
                assert len(truncated) < len(long_text)
                assert len(truncated) > 0
    
    def test_get_model_info(self):
        """Test getting model information."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI'):
                embedder = OpenAIEmbedder(api_key="sk-test123")
                
                info = embedder.get_model_info()
                
                assert info["provider"] == "openai"
                assert info["model"] == "text-embedding-3-small"
                assert info["dimensions"] == 1536
                assert info["supports_batch"] is True
                assert "rate_limits" in info
    
    def test_get_usage_stats(self):
        """Test usage statistics tracking."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI'):
                embedder = OpenAIEmbedder(api_key="sk-test123")
                
                # Simulate some usage
                import time
                current_time = time.time()
                embedder._request_times = [current_time - 30, current_time - 10]
                embedder._token_counts = [(current_time - 30, 100), (current_time - 10, 200)]
                
                stats = embedder.get_usage_stats()
                
                assert stats["total_requests"] == 2
                assert stats["total_tokens"] == 300
                assert stats["total_cost_estimate"] > 0
                assert stats["recent_requests_per_minute"] == 2
                assert stats["recent_tokens_per_minute"] == 300
                assert stats["average_tokens_per_request"] == 150


class TestRetryableEmbedder:
    """Test the retry functionality in the base embedder."""
    
    def test_retry_on_failure(self):
        """Test retry logic on API failures."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI') as mock_openai:
                # Create a mock that fails twice then succeeds
                mock_client = MagicMock()
                call_count = 0
                
                def side_effect(*args, **kwargs):
                    nonlocal call_count
                    call_count += 1
                    if call_count <= 2:
                        raise Exception("Temporary error")
                    
                    # Success on third try
                    mock_response = MagicMock()
                    mock_response.data = [MagicMock()]
                    mock_response.data[0].embedding = list(np.random.rand(1536))
                    mock_response.usage.total_tokens = 10
                    return mock_response
                
                mock_client.embeddings.create.side_effect = side_effect
                mock_openai.return_value = mock_client
                
                embedder = OpenAIEmbedder(api_key="sk-test123", max_retries=3, base_delay=0.1)
                
                with patch('time.sleep'):  # Speed up test
                    result = embedder.embed_text("test text")
                
                # Should succeed after retries
                assert result.error is None
                assert len(result.embedding) == 1536
                assert call_count == 3  # Failed twice, succeeded on third
    
    def test_max_retries_exceeded(self):
        """Test behavior when max retries are exceeded."""
        with patch('claude_indexer.embeddings.openai.OPENAI_AVAILABLE', True):
            with patch('claude_indexer.embeddings.openai.openai.OpenAI') as mock_openai:
                # Mock that always fails
                mock_client = MagicMock()
                mock_client.embeddings.create.side_effect = Exception("Persistent error")
                mock_openai.return_value = mock_client
                
                embedder = OpenAIEmbedder(api_key="sk-test123", max_retries=2, base_delay=0.1)
                
                with patch('time.sleep'):  # Speed up test
                    result = embedder.embed_text("test text")
                
                # Should fail after max retries
                assert result.error is not None
                assert "Persistent error" in result.error
                assert result.embedding == []


class TestEmbeddingResult:
    """Test the EmbeddingResult dataclass."""
    
    def test_embedding_result_creation(self):
        """Test creating EmbeddingResult instances."""
        embedding = list(np.random.rand(1536))
        
        result = EmbeddingResult(
            text="test text",
            embedding=embedding,
            model="test-model",
            token_count=10,
            processing_time=0.5,
            cost_estimate=0.001
        )
        
        assert result.text == "test text"
        assert result.embedding == embedding
        assert result.model == "test-model"
        assert result.token_count == 10
        assert result.processing_time == 0.5
        assert result.cost_estimate == 0.001
        assert result.error is None
    
    def test_embedding_result_with_error(self):
        """Test EmbeddingResult with error."""
        result = EmbeddingResult(
            text="test text",
            embedding=[],
            model="test-model",
            error="Test error"
        )
        
        assert result.text == "test text"
        assert result.embedding == []
        assert result.error == "Test error"
    
    def test_embedding_result_defaults(self):
        """Test EmbeddingResult default values."""
        result = EmbeddingResult(
            text="test",
            embedding=[],
            model="test"
        )
        
        assert result.token_count == 0
        assert result.processing_time == 0.0
        assert result.cost_estimate == 0.0
        assert result.error is None


class TestDummyEmbedder:
    """Test the dummy embedder used in tests."""
    
    def test_dummy_embedder_deterministic(self, dummy_embedder):
        """Test that dummy embedder is deterministic."""
        text = "test text"
        
        embedding1 = dummy_embedder.embed_single(text)
        embedding2 = dummy_embedder.embed_single(text)
        
        # Should be identical for same text
        assert np.array_equal(embedding1, embedding2)
        assert len(embedding1) == 1536
        assert embedding1.dtype == np.float32
    
    def test_dummy_embedder_different_texts(self, dummy_embedder):
        """Test that different texts produce different embeddings."""
        text1 = "first text"
        text2 = "second text"
        
        embedding1 = dummy_embedder.embed_single(text1)
        embedding2 = dummy_embedder.embed_single(text2)
        
        # Should be different for different texts
        assert not np.array_equal(embedding1, embedding2)
        assert len(embedding1) == len(embedding2) == 1536
    
    def test_dummy_embedder_batch(self, dummy_embedder):
        """Test dummy embedder batch processing."""
        texts = ["text 1", "text 2", "text 3"]
        
        embeddings = dummy_embedder.embed(texts)
        
        assert len(embeddings) == 3
        for embedding in embeddings:
            assert len(embedding) == 1536
            assert embedding.dtype == np.float32
        
        # Should be different embeddings
        assert not np.array_equal(embeddings[0], embeddings[1])
        assert not np.array_equal(embeddings[1], embeddings[2])
    
    def test_dummy_embedder_custom_dimension(self):
        """Test dummy embedder with custom dimensions."""
        from tests.conftest import DummyEmbedder
        
        custom_embedder = DummyEmbedder(dimension=512)
        
        embedding = custom_embedder.embed_single("test")
        assert len(embedding) == 512
        assert embedding.dtype == np.float32