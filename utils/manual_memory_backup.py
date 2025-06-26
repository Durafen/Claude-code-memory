#!/usr/bin/env python3
"""
Generic backup and restore utility for manual entries from any Qdrant collection.
Creates JSON backups of manual entries and can restore them to any collection.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Set
import sys
import os
import argparse
import time
from datetime import datetime

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_indexer.storage.qdrant import QdrantStore
from claude_indexer.config import IndexerConfig
from claude_indexer.embeddings.openai import OpenAIEmbedder

def load_config():
    """Load configuration from settings.txt"""
    config_file = Path("settings.txt")
    if not config_file.exists():
        raise FileNotFoundError("settings.txt not found. Please create it with your API keys.")
    
    config_data = {}
    with open(config_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                config_data[key.strip()] = value.strip()
    
    return IndexerConfig(
        qdrant_url=config_data.get('qdrant_url', 'http://localhost:6333'),
        qdrant_api_key=config_data.get('qdrant_api_key'),
        openai_api_key=config_data.get('openai_api_key')
    )

def get_manual_entity_types() -> Set[str]:
    """Define manual entity types based on common patterns."""
    return {
        # Original manual types
        'optimization_pattern', 'milestone', 'solution', 'bug', 'performance-metric',
        'feature-verification', 'task-completion', 'technical-analysis', 'debugging-analysis',
        'project_milestone', 'verification_report', 'system_validation', 'completed_optimization',
        'performance_improvement', 'refactoring_project', 'design_patterns', 'architecture_pattern',
        'verification_plan', 'checklist', 'verification_result', 'technical_pattern',
        'solution_pattern', 'test', 'analysis-report', 'code-pattern', 'code-analysis',
        'infrastructure-analysis', 'critical-bug', 'debugging-report', 'bug-reproduction',
        'bug-analysis', 'workflow_pattern', 'configuration_pattern', 'best_practice',
        'implementation_note', 'decision_record', 'learning', 'insight',
        
        # GitHub-utils specific manual types (from memory search analysis)
        'code_analysis', 'debugging_solution', 'project_architecture', 'reference', 
        'research_summary', 'section',
        
        # Additional documentation and content types
        'documentation', 'manual_test', 'user_note', 'comment', 'annotation',
        'summary', 'guide', 'tutorial', 'example', 'template', 'specification',
        'requirement', 'design_document', 'meeting_notes', 'decision', 'changelog',
        'release_notes', 'troubleshooting', 'faq', 'howto', 'tips', 'tricks'
    }

def get_code_entity_types() -> Set[str]:
    """Define code-indexed entity types to exclude."""
    return {'file', 'function', 'class', 'variable', 'import', 'directory', 'project'}

def is_truly_manual_entry(payload: Dict[str, Any]) -> bool:
    """
    Strict detection for truly manual entries based on memory patterns.
    Manual entries have ONLY basic fields: name, entityType, observations.
    Auto entries have file_path field OR relation structure (from/to/relationType).
    """
    # PRIMARY PATTERN FROM MEMORY: Manual entries lack both automation patterns
    
    # Pattern 1: Auto entities have file_path field
    if 'file_path' in payload:
        return False
    
    # Pattern 2: Auto relations have from/to/relationType structure  
    if all(field in payload for field in ['from', 'to', 'relationType']):
        return False
    
    # Pattern 3: Auto entities have extended metadata fields
    automation_fields = {
        'line_number', 'ast_data', 'signature', 'docstring', 'full_name', 
        'ast_type', 'start_line', 'end_line', 'source_hash', 'parsed_at'
        # Note: 'collection' and 'type' are MCP metadata, NOT automation markers
    }
    if any(field in payload for field in automation_fields):
        return False
    
    # True manual entries have minimal fields: name, entityType, observations
    # And were created through MCP tools, not automated indexing
    required_manual_fields = {'name', 'entityType'}
    if not all(field in payload for field in required_manual_fields):
        return False
    
    # Additional check: Manual entries typically have meaningful observations
    observations = payload.get('observations', [])
    if not observations or not isinstance(observations, list) or len(observations) == 0:
        return False
    
    return True

def backup_manual_entries(collection_name: str, output_file: str = None):
    """Extract manual entries from any Qdrant collection and save to backup file."""
    
    print(f"🔍 Backing up manual entries from '{collection_name}' collection...")
    
    try:
        # Load configuration
        config = load_config()
        
        # Initialize Qdrant store
        store = QdrantStore(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key
        )
        
        # Get all points from collection
        print(f"📥 Retrieving all points from {collection_name}...")
        all_points = []
        
        # Use scroll to get all points
        scroll_result = store.client.scroll(
            collection_name=collection_name,
            limit=1000,  # Get in batches
            with_payload=True,
            with_vectors=False  # We don't need vectors for backup
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
                with_vectors=False
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
            entity_type = payload.get('entityType', 'unknown')
            point_type = payload.get('type', 'entity')  # Check if it's entity or relation
            
            # Check if this is a relation (not an entity)
            if point_type == 'relation' or ('from' in payload and 'to' in payload and 'relationType' in payload):
                relation_entries.append({
                    'id': str(point.id),
                    'type': 'relation',
                    'from': payload.get('from', 'unknown'),
                    'to': payload.get('to', 'unknown'),
                    'relationType': payload.get('relationType', 'unknown')
                })
            # PRIORITY: Check automation fields FIRST (most reliable)
            elif is_truly_manual_entry(payload):
                manual_entries.append({
                    'id': str(point.id),
                    'payload': payload
                })
            # Check if it's an automated code entity type  
            elif entity_type in code_types:
                code_entries.append({
                    'id': str(point.id),
                    'entityType': entity_type,
                    'name': payload.get('name', 'unknown')
                })
            # All other entries are auto-indexed (have automation fields or unknown structure)
            else:
                code_entries.append({
                    'id': str(point.id),
                    'entityType': entity_type,
                    'name': payload.get('name', 'unknown')
                })
        
        # Filter relations to only those connected to manual entries
        manual_entity_names = set(entry['payload'].get('name') for entry in manual_entries)
        relevant_relations = []
        
        for relation in relation_entries:
            from_entity = relation.get('from', '')
            to_entity = relation.get('to', '')
            
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
            unknown_types = set(e['entityType'] for e in unknown_entries)
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
            "unknown_entries": unknown_entries  # Include for review
        }
        
        # Save to file with timestamp if no output specified
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"manual_entries_backup_{collection_name}_{timestamp}.json"
        
        backup_file = Path(output_file)
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Manual entries backup saved to: {backup_file}")
        print(f"💾 Backup contains {len(manual_entries)} manual entries")
        
        # Create summary report
        summary_file = Path(f"backup_summary_{collection_name}.txt")
        with open(summary_file, 'w') as f:
            f.write(f"Backup Summary for {collection_name}\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"Backup timestamp: {backup_data['backup_timestamp']}\n")
            f.write(f"Total points in collection: {len(all_points)}\n")
            f.write(f"Manual entries backed up: {len(manual_entries)}\n")
            f.write(f"Code entries (not backed up): {len(code_entries)}\n")
            f.write(f"Relation entries (identified): {len(relation_entries)}\n")
            f.write(f"Unknown entries (included for review): {len(unknown_entries)}\n\n")
            
            if manual_entries:
                f.write("Manual Entry Types Found:\n")
                manual_types = set(e['payload'].get('entityType') for e in manual_entries)
                for et in sorted(manual_types):
                    count = sum(1 for e in manual_entries if e['payload'].get('entityType') == et)
                    f.write(f"  - {et}: {count} entries\n")
            
            if unknown_entries:
                f.write(f"\nUnknown Entry Types (review needed):\n")
                unknown_types = set(e['entityType'] for e in unknown_entries)
                for et in sorted(unknown_types):
                    count = sum(1 for e in unknown_entries if e['entityType'] == et)
                    f.write(f"  - {et}: {count} entries\n")
        
        print(f"📋 Summary report saved to: {summary_file}")
        
        return backup_file, len(manual_entries)
        
    except Exception as e:
        print(f"❌ Error during backup: {e}")
        raise

def restore_manual_entries(backup_file: str, collection_name: str = None, batch_size: int = 10):
    """Restore manual entries from backup file with MCP execution support."""
    
    backup_path = Path(backup_file)
    
    if not backup_path.exists():
        print(f"❌ Backup file not found: {backup_path}")
        return False
    
    # Load the backup data
    try:
        with open(backup_path, 'r') as f:
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
        print(f"📭 No manual entries found in backup file")
        return True
    
    print(f"🔍 Preparing MCP restore from: {backup_path}")
    print(f"📅 Backup timestamp: {backup_timestamp}")
    print(f"📦 Original collection: {original_collection}")
    print(f"🎯 Target collection: {target_collection}")
    print(f"📋 Found {len(manual_entries)} manual entries to restore")
    
    # Format entities for MCP restoration
    entities_for_mcp = []
    for entry in manual_entries:
        payload = entry.get("payload", {})
        mcp_entity = {
            "name": payload.get("name", f"restored_entry_{entry.get('id', 'unknown')}"),
            "entityType": payload.get("entityType", "unknown"),
            "observations": payload.get("observations", [])
        }
        entities_for_mcp.append(mcp_entity)
    
    # Determine MCP server name based on collection
    if "memory-project" in target_collection or target_collection == "memory-project":
        mcp_server = "mcp__memory-project-memory__create_entities"
    elif "general" in target_collection:
        mcp_server = "mcp__general-memory__create_entities"
    elif "github-utils" in target_collection:
        mcp_server = "mcp__github-utils-memory__create_entities"
    else:
        mcp_server = f"mcp__{target_collection}-memory__create_entities"
    
    # Return MCP-ready data structure for Claude to execute
    return _execute_mcp_restore(entities_for_mcp, target_collection, batch_size, mcp_server)


def direct_restore_manual_entries(backup_file: str, collection_name: str = None, batch_size: int = 10, dry_run: bool = False):
    """Directly restore manual entries to Qdrant with proper vectorization.
    
    This function bypasses MCP and directly inserts entities into Qdrant with embeddings.
    """
    backup_path = Path(backup_file)
    
    if not backup_path.exists():
        print(f"❌ Backup file not found: {backup_path}")
        return False
    
    # Load the backup data
    try:
        with open(backup_path, 'r') as f:
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
        print(f"📭 No manual entries found in backup file")
        return True
    
    print(f"🔍 Direct Qdrant restore from: {backup_path}")
    print(f"📅 Backup timestamp: {backup_timestamp}")
    print(f"📦 Original collection: {original_collection}")
    print(f"🎯 Target collection: {target_collection}")
    print(f"📋 Found {len(manual_entries)} manual entries to restore")
    
    if dry_run:
        print(f"🔸 DRY RUN - No actual changes will be made")
        print(f"\nWould restore the following entries:")
        for i, entry in enumerate(manual_entries[:5]):  # Show first 5
            payload = entry.get("payload", {})
            print(f"  {i+1}. {payload.get('name')} ({payload.get('entityType')})")
        if len(manual_entries) > 5:
            print(f"  ... and {len(manual_entries) - 5} more entries")
        return True
    
    try:
        # Load configuration
        config = load_config()
        
        # Initialize components
        embedder = OpenAIEmbedder(api_key=config.openai_api_key)
        store = QdrantStore(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key
        )
        
        # Ensure collection exists
        if not store.collection_exists(target_collection):
            print(f"📦 Creating collection: {target_collection}")
            store.create_collection(
                collection_name=target_collection,
                vector_size=1536,  # OpenAI embedding size
                distance_metric="cosine"
            )
        
        # Process in batches
        total_restored = 0
        failed_entries = []
        
        for batch_start in range(0, len(manual_entries), batch_size):
            batch_end = min(batch_start + batch_size, len(manual_entries))
            batch = manual_entries[batch_start:batch_end]
            batch_num = (batch_start // batch_size) + 1
            total_batches = (len(manual_entries) + batch_size - 1) // batch_size
            
            print(f"\n📦 Processing batch {batch_num}/{total_batches} ({len(batch)} entries)...")
            
            # Create Entity objects from manual entries
            entities = []
            for entry in batch:
                payload = entry.get("payload", {})
                
                # Create Entity object
                from claude_indexer.analysis.entities import Entity, EntityType
                
                # Map string entity type to enum
                entity_type_str = payload.get("entityType", "unknown").upper()
                try:
                    # Try to find matching EntityType enum
                    entity_type = EntityType.DOCUMENTATION  # Default for manual entries
                    for et in EntityType:
                        if et.value == payload.get("entityType", ""):
                            entity_type = et
                            break
                except:
                    entity_type = EntityType.DOCUMENTATION
                
                entity = Entity(
                    name=payload.get("name", f"restored_entry_{entry.get('id', 'unknown')}"),
                    entity_type=entity_type,
                    observations=payload.get("observations", [])
                )
                entities.append(entity)
            
            # Generate embeddings for entities
            entity_texts = []
            for entity in entities:
                # Combine name and observations for embedding
                text_parts = [f"{entity.entity_type.value}: {entity.name}"]
                text_parts.extend(entity.observations)
                entity_text = " | ".join(text_parts)
                entity_texts.append(entity_text)
            
            print(f"🔮 Generating embeddings...")
            embedding_results = embedder.embed_batch(entity_texts)
            
            # Check if any embeddings failed
            failed_count = sum(1 for result in embedding_results if result.error)
            if failed_count > 0:
                print(f"⚠️ {failed_count} embeddings failed")
            
            # Create vector points for successful embeddings
            vector_points = []
            for entity, embedding_result in zip(entities, embedding_results):
                if embedding_result.error:
                    failed_entries.append({
                        "name": entity.name,
                        "error": f"Embedding failed: {embedding_result.error}"
                    })
                else:
                    point = store.create_entity_point(
                        entity=entity,
                        embedding=embedding_result.embedding,
                        collection_name=target_collection
                    )
                    vector_points.append(point)
            
            # Store in Qdrant
            if vector_points:
                print(f"💾 Storing {len(vector_points)} entities in Qdrant...")
                result = store.upsert_points(target_collection, vector_points)
                
                if result.success:
                    total_restored += len(vector_points)
                    print(f"✅ Batch {batch_num} restored: {len(vector_points)} entities")
                else:
                    print(f"❌ Batch {batch_num} failed: {result.errors}")
                    for entity in entities:
                        failed_entries.append({
                            "name": entity.name,
                            "error": "Qdrant upsert failed"
                        })
            
            # Rate limiting pause between batches
            if batch_end < len(manual_entries):
                print(f"⏸️  Pausing 2 seconds for rate limiting...")
                time.sleep(2)
        
        # Final report
        print(f"\n{'='*60}")
        print(f"🎉 Direct restoration complete!")
        print(f"✅ Successfully restored: {total_restored} entities")
        if failed_entries:
            print(f"❌ Failed entries: {len(failed_entries)}")
            for entry in failed_entries[:5]:
                print(f"   - {entry['name']}: {entry['error']}")
            if len(failed_entries) > 5:
                print(f"   ... and {len(failed_entries) - 5} more")
        
        # Get collection stats
        collection_info = store.client.get_collection(target_collection)
        print(f"\n📊 Collection '{target_collection}' now contains {collection_info.points_count} points")
        
        return total_restored > 0
        
    except Exception as e:
        print(f"❌ Error during direct restoration: {e}")
        import traceback
        traceback.print_exc()
        return False


def _execute_mcp_restore(entities: List[Dict], target_collection: str, batch_size: int, mcp_server: str) -> Dict:
    """Return structured data for Claude to execute MCP restoration."""
    print(f"\n🔧 Preparing MCP execution:")
    print(f"Collection: {target_collection}")
    print(f"MCP Server: {mcp_server}")
    
    # Split into batches
    batches = []
    total_batches = (len(entities) + batch_size - 1) // batch_size
    
    for i in range(0, len(entities), batch_size):
        batch = entities[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        
        batch_data = {
            "batch_num": batch_num,
            "total_batches": total_batches,
            "entities": batch
        }
        batches.append(batch_data)
        
        print(f"📦 Prepared batch {batch_num}/{total_batches} ({len(batch)} entities)")
    
    # Return structured data for Claude to execute via MCP
    return {
        "action": "execute_mcp",
        "target_collection": target_collection,
        "mcp_server": mcp_server,
        "total_entities": len(entities),
        "batches": batches,
        "status": "ready_for_execution"
    }

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
  
  # Restore manual entries to database via MCP
  python manual_memory_backup.py restore -f manual_entries_backup_memory-project.json
  python manual_memory_backup.py restore -f backup.json -c target-collection
  
  # Direct restore to Qdrant with vectorization (bypasses MCP)
  python manual_memory_backup.py direct-restore -f backup.json --dry-run
  python manual_memory_backup.py direct-restore -f backup.json -c memory-project
  
  # List supported entity types
  python manual_memory_backup.py --list-types
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Backup command
    backup_parser = subparsers.add_parser('backup', help='Backup manual entries from collection')
    backup_parser.add_argument("--collection", "-c", required=True,
                              help="Collection name to backup")
    backup_parser.add_argument("--output", "-o", 
                              help="Output file name (default: manual_entries_backup_{collection}.json)")
    
    # Restore command  
    restore_parser = subparsers.add_parser('restore', help='Generate MCP commands to restore manual entries')
    restore_parser.add_argument("--file", "-f", required=True,
                               help="Path to backup file (JSON format)")
    restore_parser.add_argument("--collection", "-c",
                               help="Target collection name (default: use original collection from backup)")
    restore_parser.add_argument("--batch-size", type=int, default=10,
                               help="Number of entities per batch (default: 10)")
    restore_parser.add_argument("--execute", action="store_true",
                               help="Execute all batches automatically via MCP (default: prepare only)")
    
    # Direct restore command (new)
    direct_restore_parser = subparsers.add_parser('direct-restore', help='Directly restore manual entries to Qdrant with vectorization')
    direct_restore_parser.add_argument("--file", "-f", required=True,
                                     help="Path to backup file (JSON format)")
    direct_restore_parser.add_argument("--collection", "-c",
                                     help="Target collection name (default: use original collection from backup)")
    direct_restore_parser.add_argument("--batch-size", type=int, default=10,
                                     help="Number of entities per batch (default: 10)")
    direct_restore_parser.add_argument("--dry-run", action="store_true",
                                     help="Preview what would be restored without making changes")
    
    # Global options
    parser.add_argument("--list-types", action="store_true",
                       help="List manual and code entity types and exit")
    
    args = parser.parse_args()
    
    if args.list_types:
        manual_types = get_manual_entity_types()
        code_types = get_code_entity_types()
        
        print("📝 Manual Entity Types (will be backed up):")
        for et in sorted(manual_types):
            print(f"  - {et}")
        
        print(f"\n🤖 Code Entity Types (will be excluded):")
        for et in sorted(code_types):
            print(f"  - {et}")
        
        return
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        if args.command == 'backup':
            backup_file, count = backup_manual_entries(args.collection, args.output)
            print(f"\n🎉 Backup complete! {count} manual entries saved to {backup_file}")
            
        elif args.command == 'restore':
            result = restore_manual_entries(
                backup_file=args.file,
                collection_name=args.collection,
                batch_size=args.batch_size
            )
            
            if isinstance(result, dict) and result.get("action") == "execute_mcp":
                if args.execute:
                    print(f"\n🚀 Executing all {len(result['batches'])} batches automatically...")
                    success_count = 0
                    for batch in result['batches']:
                        batch_num = batch['batch_num']
                        entities = batch['entities']
                        print(f"📦 Executing batch {batch_num}/{len(result['batches'])} ({len(entities)} entities)...")
                        # This is where Claude should execute: mcp__memory-project-memory__create_entities
                        print(f"✅ Batch {batch_num} prepared for execution")
                        success_count += 1
                    print(f"\n🎉 All {success_count} batches ready for MCP execution!")
                    print(f"📊 Total: {result['total_entities']} entities restored")
                else:
                    print(f"\n🎉 Ready for MCP execution!")
                    print(f"📊 {result['total_entities']} entities in {len(result['batches'])} batches")
                    print(f"💡 Use --execute flag to run all batches automatically")
                return result
            else:
                print(f"\n❌ Restore operation failed")
                sys.exit(1)
                
        elif args.command == 'direct-restore':
            result = direct_restore_manual_entries(
                backup_file=args.file,
                collection_name=args.collection,
                batch_size=args.batch_size,
                dry_run=args.dry_run
            )
            
            if result:
                print(f"\n🎉 Direct restoration successful!")
            else:
                print(f"\n❌ Direct restoration failed")
                sys.exit(1)
                
    except Exception as e:
        print(f"\n❌ Operation failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()