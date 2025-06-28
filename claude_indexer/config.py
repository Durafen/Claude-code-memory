"""Configuration management with validation and environment variable support."""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from .indexer_logging import get_logger

logger = get_logger()


class IndexerConfig(BaseModel):
    """Configuration model with validation."""
    
    # API Keys
    openai_api_key: str = Field(default="")
    voyage_api_key: str = Field(default="")
    qdrant_api_key: str = Field(default="default-key")
    
    # URLs and Endpoints
    qdrant_url: str = Field(default="http://localhost:6333")
    
    # Collection Management
    collection_name: str = Field(default="default")
    
    # Component Types
    embedder_type: str = Field(default="openai")
    embedding_provider: str = Field(default="openai")  # For compatibility
    voyage_model: str = Field(default="voyage-3-lite")
    storage_type: str = Field(default="qdrant")
    
    # Indexing Behavior
    indexer_debug: bool = Field(default=False)
    indexer_verbose: bool = Field(default=True)
    debounce_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    
    # Watcher Settings
    watch_debounce: float = Field(default=2.0, ge=0.1, le=30.0)
    include_patterns: list = Field(default_factory=lambda: ['*.py', '*.md'])
    exclude_patterns: list = Field(default_factory=lambda: [
        '*.pyc', '__pycache__', '.git', '.venv', 'node_modules'
    ])
    
    # File Processing
    include_markdown: bool = Field(default=True)
    include_tests: bool = Field(default=False)
    max_file_size: int = Field(default=1048576, ge=1024)  # 1MB default, min 1KB
    
    # Performance Settings
    batch_size: int = Field(default=50, ge=1, le=1000)
    max_concurrent_files: int = Field(default=10, ge=1, le=100)
    
    # State Management
    state_directory: Optional[Path] = Field(default=None)
    
    @classmethod
    def from_env(cls) -> 'IndexerConfig':
        """Create config with environment variable overrides."""
        return cls(
            openai_api_key=os.environ.get('OPENAI_API_KEY', ''),
            voyage_api_key=os.environ.get('VOYAGE_API_KEY', ''),
            qdrant_api_key=os.environ.get('QDRANT_API_KEY', 'default-key'),
            qdrant_url=os.environ.get('QDRANT_URL', 'http://localhost:6333'),
            embedding_provider=os.environ.get('EMBEDDING_PROVIDER', 'openai'),
            voyage_model=os.environ.get('VOYAGE_MODEL', 'voyage-3-lite'),
        )


def load_legacy_settings(settings_file: Path) -> Dict[str, Any]:
    """Load configuration from legacy settings.txt format."""
    settings = {}
    
    if not settings_file.exists():
        return settings
        
    try:
        with open(settings_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Skip empty keys
                    if not key:
                        continue
                    
                    # Convert boolean values
                    if value.lower() in ('true', 'false'):
                        value = value.lower() == 'true'
                    # Convert numeric values (more robust check)
                    elif value.replace('.', '', 1).replace('-', '', 1).isdigit():
                        try:
                            value = float(value) if '.' in value else int(value)
                        except ValueError:
                            # Keep as string if conversion fails
                            pass
                    
                    settings[key] = value
    except Exception as e:
        logger.warning(f"Failed to load settings.txt: {e}")
    
    return settings


def load_config(settings_file: Optional[Path] = None, **overrides) -> IndexerConfig:
    """Load configuration from multiple sources with precedence:
    1. Explicit overrides (highest priority)
    2. Environment variables  
    3. settings.txt file
    4. Default values (lowest priority)
    """
    # Default settings file location
    if settings_file is None:
        settings_file = Path(__file__).parent.parent / "settings.txt"
    
    # Load legacy settings if available
    legacy_settings = load_legacy_settings(settings_file)
    
    # Start with legacy file settings
    config_dict = legacy_settings.copy()
    
    # Override with environment variables (higher priority than file)
    env_settings = {
        'openai_api_key': os.environ.get('OPENAI_API_KEY'),
        'voyage_api_key': os.environ.get('VOYAGE_API_KEY'),
        'qdrant_api_key': os.environ.get('QDRANT_API_KEY'),
        'qdrant_url': os.environ.get('QDRANT_URL'),
        'embedding_provider': os.environ.get('EMBEDDING_PROVIDER'),
        'voyage_model': os.environ.get('VOYAGE_MODEL'),
    }
    
    # Only override with env vars that are actually set
    for key, value in env_settings.items():
        if value is not None:
            config_dict[key] = value
    
    # Apply explicit overrides (highest priority)
    config_dict.update(overrides)
    
    # Create config with merged settings, filtering out invalid values
    try:
        config = IndexerConfig(**config_dict)
    except Exception as e:
        logger.warning(f"Configuration validation failed: {e}")
        # Fall back to defaults, then apply valid overrides
        config = IndexerConfig()
        
        # Apply valid overrides one by one
        for key, value in overrides.items():
            if hasattr(config, key):
                try:
                    # Test if this override would be valid
                    test_config = IndexerConfig(**{key: value})
                    setattr(config, key, value)
                except:
                    logger.warning(f"Ignoring invalid override {key}={value}")
    
    return config


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
        with open(path, 'w') as f:
            f.write(template)
        logger.info(f"Created default settings file: {path}")
    except Exception as e:
        logger.error(f"Failed to create settings file: {e}")