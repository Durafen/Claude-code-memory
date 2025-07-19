#!/usr/bin/env python3
"""
Find manual entries with [ ] checkbox format using cleanup pipeline logic.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


# Correct manual entry detection from working backup system
def is_manual_entry(payload: dict[str, Any]) -> bool:
    """Enhanced logic for v2.4 chunk format - matches manual_memory_backup.py exactly."""
    # Pattern 1: Auto entities have file_path field
    if "file_path" in payload:
        return False

    # Pattern 2: Auto relations have entity_name/relation_target/relation_type structure
    if all(
        field in payload
        for field in ["entity_name", "relation_target", "relation_type"]
    ):
        return False

    # Pattern 3: Auto entities have extended metadata fields
    automation_fields = {
        "line_number",
        "ast_data",
        "signature",
        "docstring",
        "full_name",
        "ast_type",
        "start_line",
        "end_line",
        "source_hash",
        "parsed_at",
    }
    if any(field in payload for field in automation_fields):
        return False

    # True manual entries have minimal fields: entity_name, entity_type
    has_name = "entity_name" in payload
    has_type = "entity_type" in payload

    if not (has_name and has_type):
        return False

    # Additional v2.4 validation - manual entries shouldn't have certain auto fields
    auto_only_fields = {"file_hash", "parser_version", "indexed_at"}
    if any(field in payload for field in auto_only_fields):
        return False

    return True


from claude_indexer.config.config_loader import ConfigLoader


async def find_all_manual_entries(collection_name: str) -> list[dict[str, Any]]:
    """Find ALL manual entries (excluding documentation) to format with [ ]."""

    # Load configuration
    config = ConfigLoader().load()

    # Connect to Qdrant
    client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)

    manual_entries = []

    # Scroll through all entries in collection
    offset = None
    while True:
        try:
            result = client.scroll(
                collection_name=collection_name,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )

            points, next_offset = result

            if not points:
                break

            for point in points:
                payload = point.payload or {}

                # Use cleanup pipeline logic to detect manual entries
                if is_manual_entry(payload):
                    # Exclude documentation types (as specified by user)
                    entity_type = payload.get("entity_type") or payload.get(
                        "entityType", ""
                    )
                    if entity_type == "documentation":
                        continue

                    # Get content from either field
                    content = payload.get("content", "")
                    observations = payload.get("observations", [])

                    # Use content if available, otherwise first observation
                    entry_content = content
                    if not entry_content and observations:
                        entry_content = (
                            observations[0]
                            if isinstance(observations[0], str)
                            else str(observations[0])
                        )

                    manual_entries.append(
                        {
                            "id": point.id,
                            "title": payload.get("entity_name")
                            or payload.get("name", "Untitled"),
                            "entity_type": entity_type,
                            "content": entry_content,
                        }
                    )

            offset = next_offset
            if offset is None:
                break

        except Exception as e:
            print(f"Error scrolling collection: {e}")
            break

    return manual_entries


def write_memories_md(
    entries: list[dict[str, Any]],
    collection_name: str,
    output_file: str = "memories.md",
    full_content: bool = False,
):
    """Write all manual entries to memories.md with [ ] checkbox format."""

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# Manual Entries Task List - {collection_name}\n\n")
        f.write(
            f"All {len(entries)} manual entries (excluding documentation) formatted as task list\n\n"
        )
        f.write(
            "Generated using cleanup pipeline logic (`is_manual_entry()` detection)\n\n"
        )
        if full_content:
            f.write("**Full content mode enabled**\n\n")
        else:
            f.write("**Names only mode** (use --full for complete content)\n\n")
        f.write("---\n\n")

        if not entries:
            f.write("No manual entries found.\n")
            return

        # Group by entity type
        by_type = {}
        for entry in entries:
            entity_type = entry["entity_type"]
            if entity_type not in by_type:
                by_type[entity_type] = []
            by_type[entity_type].append(entry)

        # Write entries grouped by type with [ ] checkboxes
        for entity_type, type_entries in sorted(by_type.items()):
            f.write(f"## {entity_type.replace('_', ' ').title()}\n\n")

            for entry in type_entries:
                # Add [ ] checkbox format at start with category
                f.write(
                    f"[ ] **{entry['title']}** ({entry['entity_type']}) (ID: `{entry['id']}`)\n"
                )

                # Add content only if full_content is True
                if full_content:
                    content = entry["content"]
                    if content:
                        f.write("\n")
                        # Indent content under the checkbox
                        indented_content = "\n".join(
                            f"    {line}" for line in content.split("\n")
                        )
                        f.write(f"{indented_content}\n\n")
                        f.write("---\n\n")
                    else:
                        f.write("\n")
                else:
                    f.write("\n")


async def main():
    """Main function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate memories.md with all manual entries in checkbox format"
    )
    parser.add_argument(
        "-c", "--collection", required=True, help="Collection name to process"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="memories.md",
        help="Output file (default: memories.md)",
    )
    parser.add_argument(
        "--full", action="store_true", help="Include full content (default: names only)"
    )

    args = parser.parse_args()
    collection_name = args.collection

    print(
        f"🔍 Finding ALL manual entries (excluding documentation) in collection: {collection_name}"
    )
    print("Using cleanup pipeline detection logic...")
    if args.full:
        print("📝 Full content mode enabled")
    else:
        print("📝 Names only mode (use --full for complete content)")

    try:
        entries = await find_all_manual_entries(collection_name)

        print(f"✅ Found {len(entries)} manual entries")

        output_file = args.output
        write_memories_md(entries, collection_name, output_file, args.full)

        print(
            f"📝 All manual entries written to: {output_file} with [ ] checkbox format"
        )

        # Summary by type
        if entries:
            by_type = {}
            for entry in entries:
                entity_type = entry["entity_type"]
                by_type[entity_type] = by_type.get(entity_type, 0) + 1

            print("\n📊 Breakdown by type:")
            for entity_type, count in sorted(by_type.items()):
                print(f"  - {entity_type}: {count}")

    except Exception as e:
        print(f"❌ Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
