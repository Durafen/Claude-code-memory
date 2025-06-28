"""
Shared fixtures for Claude Indexer test suite.

Provides test fixtures for:
- Temporary repository creation
- Qdrant client/store setup
- Mock embedder for fast testing
- Configuration management
"""

import os
import shutil
import tempfile
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

import pytest
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, CollectionInfo

# Import project components
try:
    from claude_indexer.config import IndexerConfig
    from claude_indexer.embeddings.openai import OpenAIEmbedder
    from claude_indexer.storage.qdrant import QdrantStore
except ImportError:
    # Graceful fallback for missing imports during test discovery
    IndexerConfig = None
    OpenAIEmbedder = None
    QdrantStore = None


# ---------------------------------------------------------------------------
# Temporary repository fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def temp_repo(tmp_path_factory) -> Path:
    """Create a temporary repository with sample Python files for testing."""
    repo_path = tmp_path_factory.mktemp("sample_repo")
    
    # Create sample Python files
    (repo_path / "foo.py").write_text('''"""Sample module with functions."""

def add(x, y):
    """Return sum of two numbers."""
    return x + y

class Calculator:
    """Simple calculator class."""
    
    def multiply(self, a, b):
        """Multiply two numbers."""
        return a * b
''')
    
    (repo_path / "bar.py").write_text('''"""Module that imports and uses foo."""
from foo import add, Calculator

def main():
    """Main function that uses imported components."""
    result = add(1, 2)
    calc = Calculator()
    product = calc.multiply(3, 4)
    print(f"Results: {result}, {product}")

if __name__ == "__main__":
    main()
''')
    
    # Create a subdirectory with more code
    subdir = repo_path / "utils"
    subdir.mkdir()
    (subdir / "__init__.py").write_text("")
    (subdir / "helpers.py").write_text('''"""Helper utilities."""

def format_output(value):
    """Format value for display."""
    return f"Value: {value}"

LOG_LEVEL = "INFO"
''')
    
    # Create a test file (will be excluded by default)
    test_dir = repo_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_foo.py").write_text('''"""Tests for foo module."""
import pytest
from foo import add

def test_add():
    assert add(2, 3) == 5
''')
    
    return repo_path


@pytest.fixture()
def empty_repo(tmp_path_factory) -> Path:
    """Create an empty temporary repository."""
    return tmp_path_factory.mktemp("empty_repo")


# ---------------------------------------------------------------------------
# Qdrant test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qdrant_client() -> Iterator[QdrantClient]:
    """Create a Qdrant client for testing with session scope."""
    # Load config to get API key from settings.txt
    from claude_indexer.config import load_config
    config = load_config()
    
    # Use authentication if available
    if config.qdrant_api_key and config.qdrant_api_key != "default-key":
        client = QdrantClient(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key
        )
    else:
        # Fall back to unauthenticated for local testing
        client = QdrantClient("localhost", port=6333)
    
    # Create test collection if it doesn't exist
    collection_name = "test_collection"
    try:
        collections = client.get_collections().collections
        if not any(c.name == collection_name for c in collections):
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
                optimizers_config={
                    "indexing_threshold": 1000
                }
            )
    except Exception as e:
        pytest.skip(f"Qdrant not available: {e}")
    
    yield client
    
    # Cleanup: Remove all test collections after test session
    try:
        collections = client.get_collections().collections
        test_collections = [c.name for c in collections if 'test' in c.name.lower()]
        for collection_name in test_collections:
            try:
                client.delete_collection(collection_name)
                print(f"Cleaned up test collection: {collection_name}")
            except Exception as e:
                print(f"Warning: Failed to cleanup collection {collection_name}: {e}")
    except Exception as e:
        print(f"Warning: Failed to cleanup test collections: {e}")


@pytest.fixture()
def qdrant_store(qdrant_client) -> "QdrantStore":
    """Create a QdrantStore instance for testing."""
    if QdrantStore is None:
        pytest.skip("QdrantStore not available")
    
    # Load config to get API credentials 
    from claude_indexer.config import load_config
    config = load_config()
    
    store = QdrantStore(
        url=config.qdrant_url,
        api_key=config.qdrant_api_key if config.qdrant_api_key != "default-key" else None
    )
    
    # Clean up any existing test data
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        filter_obj = Filter(
            must=[FieldCondition(key="test", match=MatchValue(value=True))]
        )
        store.client.delete(
            collection_name="test_collection",
            points_selector=filter_obj
        )
    except Exception:
        # Skip cleanup if it fails
        pass
    
    return store


# ---------------------------------------------------------------------------
# Mock embedder fixtures
# ---------------------------------------------------------------------------

class DummyEmbedder:
    """Fast, deterministic embedder for testing."""
    
    def __init__(self, dimension: int = 1536):
        self.dimension = dimension
    
    def embed_text(self, text: str):
        """Generate embedding for single text - interface compatibility."""
        from claude_indexer.embeddings.base import EmbeddingResult
        
        # Create deterministic but unique embedding
        seed = hash(text) % 10000
        np.random.seed(seed)
        embedding = np.random.rand(self.dimension).astype(np.float32).tolist()
        
        return EmbeddingResult(
            text=text,
            embedding=embedding,
            model="dummy",
            token_count=len(text.split()),
            processing_time=0.001
        )
    
    def embed_batch(self, texts: list[str]):
        """Generate embeddings for multiple texts."""
        return [self.embed_text(text) for text in texts]
    
    def get_model_info(self):
        """Get model information."""
        return {
            "model": "dummy",
            "dimension": self.dimension,
            "max_tokens": 8192
        }
    
    def get_max_tokens(self):
        """Get maximum token limit."""
        return 8192
    
    def embed_single(self, text: str) -> np.ndarray:
        """Legacy method for backward compatibility."""
        result = self.embed_text(text)
        return np.array(result.embedding, dtype=np.float32)
    
    def embed(self, texts: list[str]) -> list[np.ndarray]:
        """Generate deterministic embeddings based on text hash."""
        embeddings = []
        for i, text in enumerate(texts):
            # Create deterministic but unique embeddings
            seed = hash(text) % 10000
            np.random.seed(seed)
            embedding = np.random.rand(self.dimension).astype(np.float32)
            embeddings.append(embedding)
        return embeddings


@pytest.fixture()
def dummy_embedder() -> DummyEmbedder:
    """Provide a fast, deterministic embedder for tests."""
    return DummyEmbedder()


@pytest.fixture()
def mock_openai_embedder(monkeypatch) -> DummyEmbedder:
    """Mock OpenAI embedder with dummy implementation."""
    dummy = DummyEmbedder()
    
    if OpenAIEmbedder is not None:
        monkeypatch.setattr(OpenAIEmbedder, "embed_text", dummy.embed_text)
        monkeypatch.setattr(OpenAIEmbedder, "embed_batch", dummy.embed_batch)
    
    return dummy


# ---------------------------------------------------------------------------
# Configuration fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_config(tmp_path) -> "IndexerConfig":
    """Create test configuration with temporary paths."""
    if IndexerConfig is None:
        pytest.skip("IndexerConfig class not available")
    
    # Load real config from settings.txt and create test settings file
    from claude_indexer.config import load_config
    real_config = load_config()
    
    settings_file = tmp_path / "test_settings.txt"
    settings_content = f"""
