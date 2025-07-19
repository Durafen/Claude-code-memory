#!/usr/bin/env python3
"""
Generic backup and restore utility for manual entries from any Qdrant collection.
Creates JSON backups of manual entries and can restore them to any collection.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_indexer.config import load_config
from claude_indexer.embeddings.openai import OpenAIEmbedder
from claude_indexer.embeddings.voyage import VoyageEmbedder
from claude_indexer.indexer_logging import setup_logging
from claude_indexer.storage.qdrant import QdrantStore


def get_manual_entity_types() -> set[str]:
    """Define manual entity types based on common patterns."""
    return {
        # Original manual types
        "optimization_pattern",
        "milestone",
        "solution",
        "bug",
        "performance-metric",
        "feature-verification",
        "task-completion",
        "technical-analysis",
        "debugging-analysis",
        "project_milestone",
        "verification_report",
        "system_validation",
        "completed_optimization",
        "performance_improvement",
        "refactoring_project",
        "design_patterns",
        "architecture_pattern",
        "verification_plan",
        "checklist",
        "verification_result",
        "technical_pattern",
        "solution_pattern",
        "test",
        "analysis-report",
        "code-pattern",
        "code-analysis",
        "infrastructure-analysis",
        "critical-bug",
        "debugging-report",
        "bug-reproduction",
        "bug-analysis",
        "workflow_pattern",
        "configuration_pattern",
        "best_practice",
        "implementation_note",
        "decision_record",
        "learning",
        "insight",
        # GitHub-utils specific manual types (from memory search analysis)
        "code_analysis",
        "debugging_solution",
        "project_architecture",
        "reference",
        "research_summary",
        "section",
        # Additional documentation and content types
        "documentation",
        "manual_test",
        "user_note",
        "comment",
        "annotation",
        "summary",
        "guide",
        "tutorial",
        "example",
        "template",
        "specification",
        "requirement",
        "design_document",
        "meeting_notes",
        "decision",
        "changelog",
        "release_notes",
        "troubleshooting",
        "faq",
        "howto",
        "tips",
        "tricks",
        # New category types
        "active_issue",
        "ideas",
    }


def get_code_entity_types() -> set[str]:
    """Define code-indexed entity types to exclude."""
    return {"file", "function", "class", "variable", "import", "directory", "project"}


def is_truly_manual_entry(payload: dict[str, Any]) -> bool:
    """
    Enhanced logic for v2.4 chunk format.
    Uses the same detection logic as qdrant_stats.py for consistency.
    """
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
        # Removed 'has_implementation' - manual entries can have this in v2.4 format
        # Removed 'collection' - manual docs can have collection field
    }
    if any(field in payload for field in automation_fields):
        return False

    # v2.4 specific: Don't reject based on chunk_type alone
    # Both manual and auto entries can have chunk_type in v2.4
    # Manual entries from MCP also get type='chunk' + chunk_type='metadata'

    # True manual entries have minimal fields: entity_name, entity_type, observations
    # v2.4 format only
    has_name = "entity_name" in payload
    has_type = "entity_type" in payload

    if not (has_name and has_type):
        return False

    # Additional check: Manual entries typically have meaningful content
    # Check for observations or content (v2.4 MCP format)
    observations = payload.get("observations", [])
    content = payload.get("content", "")

    has_meaningful_content = (
        observations and isinstance(observations, list) and len(observations) > 0
    ) or (content and isinstance(content, str) and len(content.strip()) > 0)

    if not has_meaningful_content:
        return False

    return True


def backup_manual_entries(collection_name: str, output_file: str = None):
    """Extract manual entries from any Qdrant collection and save to backup file."""

    print(f"🔍 Backing up manual entries from '{collection_name}' collection...")

    try:
        # Load configuration
        config = load_config()

        # Initialize Qdrant store
        store = QdrantStore(url=config.qdrant_url, api_key=config.qdrant_api_key)

        # Get all points from collection
        print(f"📥 Retrieving all points from {collection_name}...")
        all_points = []

        # Use scroll to get all points
        scroll_result = store.client.scroll(
            collection_name=collection_name,
            limit=1000,  # Get in batches
            with_payload=True,
            with_vectors=False,  # We don't need vectors for backup
        )

        points, next_page_offset = scroll_result
        all_points.extend(points)

        # Continue scrolling if there are more points
        while next_page_offset:
            scroll_result = store.client.scroll(
                collection_name=collection_name,
                limit=1000,
                offset=next_page_offset,
                with_payload=True,
                with_vectors=False,
            )
            points, next_page_offset = scroll_result
            all_points.extend(points)

        print(f"📊 Found {len(all_points)} total points")

        # Get entity type definitions
        manual_entity_types = get_manual_entity_types()
        code_types = get_code_entity_types()

        # Filter manual entries with improved classification
        manual_entries = []
        code_entries = []
        relation_entries = []  # Track relations separately
        unknown_entries = []

        for point in all_points:
            payload = point.payload or {}

            # Check for relations first (v2.4 format only)
            if (
                "entity_name" in payload
                and "relation_target" in payload
                and "relation_type" in payload
            ):
                point_type = payload.get("type", "relation")
                chunk_type = payload.get("chunk_type", "relation")
                relation_entries.append(
                    {
                        "id": str(point.id),
                        "type": point_type,
                        "chunk_type": chunk_type if point_type == "chunk" else None,
                        "from": payload.get("entity_name", "unknown"),
                        "to": payload.get("relation_target", "unknown"),
                        "relationType": payload.get("relation_type", "unknown"),
                    }
                )

            # Check for manual entries (using same logic as qdrant_stats)
            elif is_truly_manual_entry(payload):
                manual_entries.append({"id": str(point.id), "payload": payload})

            # Everything else is auto-indexed
            else:
                # v2.4 format only
                entity_type = payload.get("entity_type", "unknown")
                entity_name = payload.get("entity_name", "unknown")
                code_entries.append(
                    {
                        "id": str(point.id),
                        "entity_type": entity_type,
                        "name": entity_name,
                    }
                )

        # Filter relations to only those connected to manual entries
        # v2.4 format only
        manual_entity_names = set()
        for entry in manual_entries:
            payload = entry["payload"]
            entity_name = payload.get("entity_name", "")
            if entity_name:
                manual_entity_names.add(entity_name)

        relevant_relations = []

        for relation in relation_entries:
            from_entity = relation.get(
                "from", ""
            )  # These use 'from'/'to' keys as stored above
            to_entity = relation.get("to", "")

            # Keep relation if either end connects to a manual entry
            if from_entity in manual_entity_names or to_entity in manual_entity_names:
                relevant_relations.append(relation)

        # Print statistics
        print(f"📝 Manual entries: {len(manual_entries)}")
        print(f"🤖 Code entries: {len(code_entries)}")
        print(f"🔗 All relations: {len(relation_entries)}")
        print(f"🎯 Relevant relations (connected to manual): {len(relevant_relations)}")
        print(f"❓ Unknown entries: {len(unknown_entries)}")

        if unknown_entries:
            unknown_types = set(e["entity_type"] for e in unknown_entries)
            print(f"❓ Unknown entity types found: {sorted(unknown_types)}")

        # Create backup data
        backup_data = {
            "collection_name": collection_name,
            "backup_timestamp": datetime.now().isoformat(),
            "total_points": len(all_points),
            "manual_entries_count": len(manual_entries),
            "code_entries_count": len(code_entries),
            "relation_entries_count": len(relation_entries),
            "unknown_entries_count": len(unknown_entries),
            "manual_entity_types": sorted(list(manual_entity_types)),
            "code_entity_types": sorted(list(code_types)),
            "manual_entries": manual_entries,
            "relation_entries": relevant_relations,  # Only relations connected to manual entries
            "unknown_entries": unknown_entries,  # Include for review
        }

        # Save to file with timestamp if no output specified
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"manual_entries_backup_{collection_name}_{timestamp}.json"

        # Ensure backups directory exists and save there
        backups_dir = Path("backups")
        backups_dir.mkdir(exist_ok=True)
        backup_file = backups_dir / output_file
        with open(backup_file, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)

        print(f"✅ Manual entries backup saved to: {backup_file}")
        print(f"💾 Backup contains {len(manual_entries)} manual entries")

        return backup_file, len(manual_entries)

    except Exception as e:
        print(f"❌ Error during backup: {e}")
        raise


def restore_manual_entries(
    backup_file: str,
    collection_name: str = None,
    batch_size: int = 10,
    dry_run: bool = False,
    force_duplicates: bool = False,
):
    """Directly restore manual entries to Qdrant with proper vectorization.

    This function bypasses MCP and directly inserts entities into Qdrant with embeddings.
    """
    backup_path = Path(backup_file)

    if not backup_path.exists():
        print(f"❌ Backup file not found: {backup_path}")
        return False

    # Load the backup data
    try:
        with open(backup_path) as f:
            backup_data = json.load(f)
    except Exception as e:
        print(f"❌ Error reading backup file: {e}")
        return False

    # Extract collection info and manual entries
    original_collection = backup_data.get("collection_name", "unknown")
    target_collection = collection_name or original_collection
    manual_entries = backup_data.get("manual_entries", [])
    backup_timestamp = backup_data.get("backup_timestamp", "unknown")

    if not manual_entries:
        print("📭 No manual entries found in backup file")
        return True

    print(f"🔍 Direct Qdrant restore from: {backup_path}")
    print(f"📅 Backup timestamp: {backup_timestamp}")
    print(f"📦 Original collection: {original_collection}")
    print(f"🎯 Target collection: {target_collection}")
    print(f"📋 Found {len(manual_entries)} manual entries to restore")

    if dry_run:
        print("🔸 DRY RUN - No actual changes will be made")
        print("\nWould restore the following entries:")
        for i, entry in enumerate(manual_entries[:5]):  # Show first 5
            payload = entry.get("payload", {})
            # Handle v2.4 format
            name = payload.get("entity_name", "unknown")
            entity_type = payload.get("entity_type", "unknown")
            print(f"  {i + 1}. {name} ({entity_type})")
        if len(manual_entries) > 5:
            print(f"  ... and {len(manual_entries) - 5} more entries")
        return True

    try:
        # Load configuration
        config = load_config()

        # Initialize components with proper provider (voyage or openai)
        if config.embedding_provider == "voyage":
            embedder = VoyageEmbedder(
                api_key=config.voyage_api_key, model=config.voyage_model
            )
        else:
            embedder = OpenAIEmbedder(api_key=config.openai_api_key)
        store = QdrantStore(url=config.qdrant_url, api_key=config.qdrant_api_key)

        # Ensure collection exists
        if not store.collection_exists(target_collection):
            print(f"📦 Creating collection: {target_collection}")
            # Get vector size from embedder
            if config.embedding_provider == "voyage":
                vector_size = 512  # Voyage AI dimension
            else:
                vector_size = 1536  # OpenAI dimension
            store.create_collection(
                collection_name=target_collection,
                vector_size=vector_size,
                distance_metric="cosine",
            )

        # Process in batches
        total_restored = 0
        total_skipped = 0
        failed_entries = []

        for batch_start in range(0, len(manual_entries), batch_size):
            batch_end = min(batch_start + batch_size, len(manual_entries))
            batch = manual_entries[batch_start:batch_end]
            batch_num = (batch_start // batch_size) + 1
            total_batches = (len(manual_entries) + batch_size - 1) // batch_size

            print(
                f"\n📦 Processing batch {batch_num}/{total_batches} ({len(batch)} entries)..."
            )

            # Create v2.4 format points directly (bypassing Entity objects)
            vector_points = []
            skipped_duplicates = 0

            for entry in batch:
                payload = entry.get("payload", {})
                entry_id = entry.get("id", "unknown")

                # Extract from v2.4 format and preserve as v2.4 manual format
                entity_name = payload.get("entity_name", f"restored_entry_{entry_id}")
                entity_type = payload.get("entity_type", "documentation")
                content = payload.get("content", "")

                # Use existing content for embedding
                if content:
                    content_for_embedding = content
                else:
                    content_for_embedding = f"{entity_type}: {entity_name}"

                # Generate embedding for this entry
                print(f"🔮 Generating embedding for: {entity_name[:50]}...")
                embedding_result = embedder.embed_text(content_for_embedding)

                if embedding_result.error:
                    failed_entries.append(
                        {
                            "name": entity_name,
                            "error": f"Embedding failed: {embedding_result.error}",
                        }
                    )
                    continue

                # Create proper v2.4 manual format payload with required chunk fields
                manual_payload = {
                    "type": "chunk",
                    "chunk_type": "metadata",
                    "entity_name": entity_name,
                    "entity_type": entity_type,
                    "content": content,
                    "has_implementation": False,
                    # No file_path or automation fields - this preserves manual classification
                }

                # Create deterministic ID for manual entry to prevent duplicates
                import hashlib

                from qdrant_client.models import PointStruct

                # Create deterministic ID: "manual::{entity_type}::{entity_name}::{content_hash}"
                content_hash = hashlib.sha256(
                    content_for_embedding.encode()
                ).hexdigest()[:16]
                deterministic_key = (
                    f"manual::{entity_type}::{entity_name}::{content_hash}"
                )
                deterministic_id = int(
                    hashlib.sha256(deterministic_key.encode()).hexdigest()[:8], 16
                )

                # Check if entry already exists using deterministic ID (unless forcing duplicates)
                if not force_duplicates:
                    try:
                        existing_point = store.client.retrieve(
                            collection_name=target_collection,
                            ids=[deterministic_id],
                            with_payload=True,
                        )
                        if existing_point and len(existing_point) > 0:
                            print(f"⏭️  Skipping duplicate: {entity_name}")
                            skipped_duplicates += 1
                            continue
                    except Exception:
                        # Point doesn't exist, proceed with creation
                        pass

                point = PointStruct(
                    id=deterministic_id,
                    vector=embedding_result.embedding,
                    payload=manual_payload,
                )
                vector_points.append(point)

            # Store in Qdrant
            if vector_points:
                print(f"💾 Storing {len(vector_points)} entities in Qdrant...")
                result = store.upsert_points(target_collection, vector_points)

                if result.success:
                    total_restored += len(vector_points)
                    total_skipped += skipped_duplicates
                    if skipped_duplicates > 0:
                        print(
                            f"✅ Batch {batch_num}: {len(vector_points)} new, {skipped_duplicates} skipped duplicates"
                        )
                    else:
                        print(
                            f"✅ Batch {batch_num} restored: {len(vector_points)} entities"
                        )
                else:
                    print(f"❌ Batch {batch_num} failed: {result.errors}")
                    for point in vector_points:
                        failed_entries.append(
                            {
                                "name": point.payload.get("entity_name", "unknown"),
                                "error": "Qdrant upsert failed",
                            }
                        )
            elif skipped_duplicates > 0:
                total_skipped += skipped_duplicates
                print(
                    f"⏭️  Batch {batch_num}: {skipped_duplicates} duplicates skipped, 0 new entries"
                )

            # Rate limiting pause between batches
            if batch_end < len(manual_entries):
                print("⏸️  Pausing 2 seconds for rate limiting...")
                time.sleep(2)

        # Final report
        print(f"\n{'=' * 60}")
        print("🎉 Direct restoration complete!")
        print(f"✅ Successfully restored: {total_restored} entities")
        if total_skipped > 0:
            print(f"⏭️  Skipped duplicates: {total_skipped} entities")
        if failed_entries:
            print(f"❌ Failed entries: {len(failed_entries)}")
            for entry in failed_entries[:5]:
                print(f"   - {entry['name']}: {entry['error']}")
            if len(failed_entries) > 5:
                print(f"   ... and {len(failed_entries) - 5} more")

        # Get collection stats
        collection_info = store.client.get_collection(target_collection)
        print(
            f"\n📊 Collection '{target_collection}' now contains {collection_info.points_count} points"
        )

        return total_restored > 0

    except Exception as e:
        print(f"❌ Error during direct restoration: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Main CLI interface."""
    parser = argparse.ArgumentParser(
        description="Generic backup and restore utility for manual entries from any Qdrant collection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backup manual entries from collection
  python manual_memory_backup.py backup -c memory-project
  python manual_memory_backup.py backup -c github-utils -o my_backup.json

  # Restore manual entries directly to Qdrant with vectorization
  python manual_memory_backup.py restore -f manual_entries_backup_memory-project.json
  python manual_memory_backup.py restore -f backup.json -c target-collection
  python manual_memory_backup.py restore -f backup.json --dry-run
  python manual_memory_backup.py restore -f backup.json --force  # Force duplicates

  # List supported entity types
  python manual_memory_backup.py --list-types
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Backup command
    backup_parser = subparsers.add_parser(
        "backup", help="Backup manual entries from collection"
    )
    backup_parser.add_argument(
        "--collection", "-c", required=True, help="Collection name to backup"
    )
    backup_parser.add_argument(
        "--output",
        "-o",
        help="Output file name (default: manual_entries_backup_{collection}.json)",
    )

    # Restore command
    restore_parser = subparsers.add_parser(
        "restore", help="Restore manual entries directly to Qdrant with vectorization"
    )
    restore_parser.add_argument(
        "--file", "-f", required=True, help="Path to backup file (JSON format)"
    )
    restore_parser.add_argument(
        "--collection",
        "-c",
        help="Target collection name (default: use original collection from backup)",
    )
    restore_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of entities per batch (default: 10)",
    )
    restore_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be restored without making changes",
    )
    restore_parser.add_argument(
        "--force",
        action="store_true",
        help="Force restore duplicates (default: skip existing entries)",
    )

    # Global options
    parser.add_argument(
        "--list-types",
        action="store_true",
        help="List manual and code entity types and exit",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.list_types:
        manual_types = get_manual_entity_types()
        code_types = get_code_entity_types()

        print("📝 Manual Entity Types (will be backed up):")
        for et in sorted(manual_types):
            print(f"  - {et}")

        print("\n🤖 Code Entity Types (will be excluded):")
        for et in sorted(code_types):
            print(f"  - {et}")

        return

    if not args.command:
        parser.print_help()
        return

    try:
        # Setup proper logging to logs/ directory
        project_path = Path.cwd()
        verbose = getattr(args, "verbose", False)
        logger = setup_logging(
            level="DEBUG" if verbose else "INFO",
            verbose=verbose,
            collection_name=f"backup-{args.command}",
            project_path=project_path,
        )
        logger.info(f"Starting {args.command} operation")

        if args.command == "backup":
            backup_file, count = backup_manual_entries(args.collection, args.output)
            logger.info(f"Backup complete: {count} entries saved to {backup_file}")
            print(
                f"\n🎉 Backup complete! {count} manual entries saved to {backup_file}"
            )

        elif args.command == "restore":
            result = restore_manual_entries(
                backup_file=args.file,
                collection_name=args.collection,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
                force_duplicates=args.force,
            )

            if result:
                logger.info("Restoration completed successfully")
                print("\n🎉 Restoration successful!")
            else:
                logger.error("Restoration failed")
                print("\n❌ Restoration failed")
                sys.exit(1)

    except Exception as e:
        if "logger" in locals():
            logger.error(f"Operation failed: {e}")
        print(f"\n❌ Operation failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
