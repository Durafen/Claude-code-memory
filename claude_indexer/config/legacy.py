"""Legacy settings.txt parsing for backward compatibility."""

from pathlib import Path
from typing import Any

from ..indexer_logging import get_logger

logger = get_logger()


def load_legacy_settings(settings_file: Path) -> dict[str, Any]:
    """Load configuration from legacy settings.txt format."""
    settings: dict[str, Any] = {}

    # Key mapping from uppercase settings.txt format to lowercase config fields
    key_mapping = {
        "VOYAGE_API_KEY": "voyage_api_key",
        "EMBEDDING_PROVIDER": "embedding_provider",
        "EMBEDDING_MODEL": "voyage_model",  # Map EMBEDDING_MODEL to voyage_model for voyage provider
        "OPENAI_API_KEY": "openai_api_key",
        "QDRANT_API_KEY": "qdrant_api_key",
        "QDRANT_URL": "qdrant_url",
        "CHAT_MODEL": "chat_model",
        "VOYAGE_MODEL": "voyage_model",
        "cleanup_interval_minutes": "cleanup_interval_minutes",
    }

    if not settings_file.exists():
        return settings

    try:
        with open(settings_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, raw_value = line.split("=", 1)
                    key = key.strip()
                    value: Any = raw_value.strip()

                    # Skip empty keys
                    if not key:
                        continue

                    # Convert boolean values
                    if value.lower() in ("true", "false"):
                        value = value.lower() == "true"
                    # Convert numeric values (more robust check)
                    elif value.replace(".", "", 1).replace("-", "", 1).isdigit():
                        try:
                            value = float(raw_value) if "." in raw_value else int(raw_value)
                        except ValueError:
                            # Keep as string if conversion fails
                            pass

                    # Map uppercase keys to lowercase field names
                    mapped_key = key_mapping.get(key, key)
                    settings[mapped_key] = value
    except Exception as e:
        logger.warning(f"Failed to load settings.txt: {e}")

    return settings


def create_default_settings_file(path: Path) -> None:
    """Create a default settings.txt file template."""
    template = """# Claude Indexer Configuration
# Lines starting with # are comments

# Embedding Provider Configuration
embedding_provider=openai  # Options: openai, voyage

# API Configuration
openai_api_key=your-openai-api-key-here
voyage_api_key=your-voyage-api-key-here
qdrant_api_key=your-qdrant-api-key
qdrant_url=http://localhost:6333

# Voyage Settings (only used if embedding_provider=voyage)
voyage_model=voyage-3-lite  # Options: voyage-3, voyage-3-lite, voyage-code-3

# Indexing Behavior
indexer_debug=false
indexer_verbose=true
debounce_seconds=2.0

# File Processing
include_markdown=true
include_tests=false
max_file_size=1048576

# Performance Settings
batch_size=50
max_concurrent_files=10
"""

    try:
        with open(path, "w") as f:
            f.write(template)
        logger.info(f"Created default settings file: {path}")
    except Exception as e:
        logger.error(f"Failed to create settings file: {e}")