openai_api_key={real_config.openai_api_key}
qdrant_api_key={real_config.qdrant_api_key}
qdrant_url={real_config.qdrant_url}
"""
    settings_file.write_text(settings_content.strip())
    
    # Create temporary state directory for test isolation
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    
    config = load_config(settings_file)
    config.state_directory = state_dir  # Override state directory for tests
    return config


# ---------------------------------------------------------------------------
# File system fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_python_file(tmp_path) -> Path:
    """Create a single sample Python file for testing."""
    py_file = tmp_path / "sample.py"
    py_file.write_text('''"""Sample Python file for testing."""

class SampleClass:
    """A sample class."""
    
    def __init__(self, name: str):
        self.name = name
    
    def greet(self) -> str:
        """Return a greeting."""
        return f"Hello, {self.name}!"

def utility_function(data: list) -> int:
    """Process data and return count."""
    return len([x for x in data if x])

# Module-level variable
DEFAULT_NAME = "World"
''')
    return py_file


@pytest.fixture()
def sample_files_with_changes(tmp_path) -> tuple[Path, dict]:
    """Create sample files and return info about planned changes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    
    # Original file
    original = repo / "original.py"
    original.write_text('def old_func(): return "old"')
    
    # File to be modified
    modified = repo / "modified.py"
    modified.write_text('def func(): return 1')
    
    # File to be deleted
    deleted = repo / "deleted.py"
    deleted.write_text('def func(): return "delete me"')
    
    changes = {
        "modify": (modified, 'def func(): return 2'),  # Changed return value
        "delete": deleted,
        "add": (repo / "new.py", 'def new_func(): return "new"')
    }
    
    return repo, changes


