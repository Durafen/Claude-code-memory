"""Unit tests for file hashing functionality."""

import hashlib

from claude_indexer.indexer import CoreIndexer


class TestFileHashing:
    """Test file hashing functionality."""

    def test_hash_consistency(self, tmp_path):
        """Test that file hashing is consistent."""
        # Create a test file
        test_file = tmp_path / "test.py"
        test_content = "print('hello world')"
        test_file.write_text(test_content)

        # Create indexer to test hashing
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()

        # Create mock components (we only need the _get_file_hash method)
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Test hash consistency
        hash1 = indexer._get_file_hash(test_file)
        hash2 = indexer._get_file_hash(test_file)

        assert hash1 == hash2
        assert isinstance(hash1, str)
        assert len(hash1) == 64  # SHA256 hex digest length

    def test_hash_changes_with_content(self, tmp_path):
        """Test that hash changes when file content changes."""
        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("original content")

        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Get initial hash
        hash1 = indexer._get_file_hash(test_file)

        # Modify file content
        test_file.write_text("modified content")

        # Get new hash
        hash2 = indexer._get_file_hash(test_file)

        assert hash1 != hash2
        assert len(hash1) == len(hash2) == 64

    def test_hash_empty_file(self, tmp_path):
        """Test hashing of empty file."""
        test_file = tmp_path / "empty.py"
        test_file.write_text("")

        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        indexer = CoreIndexer(config, None, None, tmp_path)

        file_hash = indexer._get_file_hash(test_file)

        # Hash of empty string
        expected_hash = hashlib.sha256(b"").hexdigest()
        assert file_hash == expected_hash

    def test_hash_nonexistent_file(self, tmp_path):
        """Test hashing of non-existent file."""
        nonexistent_file = tmp_path / "nonexistent.py"

        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        indexer = CoreIndexer(config, None, None, tmp_path)

        file_hash = indexer._get_file_hash(nonexistent_file)

        # Should return empty string for error case
        assert file_hash == ""

    def test_hash_binary_file(self, tmp_path):
        """Test hashing of binary content."""
        test_file = tmp_path / "binary.bin"
        binary_content = b"\x00\x01\x02\x03\xff\xfe\xfd"
        test_file.write_bytes(binary_content)

        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        indexer = CoreIndexer(config, None, None, tmp_path)

        file_hash = indexer._get_file_hash(test_file)

        # Calculate expected hash
        expected_hash = hashlib.sha256(binary_content).hexdigest()
        assert file_hash == expected_hash

    def test_hash_unicode_content(self, tmp_path):
        """Test hashing of Unicode content."""
        test_file = tmp_path / "unicode.py"
        unicode_content = "# -*- coding: utf-8 -*-\nprint('🚀 Hello, 世界!')"
        test_file.write_text(unicode_content, encoding="utf-8")

        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        indexer = CoreIndexer(config, None, None, tmp_path)

        file_hash = indexer._get_file_hash(test_file)

        # Calculate expected hash
        expected_hash = hashlib.sha256(unicode_content.encode("utf-8")).hexdigest()
        assert file_hash == expected_hash


