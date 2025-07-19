"""
Integration tests for the complete indexing workflow.

Tests the interaction between parser, embedder, and storage components
during the full indexing process.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

from claude_indexer.config import IndexerConfig, load_config
from claude_indexer.indexer import CoreIndexer


def no_errors_in_logs(stderr_output: str, stdout_output: str) -> bool:
    """Helper function to check if there are no errors in the log output."""
    # Check stderr for actual errors
    if stderr_output.strip():
        # Allow some non-error output in stderr (like debug messages and warnings)
        error_indicators = ["error:", "exception:", "traceback", "❌"]
        # More specific error patterns, excluding common warnings
        strict_errors = [
            "error: ",
            "exception: ",
            "traceback:",
            "❌",
            "critical:",
            "fatal:",
        ]

        # Skip benign warnings like "Failed to get global entities" on new collections
        benign_patterns = [
            "failed to get global entities",
            "collection `",
            "doesn't exist",
            "warning",
            "info",
            "debug",
        ]

        # Check if any line contains errors but isn't benign
        for line in stderr_output.lower().split("\n"):
            line = line.strip()
            if any(error in line for error in strict_errors):
                # Skip if it's a benign warning
                if not any(pattern in line for pattern in benign_patterns):
                    return False

    # Check stdout for error indicators
    error_indicators = ["❌", "error:", "exception:", "traceback"]
    if any(indicator in stdout_output.lower() for indicator in error_indicators):
        return False

    return True


def validate_state_file_structure(state_file_path: str, expected_files: set) -> dict:
    """Validate state file JSON structure and file contents."""
    import json

    assert Path(state_file_path).exists(), (
        f"State file should exist at {state_file_path}"
    )

    with open(state_file_path) as f:
        state_data = json.load(f)

    # Validate JSON structure
    assert isinstance(state_data, dict), "State file should contain a JSON object"

    # Check expected files are present
    state_files = set(state_data.keys())
    for expected_file in expected_files:
        assert expected_file in state_files, (
            f"Expected file {expected_file} not in state: {state_files}"
        )

    # Validate each file's metadata structure
    for file_path, file_state in state_data.items():
        assert isinstance(file_state, dict), (
            f"File state for {file_path} should be an object"
        )

        # Skip validation for special metadata entries like _statistics
        if file_path.startswith("_"):
            continue

        assert "hash" in file_state, f"Hash missing for {file_path}"
        assert "size" in file_state, f"Size missing for {file_path}"
        assert "mtime" in file_state, f"Mtime missing for {file_path}"

        # Validate hash format (SHA256 should be 64 characters)
        assert len(file_state["hash"]) == 64, (
            f"Invalid hash length for {file_path}: {file_state['hash']}"
        )
        assert isinstance(file_state["size"], int), (
            f"Size should be integer for {file_path}"
        )
        assert isinstance(file_state["mtime"], (int, float)), (
            f"Mtime should be number for {file_path}"
        )

    return state_data


def extract_deletion_info_from_cli_output(output: str) -> dict:
    """Extract deletion information from verbose CLI output."""
    lines = output.split("\n")
    deleted_files = []
    handled_deletions = 0
    orphan_cleanups = 0

    for line in lines:
        line_lower = line.lower()

        # Look for deletion patterns in verbose output
        if "🗑️ handling deleted file" in line_lower or "deleted file:" in line_lower:
            handled_deletions += 1
            # Try to extract filename
            if ".py" in line:
                # Extract filename from various patterns
                parts = line.split()
                for part in parts:
                    if part.endswith(".py") and not part.startswith("🗑️"):
                        deleted_files.append(part)
                        break

        # Look for orphan relation cleanup
        if "🗑️ deleted" in line_lower and "orphaned relation" in line_lower:
            orphan_cleanups += 1

    return {
        "deleted_files": deleted_files,
        "handled_deletions": handled_deletions,
        "orphan_cleanups": orphan_cleanups,
    }


def extract_processed_files_from_cli_output(output: str) -> list:
    """Extract processed filenames from verbose CLI output."""
    lines = output.split("\n")
    processed_files = []

    for line in lines:
        line_lower = line.lower()

        # Look for file processing patterns
        if ("processing" in line_lower or "indexing" in line_lower) and ".py" in line:
            # Extract filename from various possible patterns
            if "file:" in line_lower:
                parts = line.split("file:")
                if len(parts) > 1:
                    filename = parts[1].strip().split()[0]
                    if filename.endswith(".py"):
                        processed_files.append(filename)
            elif "batch" in line_lower:
                # Look for batch processing indicators
                continue
            else:
                # Try to find .py files in the line
                parts = line.split()
                for part in parts:
                    if part.endswith(".py") and not part.startswith("🔄"):
                        processed_files.append(part)

    return processed_files


@pytest.mark.integration
class TestACustomFlow:
    """Test complete indexing workflows."""

    def test_full_index_flow_with_real_files(
        self, temp_repo, dummy_embedder, qdrant_store
    ):
        """Test complete indexing flow with real Python files."""
        # Load real API keys from settings.txt instead of using hardcoded test keys
        base_config = load_config()
        config = IndexerConfig(
            openai_api_key=base_config.openai_api_key,
            qdrant_api_key=base_config.qdrant_api_key,
            qdrant_url=base_config.qdrant_url,
        )

        indexer = CoreIndexer(
            config=config,
            embedder=dummy_embedder,
            vector_store=qdrant_store,
            project_path=temp_repo,
        )

        # Index the temporary repository
        result = indexer.index_project("test_integration")

        # Verify indexing succeeded
        assert result.success is True
        assert result.entities_created >= 3  # At least foo.py, bar.py, helpers.py
        assert result.relations_created >= 1  # At least one import relation

        # Verify vectors were stored
        count = qdrant_store.count("test_integration")
        assert count >= 3, f"Expected at least 3 vectors, got {count}"

        # Verify we can search for content
        search_embedding = dummy_embedder.embed_single("add function")
        hits = qdrant_store.search("test_integration", search_embedding, top_k=10)

        assert len(hits) > 0
        # Should find the add function from foo.py
        add_function_found = any(
            "add" in hit.payload.get("entity_name", "").lower()
            or "add" in hit.payload.get("name", "").lower()
            or "add" in hit.payload.get("content", "").lower()
            for hit in hits
        )
        assert add_function_found

    def test_incremental_indexing_flow(self, temp_repo, dummy_embedder, qdrant_store):
        """Test incremental indexing with file changes."""
        # Load real API keys from settings.txt instead of using hardcoded test keys
        base_config = load_config()
        config = IndexerConfig(
            openai_api_key=base_config.openai_api_key,
            qdrant_api_key=base_config.qdrant_api_key,
            qdrant_url=base_config.qdrant_url,
        )

        indexer = CoreIndexer(
            config=config,
            embedder=dummy_embedder,
            vector_store=qdrant_store,
            project_path=temp_repo,
        )

        # Initial index
        result1 = indexer.index_project("test_incremental")
        initial_count = qdrant_store.count("test_incremental")

        # Modify a file
        modified_file = temp_repo / "foo.py"
        original_content = modified_file.read_text()
        modified_content = (
            original_content
            + '\n\ndef subtract(x, y):\n    """Subtract two numbers."""\n    return x - y\n'
        )
        modified_file.write_text(modified_content)

        # Second index (should auto-detect incremental mode)
        result2 = indexer.index_project("test_incremental")
        final_count = qdrant_store.count("test_incremental")

        # Verify incremental indexing worked
        assert result2.success is True
        assert final_count >= initial_count  # Should have same or more vectors

        # Verify we can find the new function with eventual consistency
        from tests.conftest import wait_for_eventual_consistency

        def search_for_subtract():
            search_embedding = dummy_embedder.embed_single("subtract function")
            hits = qdrant_store.search("test_incremental", search_embedding, top_k=10)
            return [
                hit
                for hit in hits
                if "subtract" in hit.payload.get("entity_name", "").lower()
                or "subtract" in hit.payload.get("name", "").lower()
                or "subtract" in hit.payload.get("content", "").lower()
            ]

        # Debug: Check what entities exist in the collection
        all_entities = []
        try:
            scroll_result = qdrant_store.client.scroll(
                collection_name="test_incremental", limit=100, with_payload=True
            )
            all_entities = scroll_result[0] if scroll_result else []
        except Exception as e:
            print(f"Error scrolling collection: {e}")

        print(f"Total entities in collection: {len(all_entities)}")
        for entity in all_entities[:10]:  # Show first 10
            name = entity.payload.get("name", "N/A")
            file_path = entity.payload.get("file_path", "N/A")
            print(f"  - {name} (from {file_path})")

        # Wait for eventual consistency
        wait_for_eventual_consistency(
            search_for_subtract, expected_count=1, verbose=True
        )

        # Final verification
        search_embedding = dummy_embedder.embed_single("subtract function")
        hits = qdrant_store.search("test_incremental", search_embedding, top_k=10)

        print(f"Search results for 'subtract function': {len(hits)} hits")
        for hit in hits:
            name = hit.payload.get("name", "N/A")
            score = getattr(hit, "score", "N/A")
            print(f"  - {name} (score: {score})")

        # Look for subtract function in search results
        subtract_found = any(
            "subtract" in hit.payload.get("entity_name", "").lower()
            or "subtract" in hit.payload.get("name", "").lower()
            or "subtract" in hit.payload.get("content", "").lower()
            or "subtract" in str(hit.payload).lower()
            for hit in hits
        )

        # Enhanced debugging if test fails
        if not subtract_found:
            print("DEBUG: Detailed payload analysis for first 5 hits:")
            for i, hit in enumerate(hits[:5]):
                payload = hit.payload
                print(f"  Hit {i + 1}:")
                print(f"    - name: {payload.get('name', 'N/A')}")
                print(f"    - content: {payload.get('content', 'N/A')[:50]}...")
                print(f"    - entity_type: {payload.get('entity_type', 'N/A')}")
                print(f"    - file_path: {payload.get('file_path', 'N/A')}")
                print(f"    - full payload keys: {list(payload.keys())}")

        assert subtract_found, (
            f"subtract function not found in {len(hits)} search results"
        )

    def test_error_handling_in_flow(self, temp_repo, dummy_embedder, qdrant_store):
        """Test error handling during indexing flow."""
        config = IndexerConfig(
            collection_name="test_errors", embedder_type="dummy", storage_type="qdrant"
        )

        # Create a file with syntax errors
        bad_file = temp_repo / "bad_syntax.py"
        bad_file.write_text("def broken(\n    return 'invalid syntax'")

        indexer = CoreIndexer(
            config=config,
            embedder=dummy_embedder,
            vector_store=qdrant_store,
            project_path=temp_repo,
        )

        # Indexing should still succeed for valid files
        result = indexer.index_project("test_errors")

        # Should be successful overall despite individual file errors
        assert result.success is True
        assert result.entities_created >= 2  # Valid files still processed
        assert len(result.errors) >= 1  # Should track parsing errors

    def test_empty_project_indexing(self, empty_repo, dummy_embedder, qdrant_store):
        """Test indexing an empty project."""
        config = IndexerConfig(
            collection_name="test_empty", embedder_type="dummy", storage_type="qdrant"
        )

        indexer = CoreIndexer(
            config=config,
            embedder=dummy_embedder,
            vector_store=qdrant_store,
            project_path=empty_repo,
        )

        result = indexer.index_project("test_empty")

        # Should succeed with no entities
        assert result.success is True
        assert result.entities_created == 0
        assert result.relations_created == 0
        assert qdrant_store.count("test_empty") == 0

    def test_large_file_batching(self, tmp_path, dummy_embedder, qdrant_store):
        """Test indexing with many files to verify batching."""
        config = IndexerConfig(
            collection_name="test_batching",
            embedder_type="dummy",
            storage_type="qdrant",
        )

        # Create many small Python files
        for i in range(20):
            py_file = tmp_path / f"module_{i}.py"
            py_file.write_text(f'''"""Module {i}."""

def function_{i}():
    """Function number {i}."""
    return {i}

CLASS_{i} = "constant_{i}"
''')

        indexer = CoreIndexer(
            config=config,
            embedder=dummy_embedder,
            vector_store=qdrant_store,
            project_path=tmp_path,
        )

        result = indexer.index_project("test_batching")

        # Should successfully process all files
        assert result.success is True
        assert result.entities_created >= 40  # At least 2 entities per file
        assert qdrant_store.count("test_batching") >= 40

    def test_duplicate_entity_handling(self, tmp_path, dummy_embedder, qdrant_store):
        """Test handling of duplicate entities across files."""
        config = IndexerConfig(
            collection_name="test_duplicates",
            embedder_type="dummy",
            storage_type="qdrant",
        )

        # Create files with same function names
        file1 = tmp_path / "module1.py"
        file1.write_text('''
def common_function():
    """First implementation."""
    return 1
''')

        file2 = tmp_path / "module2.py"
        file2.write_text('''
def common_function():
    """Second implementation."""
    return 2
''')

        indexer = CoreIndexer(
            config=config,
            embedder=dummy_embedder,
            vector_store=qdrant_store,
            project_path=tmp_path,
        )

        result = indexer.index_project("test_duplicates")

        # Should handle duplicates gracefully
        assert result.success is True

        # Search should find both implementations
        search_embedding = dummy_embedder.embed_single("common_function")
        hits = qdrant_store.search("test_duplicates", search_embedding, top_k=10)

        # Should find function in both files
        file_paths = {hit.payload.get("file_path", "") for hit in hits}
        assert "module1.py" in str(file_paths)
        assert "module2.py" in str(file_paths)


@pytest.mark.integration
class TestIndexerConfiguration:
    """Test indexer configuration and initialization."""

    def test_indexer_with_different_embedders(self, temp_repo, qdrant_store):
        """Test indexer with different embedder configurations."""
        # Test with dummy embedder
        config = IndexerConfig(
            collection_name="test_embedders",
            embedder_type="dummy",
            storage_type="qdrant",
        )

        with patch(
            "claude_indexer.embeddings.registry.create_embedder_from_config"
        ) as mock_create:
            from claude_indexer.embeddings.base import EmbeddingResult

            mock_embedder = Mock()

            # Mock embed_text method to return EmbeddingResult
            def mock_embed_text(text):
                return EmbeddingResult(
                    text=text,
                    embedding=np.random.rand(1536).astype(np.float32).tolist(),
                    model="mock-model",
                    token_count=len(text.split()),
                    processing_time=0.001,
                    cost_estimate=0.0001,
                )

            # Mock embed_batch method to return list of EmbeddingResult
            def mock_embed_batch(texts):
                return [mock_embed_text(text) for text in texts]

            mock_embedder.embed_text.side_effect = mock_embed_text
            mock_embedder.embed_batch.side_effect = mock_embed_batch

            # Mock get_usage_stats to return proper dict structure
            mock_embedder.get_usage_stats.return_value = {
                "total_requests": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
            }

            mock_create.return_value = mock_embedder

            indexer = CoreIndexer(
                config=config,
                embedder=mock_embedder,
                vector_store=qdrant_store,
                project_path=temp_repo,
            )

            result = indexer.index_project("test_embedders")
            assert result.success is True

            # Verify embedder was used
            assert mock_embedder.embed_text.called or mock_embedder.embed_batch.called

    def test_indexer_with_custom_filters(self, temp_repo, dummy_embedder, qdrant_store):
        """Test indexer with custom file filters."""
        config = IndexerConfig(
            collection_name="test_filters",
            embedder_type="dummy",
            storage_type="qdrant",
            include_patterns=["*.py"],
            exclude_patterns=["test_*"],
        )

        # Add test files that should be excluded
        test_dir = temp_repo / "tests"
        test_dir.mkdir(exist_ok=True)
        (test_dir / "test_example.py").write_text("def test_something(): pass")

        indexer = CoreIndexer(
            config=config,
            embedder=dummy_embedder,
            vector_store=qdrant_store,
            project_path=temp_repo,
        )

        result = indexer.index_project("test_filters")

        # Should exclude test files
        assert result.success is True

        # Verify test files were not indexed
        search_embedding = dummy_embedder.embed_single("test_something")
        hits = qdrant_store.search("test_filters", search_embedding, top_k=10)

        test_files_found = any(
            "test_" in hit.payload.get("file_path", "") for hit in hits
        )
        assert not test_files_found


@pytest.mark.integration
class TestIndexerPerformance:
    """Test indexer performance characteristics."""

    def test_indexing_performance_tracking(
        self, temp_repo, dummy_embedder, qdrant_store
    ):
        """Test that indexing tracks performance metrics."""
        config = IndexerConfig(
            collection_name="test_performance",
            embedder_type="dummy",
            storage_type="qdrant",
        )

        indexer = CoreIndexer(
            config=config,
            embedder=dummy_embedder,
            vector_store=qdrant_store,
            project_path=temp_repo,
        )

        result = indexer.index_project("test_performance")

        # Should track timing information
        assert result.success is True
        assert hasattr(result, "duration")
        assert result.duration >= 0

        # Should have file-level statistics
        assert result.files_processed >= 3
        assert result.entities_created >= 3

    def test_memory_efficient_processing(self, tmp_path, dummy_embedder, qdrant_store):
        """Test that large projects don't consume excessive memory."""
        config = IndexerConfig(
            collection_name="test_memory", embedder_type="dummy", storage_type="qdrant"
        )

        # Create larger files to test memory usage
        for i in range(5):
            large_file = tmp_path / f"large_{i}.py"
            content = f'"""Large module {i}."""\n\n'

            # Add many functions to create a larger file
            for j in range(50):
                content += f'''
def function_{i}_{j}(param_{j}):
    """Function {i}-{j} with parameter."""
    result = "processing_{i}_{j}"
    return result
'''
            large_file.write_text(content)

        indexer = CoreIndexer(
            config=config,
            embedder=dummy_embedder,
            vector_store=qdrant_store,
            project_path=tmp_path,
        )

        # Should process without memory issues
        result = indexer.index_project("test_memory")

        assert result.success is True
        assert result.entities_created >= 250  # 5 files * 50 functions each
        assert qdrant_store.count("test_memory") >= 250


