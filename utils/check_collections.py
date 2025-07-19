#!/usr/bin/env python3
"""Check what collections exist and cleanup logic."""

from qdrant_client import QdrantClient

from claude_indexer.config import load_config

config = load_config()
client = QdrantClient(
    url=config.qdrant_url,
    api_key=config.qdrant_api_key if config.qdrant_api_key != "default-key" else None,
)

collections = client.get_collections().collections
print("Current collections:")
for col in collections:
    print(f"  {col.name} (length: {len(col.name)})")

    # Check cleanup criteria from conftest.py
    has_numbers = any(char.isdigit() for char in col.name)
    is_long_test = col.name.startswith("test_") and len(col.name) > 20
    matches_cleanup = "test" in col.name.lower() and (has_numbers or is_long_test)
    print(
        f"    would be cleaned up: {matches_cleanup} (has_numbers: {has_numbers}, is_long_test: {is_long_test})"
    )

    # Check point count if it contains test data
    if "test" in col.name.lower():
        try:
            count = client.count(col.name).count
            print(f"    points: {count}")
        except Exception as e:
            print(f"    points: error ({e})")