class TestIndexingState:
    """Test indexing state management."""

    def test_state_file_creation(self, tmp_path):
        """Test that state file is created with correct name."""
        from claude_indexer.config import IndexerConfig

        # Create config with temporary state directory for test isolation
        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # State file should be in the configured state directory (tmp_path)
        assert indexer.state_file.parent == tmp_path
        assert indexer.state_file.name.endswith(".json")
        assert "default" in indexer.state_file.name

    def test_save_and_load_state(self, tmp_path):
        """Test saving and loading state."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Create test files
        test_files = []
        for i in range(3):
            file_path = tmp_path / f"test{i}.py"
            file_path.write_text(f"print('test {i}')")
            test_files.append(file_path)

        # Save state
        indexer._update_state(test_files, "default", full_rebuild=True)

        # Verify state file exists
        assert indexer.state_file.exists()

        # Load state
        loaded_state = indexer._load_state("default")

        # Verify state content
        assert isinstance(loaded_state, dict)
        assert len(loaded_state) == 3

        for file_path in test_files:
            file_key = str(file_path.relative_to(tmp_path))
            assert file_key in loaded_state
            assert "hash" in loaded_state[file_key]
            assert "mtime" in loaded_state[file_key]

    def test_state_roundtrip_consistency(self, tmp_path):
        """Test that state persists correctly across save/load cycles."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Create test file
        test_file = tmp_path / "test.py"
        test_file.write_text("test content")

        # Get current state
        current_state = indexer._get_current_state([test_file])

        # Save state
        indexer._update_state([test_file], "default", full_rebuild=True)

        # Load state
        loaded_state = indexer._load_state("default")

        # Compare states
        file_key = str(test_file.relative_to(tmp_path))
        assert current_state[file_key] == loaded_state[file_key]

    def test_empty_state_handling(self, tmp_path):
        """Test handling of empty state."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Load state when no state file exists
        loaded_state = indexer._load_state("default")

        assert loaded_state == {}

    def test_corrupted_state_handling(self, tmp_path):
        """Test handling of corrupted state file."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Create corrupted state file
        indexer.state_file.write_text("invalid json content")

        # Load state should return empty dict
        loaded_state = indexer._load_state("default")

        assert loaded_state == {}

    def test_get_current_state(self, tmp_path):
        """Test getting current state for files."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Create test files with different content
        files = []
        for i in range(2):
            file_path = tmp_path / f"file{i}.py"
            file_path.write_text(f"content {i}")
            files.append(file_path)

        # Get current state
        current_state = indexer._get_current_state(files)

        # Verify state structure
        assert len(current_state) == 2

        for file_path in files:
            file_key = str(file_path.relative_to(tmp_path))
            assert file_key in current_state

            state_entry = current_state[file_key]
            assert "hash" in state_entry
            assert "mtime" in state_entry
            assert isinstance(state_entry["hash"], str)
            assert len(state_entry["hash"]) == 64  # SHA256 length
            assert isinstance(state_entry["mtime"], float)

    def test_state_file_permissions_error(self, tmp_path, monkeypatch):
        """Test handling of state file permission errors."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Mock open to raise permission error
        def mock_open(*args, **kwargs):
            if "w" in str(kwargs.get("mode", "")) or len(args) > 1:
                raise PermissionError("Permission denied")
            # Allow reading
            return open(*args, **kwargs)

        monkeypatch.setattr("builtins.open", mock_open)

        test_file = tmp_path / "test.py"
        test_file.write_text("test")

        # Should not raise exception
        indexer._update_state([test_file], "default", full_rebuild=True)

        # Should return empty state
        loaded_state = indexer._load_state("default")
        assert loaded_state == {}


class TestIncrementalIndexing:
    """Test incremental indexing logic based on file hashes."""

    def test_detect_unchanged_files(self, tmp_path):
        """Test detection of unchanged files."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Create test file
        test_file = tmp_path / "test.py"
        test_file.write_text("original content")

        # Save initial state
        indexer._update_state([test_file], "test", full_rebuild=True)

        # Check if file needs processing (should not, as it's unchanged)
        files_to_process = indexer._get_files_needing_processing(
            include_tests=False, collection_name="test"
        )

        assert len(files_to_process) == 0

    def test_detect_modified_files(self, tmp_path):
        """Test detection of modified files."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Create test file
        test_file = tmp_path / "test.py"
        test_file.write_text("original content")

        # Save initial state
        indexer._update_state([test_file], "test", full_rebuild=True)

        # Modify file
        import time

        time.sleep(0.1)  # Ensure different timestamp
        test_file.write_text("modified content")

        # Check if file needs processing (should need processing)
        files_to_process = indexer._get_files_needing_processing(
            include_tests=False, collection_name="test"
        )

        assert len(files_to_process) == 1
        assert files_to_process[0] == test_file

    def test_detect_new_files(self, tmp_path):
        """Test detection of new files."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Create and save state for first file
        file1 = tmp_path / "file1.py"
        file1.write_text("content 1")
        indexer._update_state([file1], "test", full_rebuild=True)

        # Create new file
        file2 = tmp_path / "file2.py"
        file2.write_text("content 2")

        # Check processing needs
        files_to_process = indexer._get_files_needing_processing(
            include_tests=False, collection_name="test"
        )

        # Only new file should need processing
        assert len(files_to_process) == 1
        assert files_to_process[0] == file2

    def test_full_reprocessing(self, tmp_path):
        """Test full reprocessing finds all files."""
        from claude_indexer.config import IndexerConfig

        config = IndexerConfig()
        config.state_directory = tmp_path
        indexer = CoreIndexer(config, None, None, tmp_path)

        # Create test files
        files = []
        for i in range(3):
            file_path = tmp_path / f"file{i}.py"
            file_path.write_text(f"content {i}")
            files.append(file_path)

        # Save state
        indexer._update_state(files, "test", full_rebuild=True)

        # Get all files (full mode - not incremental)
        all_files = indexer._find_all_files(include_tests=False)

        # All files should be found in full mode
        assert len(all_files) == 3
        assert set(all_files) == set(files)
