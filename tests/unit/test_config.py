"""Unit tests for configuration management."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from claude_indexer.config import (
    IndexerConfig,
    create_default_settings_file,
    load_config,
    load_legacy_settings,
)


class TestIndexerConfig:
    """Test the IndexerConfig pydantic model."""

    def test_default_values(self):
        """Test that default configuration values are set correctly."""
        config = IndexerConfig()

        assert config.openai_api_key == ""
        assert config.qdrant_api_key == "default-key"
        assert config.qdrant_url == "http://localhost:6333"
        assert config.indexer_debug is False
        assert config.indexer_verbose is True
        assert config.debounce_seconds == 2.0
        assert config.include_markdown is True
        assert config.include_tests is False
        assert config.max_file_size == 1048576
        assert config.batch_size == 50
        assert config.max_concurrent_files == 10

    def test_environment_variable_override(self, monkeypatch):
        """Test that environment variables can be used in config creation."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test123")
        monkeypatch.setenv("QDRANT_URL", "https://custom.qdrant.com")

        config = IndexerConfig.from_env()

        assert config.openai_api_key == "sk-test123"
        assert config.qdrant_url == "https://custom.qdrant.com"

    def test_explicit_values_override_env(self, monkeypatch):
        """Test that explicit values override environment variables."""
        monkeypatch.setenv("QDRANT_URL", "https://env.qdrant.com")

        config = IndexerConfig(qdrant_url="https://explicit.qdrant.com")

        assert config.qdrant_url == "https://explicit.qdrant.com"

    def test_openai_key_setting(self):
        """Test OpenAI API key setting (no validation in current implementation)."""
        # Valid key
        config = IndexerConfig(openai_api_key="sk-valid123")
        assert config.openai_api_key == "sk-valid123"

        # Empty key should be allowed (for testing)
        config = IndexerConfig(openai_api_key="")
        assert config.openai_api_key == ""

        # Any format is currently accepted
        config = IndexerConfig(openai_api_key="invalid-key")
        assert config.openai_api_key == "invalid-key"

    def test_qdrant_url_setting(self):
        """Test Qdrant URL setting (no validation in current implementation)."""
        # Standard URLs
        config = IndexerConfig(qdrant_url="http://localhost:6333")
        assert config.qdrant_url == "http://localhost:6333"

        config = IndexerConfig(qdrant_url="https://cloud.qdrant.io")
        assert config.qdrant_url == "https://cloud.qdrant.io"

        # Any URL format is currently accepted
        config = IndexerConfig(qdrant_url="invalid-url")
        assert config.qdrant_url == "invalid-url"

    def test_numeric_constraints(self):
        """Test numeric field constraints."""
        # Valid values
        config = IndexerConfig(
            debounce_seconds=1.5,
            max_file_size=2048,
            batch_size=25,
            max_concurrent_files=5,
        )
        assert config.debounce_seconds == 1.5
        assert config.max_file_size == 2048
        assert config.batch_size == 25
        assert config.max_concurrent_files == 5

        # Test constraint validation
        with pytest.raises(ValidationError):
            IndexerConfig(debounce_seconds=0.05)  # Too small

        with pytest.raises(ValidationError):
            IndexerConfig(debounce_seconds=35.0)  # Too large

        with pytest.raises(ValidationError):
            IndexerConfig(max_file_size=500)  # Too small

        with pytest.raises(ValidationError):
            IndexerConfig(batch_size=0)  # Too small

        with pytest.raises(ValidationError):
            IndexerConfig(batch_size=1500)  # Too large


class TestLegacySettingsLoader:
    """Test the legacy settings.txt loader."""

    def test_load_nonexistent_file(self, tmp_path):
        """Test loading from a non-existent file."""
        nonexistent_file = tmp_path / "nonexistent.txt"
        settings = load_legacy_settings(nonexistent_file)
        assert settings == {}

    def test_load_empty_file(self, tmp_path):
        """Test loading from an empty file."""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        settings = load_legacy_settings(empty_file)
        assert settings == {}

    def test_load_basic_settings(self, tmp_path):
        """Test loading basic key-value pairs."""
        settings_file = tmp_path / "settings.txt"
        settings_content = """openai_api_key=sk-test123
qdrant_url=http://localhost:6333
indexer_verbose=true
"""
        settings_file.write_text(settings_content)

        settings = load_legacy_settings(settings_file)

        assert settings["openai_api_key"] == "sk-test123"
        assert settings["qdrant_url"] == "http://localhost:6333"
        assert settings["indexer_verbose"] is True

    def test_load_with_comments_and_whitespace(self, tmp_path):
        """Test loading with comments and whitespace handling."""
        settings_file = tmp_path / "settings.txt"
        settings_content = """# This is a comment
 openai_api_key = sk-test123
# Another comment
qdrant_url=http://localhost:6333

# Empty lines should be ignored
indexer_debug=false
"""
        settings_file.write_text(settings_content)

        settings = load_legacy_settings(settings_file)

        assert settings["openai_api_key"] == "sk-test123"
        assert settings["qdrant_url"] == "http://localhost:6333"
        assert settings["indexer_debug"] is False
        assert len(settings) == 3  # No comment lines included

    def test_type_conversion(self, tmp_path):
        """Test automatic type conversion."""
        settings_file = tmp_path / "settings.txt"
        settings_content = """# Boolean values
indexer_debug=true
indexer_verbose=false
# Integer values
batch_size=100
max_concurrent_files=20
# Float values
debounce_seconds=3.5
# String values
qdrant_url=http://localhost:6333
"""
        settings_file.write_text(settings_content)

        settings = load_legacy_settings(settings_file)

        # Boolean conversion
        assert settings["indexer_debug"] is True
        assert settings["indexer_verbose"] is False

        # Integer conversion
        assert settings["batch_size"] == 100
        assert settings["max_concurrent_files"] == 20

        # Float conversion
        assert settings["debounce_seconds"] == 3.5

        # String (no conversion)
        assert settings["qdrant_url"] == "http://localhost:6333"

    def test_malformed_lines_ignored(self, tmp_path):
        """Test that malformed lines are ignored gracefully."""
        settings_file = tmp_path / "settings.txt"
        settings_content = """valid_key=valid_value
malformed line without equals
=empty_key
valid_key2=valid_value2
key_without_value=
"""
        settings_file.write_text(settings_content)

        settings = load_legacy_settings(settings_file)

        assert settings["valid_key"] == "valid_value"
        assert settings["valid_key2"] == "valid_value2"
        assert settings["key_without_value"] == ""
        # Malformed lines should be ignored
        assert "malformed line without equals" not in settings
        assert "" not in settings  # Empty key

    def test_file_read_error_handling(self, tmp_path, monkeypatch):
        """Test handling of file read errors."""
        settings_file = tmp_path / "settings.txt"
        settings_file.write_text("key=value")

        # Mock file reading to raise an exception
        def mock_open(*args, **kwargs):
            raise PermissionError("Permission denied")

        monkeypatch.setattr("builtins.open", mock_open)

        # Should return empty dict and not raise
        settings = load_legacy_settings(settings_file)
        assert settings == {}


