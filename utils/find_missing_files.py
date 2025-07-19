#!/usr/bin/env python3
"""
Find missing files between Qdrant collection and state files.

This script compares files tracked in Qdrant vs state files to identify
the exact files that are missing/out of sync.
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path

# Add parent directory to path to find claude_indexer
sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_indexer.storage.qdrant import QdrantStore


def load_settings(settings_path: str = "settings.txt") -> dict[str, str]:
    """Load settings from the project's settings.txt file."""
    settings = {}
    if not os.path.exists(settings_path):
        raise FileNotFoundError(f"Settings file not found: {settings_path}")

    with open(settings_path) as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.strip().split("=", 1)
                settings[key.strip()] = value.strip()
    return settings


def get_qdrant_files(
    collection_name: str, qdrant_store: QdrantStore, project_root: str
) -> set[str]:
    """Get all file entities from Qdrant collection."""
    print(f"Getting files from Qdrant collection '{collection_name}'...")

    try:
        # Use scroll to get ALL points with payloads
        all_points = []

        scroll_result = qdrant_store.client.scroll(
            collection_name=collection_name,
            limit=1000,
            with_payload=True,
            with_vectors=False,
        )

        points, next_page_offset = scroll_result
        all_points.extend(points)

        # Continue scrolling if there are more points
        while next_page_offset:
            scroll_result = qdrant_store.client.scroll(
                collection_name=collection_name,
                limit=1000,
                offset=next_page_offset,
                with_payload=True,
                with_vectors=False,
            )
            points, next_page_offset = scroll_result
            all_points.extend(points)

        print(f"Found {len(all_points)} total entries in Qdrant")

        # Extract file entities only and normalize paths
        files = set()
        entity_stats: Counter[str] = Counter()

        for point in all_points:
            if hasattr(point, "payload") and point.payload:
                # Handle both old and new chunk formats
                entity_type = point.payload.get("entity_type", "unknown")
                entity_stats[entity_type] += 1

                if entity_type == "file":
                    # Get file path from either 'name' or 'file_path' field
                    file_path = point.payload.get("name", "") or point.payload.get(
                        "file_path", ""
                    )
                    if file_path:
                        # Normalize to relative path from project root
                        relative_path = normalize_path_to_relative(
                            file_path, project_root
                        )
                        files.add(relative_path)

        print(f"Entity breakdown: {dict(entity_stats)}")
        print(f"Found {len(files)} file entities in Qdrant")

        return files

    except Exception as e:
        print(f"Error getting files from Qdrant: {e}")
        return set()


def normalize_path_to_relative(file_path: str, project_root: str) -> str:
    """Convert absolute path to relative path from project root."""
    project_root_path = Path(project_root).resolve()
    file_path_obj = Path(file_path)

    try:
        # If it's already a relative path, return as is
        if not file_path_obj.is_absolute():
            return file_path

        # Convert absolute path to relative from project root
        relative = file_path_obj.relative_to(project_root_path)
        return str(relative)
    except ValueError:
        # If the file is outside project root, return the absolute path
        return file_path


def get_state_files(collection_name: str) -> set[str]:
    """Get all tracked files from indexer state files."""
    print(f"Getting files from state files for '{collection_name}'...")

    tracked_files = set()

    # Project root is parent of utils directory (where this script is located)
    project_root = Path(__file__).parent.parent

    # Check project-local state directory
    project_state_dir = project_root / ".claude-indexer"
    if project_state_dir.exists():
        # Look for state files with collection name
        state_files = list(project_state_dir.glob(f"{collection_name}.json"))
        print(
            f"Found {len(state_files)} state file(s): {[f.name for f in state_files]}"
        )

        for state_file in state_files:
            try:
                with open(state_file) as f:
                    state_data = json.load(f)

                # Handle both state file formats
                if "files" in state_data:
                    # New format with 'files' key
                    file_entries = state_data["files"]
                else:
                    # Old format with files as direct keys (exclude metadata keys that start with '_')
                    file_entries = {
                        k: v for k, v in state_data.items() if not k.startswith("_")
                    }
                tracked_files.update(file_entries.keys())
                print(f"Loaded {len(file_entries)} files from {state_file.name}")

            except Exception as e:
                print(f"Error reading {state_file}: {e}")
                continue
    print(f"Total tracked files in state: {len(tracked_files)}")
    return tracked_files