# ---------------------------------------------------------------------------
# Async fixtures for file watching tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def event_loop():
    """Create an event loop for async tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Marker decorators
# ---------------------------------------------------------------------------

def requires_qdrant(func):
    """Decorator to skip tests if Qdrant is not available."""
    return pytest.mark.skipif(
        not _qdrant_available(),
        reason="Qdrant not available"
    )(func)


def requires_openai(func):
    """Decorator to skip tests if OpenAI API key is not available."""
    return pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="OpenAI API key not available"
    )(func)


def _qdrant_available() -> bool:
    """Check if Qdrant is available."""
    try:
        # Load config to get API key from settings.txt
        from claude_indexer.config import load_config
        config = load_config()
        
        # Use authentication if available
        if config.qdrant_api_key and config.qdrant_api_key != "default-key":
            client = QdrantClient(
                url=config.qdrant_url,
                api_key=config.qdrant_api_key
            )
        else:
            # Fall back to unauthenticated for local testing
            client = QdrantClient("localhost", port=6333)
            
        client.get_collections()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Additional cleanup fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="function")
def cleanup_test_collections_on_failure():
    """Cleanup test collections after each test function to prevent accumulation."""
    yield  # Run the test
    
    # Only perform cleanup if Qdrant is available
    if not _qdrant_available():
        return
    
    # Cleanup any collections created during this test that match test patterns
    try:
        from claude_indexer.config import load_config
        config = load_config()
        
        if config.qdrant_api_key and config.qdrant_api_key != "default-key":
            client = QdrantClient(
                url=config.qdrant_url,
                api_key=config.qdrant_api_key
            )
        else:
            client = QdrantClient("localhost", port=6333)
        
        collections = client.get_collections().collections
        # Only cleanup collections that look like temporary test collections
        temp_test_collections = [
            c.name for c in collections 
            if ('test' in c.name.lower() and 
                (any(char.isdigit() for char in c.name) or  # has numbers (likely timestamps)
                 c.name.startswith('test_') or  # any test collection
                 c.name.endswith('_test') or   # reverse pattern
                 'integration' in c.name.lower() or  # integration tests
                 'delete' in c.name.lower()))  # deletion tests
        ]
        
        for collection_name in temp_test_collections:
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass  # Ignore individual failures
                
    except Exception:
        pass  # Ignore all cleanup failures to not interfere with test results


# ---------------------------------------------------------------------------
# Utility functions for tests
# ---------------------------------------------------------------------------

def assert_valid_embedding(embedding: np.ndarray, expected_dim: int = 1536):
    """Assert that an embedding has the correct shape and type."""
    assert isinstance(embedding, np.ndarray)
    assert embedding.shape == (expected_dim,)
    assert embedding.dtype == np.float32
    assert not np.isnan(embedding).any()
    assert not np.isinf(embedding).any()


def count_python_files(path: Path) -> int:
    """Count Python files in a directory recursively."""
    return len(list(path.rglob("*.py")))


def wait_for_eventual_consistency(
    search_func,
    expected_count: int = 0,
    timeout: float = 15.0,
    initial_delay: float = 0.5,
    max_delay: float = 3.0,
    backoff_multiplier: float = 1.2,
    verbose: bool = False
) -> bool:
    """
    Wait for Qdrant eventual consistency by retrying searches until entities are properly deleted.
    
    Args:
        search_func: Function that returns search results (should return list/count)
        expected_count: Expected number of results after consistency (default: 0 for deletions)
        timeout: Maximum time to wait in seconds
        initial_delay: Initial delay between retries
        max_delay: Maximum delay between retries
        backoff_multiplier: Multiplier for exponential backoff
        verbose: Print debug information
        
    Returns:
        True if eventual consistency achieved, False if timeout
    """
    import time
    
    start_time = time.time()
    delay = initial_delay
    attempt = 0
    last_results = None
    
    # Start with a small initial delay to allow Qdrant to process the deletion
    time.sleep(0.2)
    
    while time.time() - start_time < timeout:
        attempt += 1
        
        try:
            results = search_func()
            
            # Handle different return types
            if hasattr(results, '__len__'):
                actual_count = len(results)
            elif isinstance(results, (int, float)):
                actual_count = int(results)
            else:
                actual_count = 1 if results else 0
                
            if verbose:
                print(f"Attempt {attempt}: Expected {expected_count}, got {actual_count}")
                if hasattr(results, '__len__') and len(results) > 0 and verbose:
                    # Show details of what's still being found
                    for i, result in enumerate(results[:3]):  # Show first 3 results
                        if hasattr(result, 'payload'):
                            name = result.payload.get('name', 'Unknown')
                            file_path = result.payload.get('file_path', 'Unknown')
                            print(f"  Still found #{i+1}: {name} in {file_path}")
                        else:
                            print(f"  Still found #{i+1}: {result}")
                
            if actual_count == expected_count:
                if verbose:
                    print(f"Eventual consistency achieved after {time.time() - start_time:.2f}s")
                return True
                
            # If count hasn't changed for several attempts, try a longer delay
            if last_results is not None and actual_count == last_results:
                delay = min(delay * 1.5, max_delay)
            
            last_results = actual_count
                
        except Exception as e:
            if verbose:
                print(f"Search attempt {attempt} failed: {e}")
            # Continue retrying on search errors
            
        # Wait before next attempt with exponential backoff
        time.sleep(delay)
        delay = min(delay * backoff_multiplier, max_delay)
    
    if verbose:
        print(f"Timeout reached after {timeout}s, last count: {last_results}")
    return False


def wait_for_collection_ready(
    qdrant_store,
    collection_name: str,
    timeout: float = 15.0,
    initial_delay: float = 0.2,
    max_delay: float = 2.0,
    verbose: bool = False
) -> bool:
    """
    Wait for a Qdrant collection to exist and be ready for operations.
    
    Args:
        qdrant_store: QdrantStore instance
        collection_name: Name of collection to wait for
        timeout: Maximum time to wait in seconds
        initial_delay: Initial delay between checks
        max_delay: Maximum delay between checks
        verbose: Print debug information
        
    Returns:
        True if collection is ready, False if timeout
    """
    import time
    
    start_time = time.time()
    delay = initial_delay
    attempt = 0
    
    if verbose:
        print(f"Waiting for collection '{collection_name}' to be ready...")
    
    while time.time() - start_time < timeout:
        attempt += 1
        
        try:
            # Check if collection exists
            if qdrant_store.collection_exists(collection_name):
                # Try a simple count operation to verify it's ready
                count = qdrant_store.count(collection_name)
                if verbose:
                    print(f"Collection '{collection_name}' ready with {count} points")
                return True
            elif verbose:
                print(f"Attempt {attempt}: Collection '{collection_name}' not found")
                
        except Exception as e:
            if verbose:
                print(f"Attempt {attempt}: Collection check failed: {e}")
        
        time.sleep(delay)
        delay = min(delay * 1.5, max_delay)
    
    if verbose:
        print(f"Timeout: Collection '{collection_name}' not ready after {timeout}s")
    return False


def verify_entity_searchable(
    qdrant_store,
    dummy_embedder,
    collection_name: str,
    entity_name: str,
    timeout: float = 10.0,
    verbose: bool = False,
    expected_count: int = 1
) -> bool:
    """
    Verify that a specific entity is indexed and searchable.
    
    Args:
        qdrant_store: QdrantStore instance
        dummy_embedder: Embedder for search queries
        collection_name: Collection to search in
        entity_name: Name of entity to search for
        timeout: Maximum time to wait
        verbose: Print debug information
        expected_count: Expected number of entities to find (default: 1)
        
    Returns:
        True if entity is found, False if timeout
    """
    def search_for_entity():
        search_embedding = dummy_embedder.embed_single(entity_name)
        # Increase top_k to handle cases where target entity might not be in top 10
        # due to DummyEmbedder's deterministic but not perfect scoring
        hits = qdrant_store.search(collection_name, search_embedding, top_k=50)
        matching_hits = [
            hit for hit in hits 
            if entity_name in hit.payload.get("name", "")
        ]
        return matching_hits
    
    return wait_for_eventual_consistency(
        search_for_entity,
        expected_count=expected_count,
        timeout=timeout,
        verbose=verbose
    )