class TestConfigLoader:
    """Test the main config loading function."""

    def test_load_config_defaults_only(self):
        """Test loading config with only default values."""
        config = load_config(settings_file=Path("/nonexistent/path"))

        assert isinstance(config, IndexerConfig)
        assert config.openai_api_key == ""
        assert config.qdrant_url == "http://localhost:6333"

    def test_load_config_with_legacy_file(self, tmp_path):
        """Test loading config with legacy settings file."""
        settings_file = tmp_path / "settings.txt"
        settings_content = """openai_api_key=sk-legacy123
qdrant_url=https://legacy.qdrant.com
batch_size=75
"""
        settings_file.write_text(settings_content)

        config = load_config(settings_file=settings_file)

        assert config.openai_api_key == "sk-legacy123"
        assert config.qdrant_url == "https://legacy.qdrant.com"
        assert config.batch_size == 75

    def test_load_config_with_overrides(self, tmp_path):
        """Test that explicit overrides take precedence."""
        settings_file = tmp_path / "settings.txt"
        settings_content = """openai_api_key=sk-file123
qdrant_url=https://file.qdrant.com
"""
        settings_file.write_text(settings_content)

        config = load_config(
            settings_file=settings_file,
            openai_api_key="sk-override123",
            indexer_debug=True,
        )

        # Override takes precedence
        assert config.openai_api_key == "sk-override123"
        assert config.indexer_debug is True

        # File value used where no override
        assert config.qdrant_url == "https://file.qdrant.com"

    def test_load_config_validation_fallback(self, tmp_path):
        """Test fallback to defaults when validation fails."""
        settings_file = tmp_path / "settings.txt"
        settings_content = """openai_api_key=valid-key
debounce_seconds=invalid-number
max_file_size=not-a-number
"""
        settings_file.write_text(settings_content)

        # Should fall back to defaults (with overrides) when validation fails
        config = load_config(
            settings_file=settings_file, openai_api_key="sk-override123"
        )

        assert config.openai_api_key == "sk-override123"
        # The invalid values should be filtered out during loading and defaults used
        assert config.debounce_seconds == 2.0  # Default
        assert config.max_file_size == 1048576  # Default

    def test_load_config_environment_precedence(self, tmp_path, monkeypatch):
        """Test that environment variables have precedence over file."""
        settings_file = tmp_path / "settings.txt"
        settings_content = """openai_api_key=sk-file123
qdrant_url=https://file.qdrant.com
"""
        settings_file.write_text(settings_content)

        monkeypatch.setenv("QDRANT_URL", "https://env.qdrant.com")

        config = load_config(settings_file=settings_file)

        # Environment variable should win
        assert config.qdrant_url == "https://env.qdrant.com"
        # File value used where no env var
        assert config.openai_api_key == "sk-file123"


class TestCreateDefaultSettingsFile:
    """Test the default settings file creation function."""

    def test_create_default_file(self, tmp_path):
        """Test creating a default settings file."""
        settings_file = tmp_path / "new_settings.txt"

        create_default_settings_file(settings_file)

        assert settings_file.exists()
        content = settings_file.read_text()

        # Check that key sections are present
        assert "# Claude Indexer Configuration" in content
        assert "openai_api_key=" in content
        assert "qdrant_api_key=" in content
        assert "qdrant_url=http://localhost:6333" in content
        assert "indexer_debug=false" in content
        assert "batch_size=50" in content

    def test_create_file_error_handling(self, tmp_path, monkeypatch):
        """Test handling of file creation errors."""
        settings_file = tmp_path / "readonly" / "settings.txt"

        # Mock open to raise an exception
        def mock_open(*args, **kwargs):
            raise PermissionError("Permission denied")

        monkeypatch.setattr("builtins.open", mock_open)

        # Should not raise, just print error
        create_default_settings_file(settings_file)

        # File should not exist due to mocked error
        assert not settings_file.exists()