def compare_file_sets(
    qdrant_files: set[str], state_files: set[str]
) -> dict[str, list[str]]:
    """Compare the two file sets and return differences."""

    # Files in Qdrant but not in state (the missing 6 files we're looking for)
    in_qdrant_not_state = qdrant_files - state_files

    # Files in state but not in Qdrant (should be minimal)
    in_state_not_qdrant = state_files - qdrant_files

    # Files in both (should be most files)
    in_both = qdrant_files & state_files

    return {
        "in_qdrant_not_state": sorted(list(in_qdrant_not_state)),
        "in_state_not_qdrant": sorted(list(in_state_not_qdrant)),
        "in_both": sorted(list(in_both)),
    }


def print_comparison_results(comparison: dict[str, list[str]]) -> None:
    """Print detailed comparison results."""

    print("\n" + "=" * 80)
    print("📊 FILE COMPARISON RESULTS")
    print("=" * 80)

    in_both = comparison["in_both"]
    in_qdrant_not_state = comparison["in_qdrant_not_state"]
    in_state_not_qdrant = comparison["in_state_not_qdrant"]

    print(f"📄 Files in both Qdrant and state: {len(in_both)}")
    print(f"🔍 Files in Qdrant but NOT in state: {len(in_qdrant_not_state)}")
    print(f"📝 Files in state but NOT in Qdrant: {len(in_state_not_qdrant)}")

    if in_qdrant_not_state:
        print(
            f"\n🎯 THE MISSING {len(in_qdrant_not_state)} FILES (in Qdrant but not tracked in state):"
        )
        print("-" * 60)
        for i, file_path in enumerate(in_qdrant_not_state, 1):
            print(f"{i:2d}. {file_path}")

        # Analyze the missing files
        print("\n📊 ANALYSIS OF MISSING FILES:")
        extensions: Counter[str] = Counter()
        directories: Counter[str] = Counter()

        for file_path in in_qdrant_not_state:
            # Get extension
            ext = Path(file_path).suffix.lower()
            extensions[ext if ext else "no_extension"] += 1

            # Get directory
            dir_name = str(Path(file_path).parent)
            directories[dir_name] += 1

        print(f"Extensions: {dict(extensions)}")
        print(f"Directories: {dict(directories)}")

    if in_state_not_qdrant:
        print(f"\n⚠️  FILES IN STATE BUT NOT IN QDRANT ({len(in_state_not_qdrant)}):")
        print("-" * 60)
        for i, file_path in enumerate(in_state_not_qdrant, 1):
            print(f"{i:2d}. {file_path}")


def main() -> int:
    """Main function to find missing files."""
    collection_name = "claude-memory-test"

    try:
        # Load settings
        settings = load_settings()

        # Create Qdrant store
        qdrant_store = QdrantStore(
            url=settings.get("qdrant_url", "http://localhost:6333"),
            api_key=settings.get("qdrant_api_key") or "",
        )

        print(f"🔍 FINDING MISSING FILES FOR COLLECTION: {collection_name}")
        print("=" * 80)

        # Project root is parent of utils directory (where this script is located)
        project_root = str(Path(__file__).parent.parent.resolve())
        print(f"Project root: {project_root}")

        # Get files from both sources
        qdrant_files = get_qdrant_files(collection_name, qdrant_store, project_root)
        state_files = get_state_files(collection_name)

        print("\n📊 SUMMARY:")
        print(f"Qdrant files: {len(qdrant_files)}")
        print(f"State files:  {len(state_files)}")
        print(f"Difference:   {len(qdrant_files) - len(state_files)}")

        # Compare the sets
        comparison = compare_file_sets(qdrant_files, state_files)

        # Print results
        print_comparison_results(comparison)

        # Save detailed results to file
        output_file = "missing_files_analysis.json"
        with open(output_file, "w") as f:
            json.dump(
                {
                    "collection_name": collection_name,
                    "qdrant_file_count": len(qdrant_files),
                    "state_file_count": len(state_files),
                    "qdrant_files": sorted(list(qdrant_files)),
                    "state_files": sorted(list(state_files)),
                    "comparison": comparison,
                },
                f,
                indent=2,
            )

        print(f"\n💾 Detailed analysis saved to: {output_file}")

        return 0

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
