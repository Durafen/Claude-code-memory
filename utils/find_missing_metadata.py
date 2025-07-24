#!/usr/bin/env python3
"""
Find which specific implementations are missing metadata chunks
"""

import sys
sys.path.append('/Users/Duracula 1/Python-Projects/memory')

from claude_indexer.config.config_loader import ConfigLoader
from qdrant_client import QdrantClient
from collections import defaultdict

def find_missing_metadata():
    """Find specific implementations missing metadata chunks"""
    
    config = ConfigLoader().load()
    client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
    
    print("=== FINDING MISSING METADATA CHUNKS ===")
    
    # Get all implementation chunks
    impl_result = client.scroll(
        collection_name='claude-memory',
        scroll_filter={'must': [{'key': 'chunk_type', 'match': {'value': 'implementation'}}]},
        limit=10000,
        with_payload=True
    )
    
    # Get all metadata chunks with has_implementation=true
    meta_result = client.scroll(
        collection_name='claude-memory',
        scroll_filter={
            'must': [
                {'key': 'chunk_type', 'match': {'value': 'metadata'}},
                {'key': 'metadata.has_implementation', 'match': {'value': True}}
            ]
        },
        limit=10000,
        with_payload=True
    )
    
    print(f"Implementation chunks: {len(impl_result[0])}")
    print(f"Metadata chunks (has_impl=true): {len(meta_result[0])}")
    
    # Extract identifiers
    impl_entities = {}
    for point in impl_result[0]:
        payload = point.payload
        entity_name = payload.get('entity_name', '')
        entity_type = payload.get('entity_type', 'unknown')
        
        # Get file_path from metadata if available
        file_path = payload.get('file_path', '')
        if not file_path and 'metadata' in payload:
            file_path = payload['metadata'].get('file_path', '')
        
        # Skip manual entries (no file_path)
        if not file_path:
            continue
            
        identifier = f"{file_path}::{entity_type}::{entity_name}"
        impl_entities[identifier] = {
            'entity_name': entity_name,
            'entity_type': entity_type,
            'file_path': file_path,
            'content_preview': payload.get('content', '')[:100]
        }
    
    meta_entities = set()
    for point in meta_result[0]:
        payload = point.payload
        entity_name = payload.get('entity_name', '')
        entity_type = payload.get('entity_type', 'unknown')
        
        # Get file_path from metadata
        file_path = payload.get('file_path', '')
        if not file_path and 'metadata' in payload:
            file_path = payload['metadata'].get('file_path', '')
        
        if file_path:  # Only count code entities
            identifier = f"{file_path}::{entity_type}::{entity_name}"
            meta_entities.add(identifier)
    
    # Find missing metadata
    missing_metadata = []
    for identifier, details in impl_entities.items():
        if identifier not in meta_entities:
            missing_metadata.append(details)
    
    print(f"\n=== MISSING METADATA ANALYSIS ===")
    print(f"Code implementations: {len(impl_entities)}")
    print(f"Code metadata (has_impl=true): {len(meta_entities)}")
    print(f"Missing metadata chunks: {len(missing_metadata)}")
    
    # Categorize missing by file/type
    by_file = defaultdict(list)
    by_type = defaultdict(int)
    by_extension = defaultdict(int)
    
    for missing in missing_metadata:
        file_path = missing['file_path']
        entity_type = missing['entity_type']
        
        by_file[file_path].append(missing)
        by_type[entity_type] += 1
        
        # Get file extension
        if '.' in file_path:
            ext = file_path.split('.')[-1]
            by_extension[ext] += 1
        else:
            by_extension['no_ext'] += 1
    
    print(f"\n=== MISSING BY ENTITY TYPE ===")
    for entity_type, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        print(f"  {entity_type}: {count}")
    
    print(f"\n=== MISSING BY FILE EXTENSION ===")
    for ext, count in sorted(by_extension.items(), key=lambda x: x[1], reverse=True):
        print(f"  .{ext}: {count}")
    
    # Show non-.md files specifically
    non_md_missing = [m for m in missing_metadata if not m['file_path'].endswith('.md')]
    print(f"\n=== NON-MARKDOWN MISSING ({len(non_md_missing)} total) ===")
    
    if non_md_missing:
        non_md_by_ext = defaultdict(int)
        for missing in non_md_missing:
            file_path = missing['file_path']
            if '.' in file_path:
                ext = file_path.split('.')[-1]
                non_md_by_ext[ext] += 1
            else:
                non_md_by_ext['no_ext'] += 1
        
        for ext, count in sorted(non_md_by_ext.items(), key=lambda x: x[1], reverse=True):
            print(f"  .{ext}: {count}")
        
        print(f"\n=== SAMPLE NON-MARKDOWN MISSING ===")
        for i, missing in enumerate(non_md_missing[:10]):
            print(f"  {i+1}. {missing['entity_type']}: {missing['entity_name']}")
            print(f"      File: {missing['file_path']}")
            print(f"      Content: {missing['content_preview']}...")
            print()
    else:
        print("  ALL missing metadata chunks are from .md files only!")
    
    print(f"\n=== TOP FILES WITH MISSING METADATA ===")
    sorted_files = sorted(by_file.items(), key=lambda x: len(x[1]), reverse=True)
    for file_path, missing_list in sorted_files[:10]:
        print(f"  {file_path}: {len(missing_list)} missing")
        for i, missing in enumerate(missing_list[:3]):  # Show first 3
            print(f"    - {missing['entity_type']}: {missing['entity_name']}")
        if len(missing_list) > 3:
            print(f"    ... and {len(missing_list) - 3} more")
    
    print(f"\n=== SAMPLE MISSING IMPLEMENTATIONS ===")
    for i, missing in enumerate(missing_metadata[:10]):
        print(f"  {i+1}. {missing['entity_type']}: {missing['entity_name']}")
        print(f"      File: {missing['file_path']}")
        print(f"      Content: {missing['content_preview']}...")
        print()
    
    return missing_metadata

if __name__ == "__main__":
    find_missing_metadata()