@pytest.mark.integration
class TestACustomIncrementalBehavior:
    """Custom tests for precise incremental indexing behavior verification."""

    def test_custom_single_new_file_processing(
        self, temp_repo, dummy_embedder, qdrant_store
    ):
        """Test that exactly 1 new file is processed in incremental mode using CLI."""
        import subprocess

        # Create settings file for CLI with real API keys
        base_config = load_config()
        settings_file = temp_repo / "settings.txt"
        settings_file.write_text(f"""
openai_api_key={base_config.openai_api_key}
qdrant_api_key={base_config.qdrant_api_key}
qdrant_url={base_config.qdrant_url}
""")

        # Run initial CLI indexing - should be full mode (auto-detected)
        result1 = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "index",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_new_file",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        initial_output = result1.stdout
        initial_errors = result1.stderr

        assert result1.returncode == 0, f"Initial indexing failed: {initial_errors}"
        initial_count = qdrant_store.count("test_custom_new_file")

        # CONSOLE LOG CHECKS - Initial indexing should show CLI output
        assert (
            "Mode: Full" in initial_output
            or "files to process" in initial_output.lower()
        ), f"Initial CLI should show mode or processing activity. Got: {initial_output}"
        assert no_errors_in_logs(initial_errors, initial_output), (
            f"Should have no errors in initial indexing. Errors: {initial_errors}"
        )

        # Find state file location (CLI uses project-local state directory)
        state_dir = temp_repo / ".claude-indexer"
        state_file = state_dir / "test_custom_new_file.json"

        # Verify state file was created after initial index
        assert state_file.exists(), f"State file should exist at {state_file}"

        # Load initial state file content
        with open(state_file) as f:
            initial_state = json.load(f)
        initial_state_file_count = len(initial_state)

        # Add exactly 1 new file
        new_file = temp_repo / "new_module.py"
        new_file.write_text('''
def new_function():
    """A new function to test incremental indexing."""
    return "new functionality"

NEW_CONSTANT = "test_value"
''')

        # Run incremental CLI indexing - should auto-detect incremental mode
        result2 = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "index",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_new_file",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        incremental_output = result2.stdout
        incremental_errors = result2.stderr

        assert result2.returncode == 0, (
            f"Incremental indexing failed: {incremental_errors}"
        )
        final_count = qdrant_store.count("test_custom_new_file")

        # CONSOLE LOG CHECKS - Incremental indexing should show CLI mode output
        assert (
            "Mode: Incremental" in incremental_output
            or "1 files to process" in incremental_output
        ), f"Should show incremental mode or file count. Got: {incremental_output}"
        # Check that incremental mode processed the new file
        assert any(
            phrase in incremental_output
            for phrase in [
                "Files processed: 1",
                "Total Vectored Files:",
                "files to process",
            ]
        ), f"Should show file processing activity. Got: {incremental_output}"
        assert no_errors_in_logs(incremental_errors, incremental_output), (
            f"Should have no errors in incremental indexing. Errors: {incremental_errors}"
        )

        # ENHANCED FILENAME VALIDATION - Parse CLI verbose output and validate state
        processed_files = extract_processed_files_from_cli_output(incremental_output)

        # ENHANCED STATE FILE VALIDATION
        # Filter out metadata keys (those starting with _) from expected files
        initial_files = {k for k in initial_state.keys() if not k.startswith("_")}
        expected_files_in_final_state = initial_files | {"new_module.py"}
        final_state_validated = validate_state_file_structure(
            str(state_file), expected_files_in_final_state
        )

        # Verify vector count increased (new entities added)
        assert final_count > initial_count, (
            f"Vector count should increase from {initial_count} to {final_count}"
        )

        # Verify state file was updated with exactly 1 additional file
        with open(state_file) as f:
            final_state = json.load(f)
        final_state_file_count = len(final_state)

        assert final_state_file_count == initial_state_file_count + 1, (
            f"State file should have {initial_state_file_count + 1} files, got {final_state_file_count}"
        )
        assert "new_module.py" in final_state, "new_module.py should be in state file"

        # Verify state file contains correct metadata for new file
        new_file_state = final_state["new_module.py"]
        assert "hash" in new_file_state, "State should contain hash for new file"
        assert "size" in new_file_state, "State should contain size for new file"
        assert "mtime" in new_file_state, "State should contain mtime for new file"

        # Verify we can find the new function using CLI search
        search_result = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "search",
                "new_function",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_new_file",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        assert search_result.returncode == 0, f"Search failed: {search_result.stderr}"
        assert "new_function" in search_result.stdout, (
            "Should find new_function in search results"
        )

    def test_custom_single_file_deletion(self, temp_repo, dummy_embedder, qdrant_store):
        """Test that exactly 1 deleted file is processed in incremental mode using CLI."""
        import subprocess

        # Create settings file for CLI with real API keys
        base_config = load_config()
        settings_file = temp_repo / "settings.txt"
        settings_file.write_text(f"""
openai_api_key={base_config.openai_api_key}
qdrant_api_key={base_config.qdrant_api_key}
qdrant_url={base_config.qdrant_url}
""")

        # Create an additional file to delete later
        deletable_file = temp_repo / "deletable.py"
        deletable_file.write_text('''
def deletable_function():
    """This function will be deleted."""
    return "will be deleted"

DELETABLE_CONSTANT = "to_be_removed"
''')

        # Run initial CLI indexing - should be full mode (auto-detected)
        result1 = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "index",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_deletion",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        initial_output = result1.stdout
        initial_errors = result1.stderr

        assert result1.returncode == 0, f"Initial indexing failed: {initial_errors}"
        initial_count = qdrant_store.count("test_custom_deletion")

        # CONSOLE LOG CHECKS - Initial indexing should show CLI output
        assert (
            "Mode: Full" in initial_output
            or "files to process" in initial_output.lower()
        ), f"Initial CLI should show mode or processing activity. Got: {initial_output}"
        assert no_errors_in_logs(initial_errors, initial_output), (
            f"Should have no errors in initial indexing. Errors: {initial_errors}"
        )

        # Find state file location (CLI uses project-local state directory)
        state_dir = temp_repo / ".claude-indexer"
        state_file = state_dir / "test_custom_deletion.json"

        # Verify state file was created and contains deletable file
        assert state_file.exists(), f"State file should exist at {state_file}"

        with open(state_file) as f:
            initial_state = json.load(f)
        # Count only actual files, not metadata keys
        initial_state_file_count = sum(
            1 for k in initial_state.keys() if not k.startswith("_")
        )
        assert "deletable.py" in initial_state, (
            "deletable.py should be in initial state"
        )

        # Delete exactly 1 file
        deletable_file.unlink()

        # Run incremental CLI indexing - should auto-detect incremental mode and handle deletion
        result2 = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "index",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_deletion",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        deletion_output = result2.stdout
        deletion_errors = result2.stderr

        assert result2.returncode == 0, f"Deletion indexing failed: {deletion_errors}"
        final_count = qdrant_store.count("test_custom_deletion")

        # CONSOLE LOG CHECKS - Deletion processing should show CLI mode output
        assert "Mode: Incremental" in deletion_output, (
            f"Should show incremental mode. Got: {deletion_output}"
        )
        # Check that deletion mode shows appropriate output
        assert any(
            phrase in deletion_output
            for phrase in [
                "Files processed: 0",
                "No files to process",
                "deleted files",
                "Handled",
            ]
        ), f"Should show deletion or no processing activity. Got: {deletion_output}"
        assert no_errors_in_logs(deletion_errors, deletion_output), (
            f"Should have no errors in deletion processing. Errors: {deletion_errors}"
        )

        # ENHANCED DELETION VALIDATION - Parse CLI verbose output for deletion information
        deletion_info = extract_deletion_info_from_cli_output(deletion_output)

        # ENHANCED STATE FILE VALIDATION FOR DELETION
        # Filter out metadata keys (those starting with _) from expected files
        initial_files = {k for k in initial_state.keys() if not k.startswith("_")}
        expected_files_after_deletion = initial_files - {"deletable.py"}
        final_state_validated = validate_state_file_structure(
            str(state_file), expected_files_after_deletion
        )

        # Verify vector count decreased (entities removed)
        assert final_count < initial_count, (
            f"Vector count should decrease from {initial_count} to {final_count}"
        )

        # Verify state file was updated with file removed
        with open(state_file) as f:
            final_state = json.load(f)
        # Count only actual files, not metadata keys
        final_state_file_count = sum(
            1 for k in final_state.keys() if not k.startswith("_")
        )

        assert final_state_file_count == initial_state_file_count - 1, (
            f"State file should have {initial_state_file_count - 1} files, got {final_state_file_count}"
        )
        assert "deletable.py" not in final_state, (
            "deletable.py should not be in final state"
        )

        # Verify we can no longer find the deleted function using CLI search
        search_result = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "search",
                "deletable_function",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_deletion",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        assert search_result.returncode == 0, f"Search failed: {search_result.stderr}"
        # The search output will show "Found X results for: deletable_function"
        # but should not show "deletable_function" as an actual result entity
        # Check that no line starts with a number and contains "deletable_function" as the entity name
        lines = search_result.stdout.split("\n")
        for line in lines:
            # Result lines look like: "1. entity_name (score: 0.123)"
            if line.strip() and line[0].isdigit() and ". " in line:
                entity_name = line.split(". ", 1)[1].split(" (score:")[0]
                assert entity_name != "deletable_function", (
                    f"Found deleted function in results: {line}"
                )

        # Verify remaining files from temp_repo are still indexed
        remaining_files = set(final_state.keys())
        expected_remaining = {
            "foo.py",
            "bar.py",
            "utils/helpers.py",
            "utils/__init__.py",
        }
        assert expected_remaining.issubset(remaining_files), (
            f"Expected remaining files {expected_remaining}, got {remaining_files}"
        )

    def test_custom_three_new_files_processing(
        self, temp_repo, dummy_embedder, qdrant_store
    ):
        """Test that exactly 3 new files are processed in incremental mode using CLI."""
        import subprocess

        # Create settings file for CLI with real API keys
        base_config = load_config()
        settings_file = temp_repo / "settings.txt"
        settings_file.write_text(f"""
openai_api_key={base_config.openai_api_key}
qdrant_api_key={base_config.qdrant_api_key}
qdrant_url={base_config.qdrant_url}
""")

        # Run initial CLI indexing - should be full mode (auto-detected)
        result1 = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "index",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_three_new",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        initial_output = result1.stdout
        initial_errors = result1.stderr

        assert result1.returncode == 0, f"Initial indexing failed: {initial_errors}"
        initial_count = qdrant_store.count("test_custom_three_new")

        # CONSOLE LOG CHECKS - Initial indexing should show CLI output
        assert (
            "Mode: Full" in initial_output
            or "files to process" in initial_output.lower()
        ), f"Initial CLI should show mode or processing activity. Got: {initial_output}"
        assert no_errors_in_logs(initial_errors, initial_output), (
            f"Should have no errors in initial indexing. Errors: {initial_errors}"
        )

        # Find state file location (CLI uses project-local state directory)
        state_dir = temp_repo / ".claude-indexer"
        state_file = state_dir / "test_custom_three_new.json"

        # Verify state file was created after initial index
        assert state_file.exists(), f"State file should exist at {state_file}"

        # Load initial state file content
        with open(state_file) as f:
            initial_state = json.load(f)
        initial_state_file_count = len(initial_state)

        # Add exactly 3 new files
        new_files = []
        for i in range(1, 4):
            new_file = temp_repo / f"new_module_{i}.py"
            new_file.write_text(f'''
def new_function_{i}():
    """A new function {i} to test incremental indexing."""
    return "new functionality {i}"

NEW_CONSTANT_{i} = "test_value_{i}"

class NewClass_{i}:
    """A new class {i} for testing."""
    def method_{i}(self):
        return {i}
''')
            new_files.append(new_file)

        # Run incremental CLI indexing - should auto-detect incremental mode and process exactly 3 files
        result2 = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "index",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_three_new",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        incremental_output = result2.stdout
        incremental_errors = result2.stderr

        assert result2.returncode == 0, (
            f"Incremental indexing failed: {incremental_errors}"
        )
        final_count = qdrant_store.count("test_custom_three_new")

        # CONSOLE LOG CHECKS - Incremental indexing should show CLI mode output
        assert "Mode: Incremental" in incremental_output, (
            f"Should show incremental mode. Got: {incremental_output}"
        )
        # Check that incremental mode processed multiple files
        assert any(
            phrase in incremental_output
            for phrase in [
                "Files processed: 3",
                "Total Vectored Files:",
                "files to process",
            ]
        ), f"Should show file processing activity. Got: {incremental_output}"
        assert no_errors_in_logs(incremental_errors, incremental_output), (
            f"Should have no errors in incremental indexing. Errors: {incremental_errors}"
        )

        # ENHANCED FILENAME VALIDATION - Parse CLI verbose output and validate state
        processed_files = extract_processed_files_from_cli_output(incremental_output)

        # ENHANCED STATE FILE VALIDATION
        new_file_names = {f"new_module_{i}.py" for i in range(1, 4)}
        # Filter out metadata keys (those starting with _) from expected files
        initial_files = {k for k in initial_state.keys() if not k.startswith("_")}
        expected_files_in_final_state = initial_files | new_file_names
        final_state_validated = validate_state_file_structure(
            str(state_file), expected_files_in_final_state
        )

        # Verify vector count increased significantly (new entities added)
        assert final_count > initial_count, (
            f"Vector count should increase from {initial_count} to {final_count}"
        )

        # Verify state file was updated with exactly 3 additional files
        with open(state_file) as f:
            final_state = json.load(f)
        final_state_file_count = len(final_state)

        assert final_state_file_count == initial_state_file_count + 3, (
            f"State file should have {initial_state_file_count + 3} files, got {final_state_file_count}"
        )

        # Verify all 3 new files are in state file
        for i in range(1, 4):
            assert f"new_module_{i}.py" in final_state, (
                f"new_module_{i}.py should be in state file"
            )

            # Verify state file contains correct metadata for each new file
            new_file_state = final_state[f"new_module_{i}.py"]
            assert "hash" in new_file_state, (
                f"State should contain hash for new_module_{i}.py"
            )
            assert "size" in new_file_state, (
                f"State should contain size for new_module_{i}.py"
            )
            assert "mtime" in new_file_state, (
                f"State should contain mtime for new_module_{i}.py"
            )

        # Verify we can find the new functions using CLI search
        for i in range(1, 4):
            search_result = subprocess.run(
                [
                    "python",
                    "-m",
                    "claude_indexer",
                    "search",
                    f"new_function_{i}",
                    "--project",
                    str(temp_repo),
                    "--collection",
                    "test_custom_three_new",
                ],
                capture_output=True,
                text=True,
                cwd=temp_repo,
                timeout=120,
            )

            assert search_result.returncode == 0, (
                f"Search failed for new_function_{i}: {search_result.stderr}"
            )
            assert f"new_function_{i}" in search_result.stdout, (
                f"Should find new_function_{i} in search results"
            )

    def test_custom_three_files_deletion(self, temp_repo, dummy_embedder, qdrant_store):
        """Test that exactly 3 deleted files are processed in incremental mode using CLI."""
        import subprocess

        # Create settings file for CLI with real API keys
        base_config = load_config()
        settings_file = temp_repo / "settings.txt"
        settings_file.write_text(f"""
openai_api_key={base_config.openai_api_key}
qdrant_api_key={base_config.qdrant_api_key}
qdrant_url={base_config.qdrant_url}
""")

        # Create 3 additional files to delete later
        deletable_files = []
        for i in range(1, 4):
            deletable_file = temp_repo / f"deletable_{i}.py"
            deletable_file.write_text(f'''
def deletable_function_{i}():
    """This function {i} will be deleted."""
    return "will be deleted {i}"

DELETABLE_CONSTANT_{i} = "to_be_removed_{i}"

class DeletableClass_{i}:
    """A deletable class {i}."""
    def deletable_method_{i}(self):
        return "deletable_{i}"
''')
            deletable_files.append(deletable_file)

        # Run initial CLI indexing - should be full mode (auto-detected)
        result1 = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "index",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_three_deletion",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        initial_output = result1.stdout
        initial_errors = result1.stderr

        assert result1.returncode == 0, f"Initial indexing failed: {initial_errors}"
        initial_count = qdrant_store.count("test_custom_three_deletion")

        # CONSOLE LOG CHECKS - Initial indexing should show CLI output
        assert (
            "Mode: Full" in initial_output
            or "files to process" in initial_output.lower()
        ), f"Initial CLI should show mode or processing activity. Got: {initial_output}"
        assert no_errors_in_logs(initial_errors, initial_output), (
            f"Should have no errors in initial indexing. Errors: {initial_errors}"
        )

        # Find state file location (CLI uses project-local state directory)
        state_dir = temp_repo / ".claude-indexer"
        state_file = state_dir / "test_custom_three_deletion.json"

        # Verify state file was created and contains all deletable files
        assert state_file.exists(), f"State file should exist at {state_file}"

        with open(state_file) as f:
            initial_state = json.load(f)
        initial_state_file_count = len(initial_state)

        # Verify all 3 deletable files are in initial state
        for i in range(1, 4):
            assert f"deletable_{i}.py" in initial_state, (
                f"deletable_{i}.py should be in initial state"
            )

        # Delete exactly 3 files
        for deletable_file in deletable_files:
            deletable_file.unlink()

        # Run incremental CLI indexing - should auto-detect incremental mode and process all 3 deletions
        result2 = subprocess.run(
            [
                "python",
                "-m",
                "claude_indexer",
                "index",
                "--project",
                str(temp_repo),
                "--collection",
                "test_custom_three_deletion",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            cwd=temp_repo,
            timeout=120,
        )

        deletion_output = result2.stdout
        deletion_errors = result2.stderr

        assert result2.returncode == 0, f"Deletion indexing failed: {deletion_errors}"
        final_count = qdrant_store.count("test_custom_three_deletion")

        # CONSOLE LOG CHECKS - Deletion processing should show CLI mode output
        assert "Mode: Incremental" in deletion_output, (
            f"Should show incremental mode. Got: {deletion_output}"
        )
        # Check that deletion mode shows appropriate output
        assert any(
            phrase in deletion_output
            for phrase in [
                "Files processed: 0",
                "No files to process",
                "deleted files",
                "Handled",
            ]
        ), f"Should show deletion or no processing activity. Got: {deletion_output}"
        assert no_errors_in_logs(deletion_errors, deletion_output), (
            f"Should have no errors in deletion processing. Errors: {deletion_errors}"
        )

        # ENHANCED DELETION VALIDATION - Parse CLI verbose output for deletion information
        deletion_info = extract_deletion_info_from_cli_output(deletion_output)

        # ENHANCED STATE FILE VALIDATION FOR THREE-FILE DELETION
        deleted_file_names = {f"deletable_{i}.py" for i in range(1, 4)}
        # Filter out metadata keys (those starting with _) from expected files
        initial_files = {k for k in initial_state.keys() if not k.startswith("_")}
        expected_files_after_deletion = initial_files - deleted_file_names
        final_state_validated = validate_state_file_structure(
            str(state_file), expected_files_after_deletion
        )

        # Verify vector count decreased significantly (entities removed)
        assert final_count < initial_count, (
            f"Vector count should decrease from {initial_count} to {final_count}"
        )

        # The decrease should be substantial (3 files worth of entities)
        count_decrease = initial_count - final_count
        assert count_decrease >= 6, (
            f"Expected significant decrease (>=6), got {count_decrease}"
        )

        # Verify state file was updated with all 3 files removed
        with open(state_file) as f:
            final_state = json.load(f)
        final_state_file_count = len(final_state)

        assert final_state_file_count == initial_state_file_count - 3, (
            f"State file should have {initial_state_file_count - 3} files, got {final_state_file_count}"
        )

        # Verify all 3 deletable files are no longer in state file
        for i in range(1, 4):
            assert f"deletable_{i}.py" not in final_state, (
                f"deletable_{i}.py should not be in final state"
            )

        # Verify we can no longer find any of the deleted functions using CLI search
        for i in range(1, 4):
            search_result = subprocess.run(
                [
                    "python",
                    "-m",
                    "claude_indexer",
                    "search",
                    f"deletable_function_{i}",
                    "--project",
                    str(temp_repo),
                    "--collection",
                    "test_custom_three_deletion",
                ],
                capture_output=True,
                text=True,
                cwd=temp_repo,
                timeout=120,
            )

            assert search_result.returncode == 0, (
                f"Search failed for deletable_function_{i}: {search_result.stderr}"
            )
            # Check that no line starts with a number and contains "deletable_function_X" as the entity name
            lines = search_result.stdout.split("\n")
            for line in lines:
                # Result lines look like: "1. entity_name (score: 0.123)"
                if line.strip() and line[0].isdigit() and ". " in line:
                    entity_name = line.split(". ", 1)[1].split(" (score:")[0]
                    assert entity_name != f"deletable_function_{i}", (
                        f"Found deleted function in results: {line}"
                    )

        # Verify remaining files from temp_repo are still indexed
        remaining_files = set(final_state.keys())
        expected_remaining = {
            "foo.py",
            "bar.py",
            "utils/helpers.py",
            "utils/__init__.py",
        }
        assert expected_remaining.issubset(remaining_files), (
            f"Expected remaining files {expected_remaining}, got {remaining_files}"
        )
