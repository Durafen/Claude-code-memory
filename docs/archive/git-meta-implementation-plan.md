# Git Storage + Meta's Diff Metadata Implementation Plan

## Executive Summary

This plan implements a content-addressable storage system (Git-like) combined with Meta's immutable diff layers to solve the core inefficiency: currently, modifying a single function in a file causes ALL entities (40+) to be deleted, recreated, and re-embedded. The new approach will only process changed content, reducing API calls by 90%+ for typical modifications.

## Architecture Overview

### Current Problem Flow
```
File Modified → Delete ALL entities → Recreate ALL → Embed ALL (40 operations)
```

### New Git+Meta Flow
```
File Modified → Hash entities → Check existing → Embed ONLY changed (1-2 operations)
```

## Core Components

### 1. Content Hashing System

**Location**: `claude_indexer/storage/qdrant.py`

```python
class ContentHashMixin:
    """Mixin for content-addressable storage functionality"""

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Generate SHA256 hash of content"""
        return hashlib.sha256(content.encode()).hexdigest()

    def check_content_exists(self, collection_name: str, content_hash: str) -> bool:
        """Check if content hash already exists in storage"""
        results = self.client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(
                must=[FieldCondition(key="content_hash", match=MatchValue(value=content_hash))]
            ),
            limit=1
        )
        return len(results[0]) > 0
```

### 2. Enhanced Entity Storage with Progressive Disclosure

**Modify**: Both `EntityChunk` types to include content hash while preserving dual-chunk architecture

```python
# In claude_indexer/analysis/entities.py
def create_metadata_chunk(self) -> Dict[str, Any]:
    content = self._generate_metadata_content()
    return {
        "id": self.generate_id("metadata"),
        "entity_name": self.name,
        "entity_type": self.entity_type,
        "chunk_type": "metadata",
        "content": content,
        "content_hash": ContentHashMixin.compute_content_hash(content),  # NEW
        "has_implementation": self.has_implementation,  # PRESERVE
        "file_path": self.file_path,
        # ... other fields
    }

def create_implementation_chunk(self) -> Dict[str, Any]:
    """Create implementation chunk with content hashing"""
    if not self.has_implementation:
        return None

    content = self._get_full_source_code()
    return {
        "id": self.generate_id("implementation"),
        "entity_name": self.name,
        "entity_type": self.entity_type,
        "chunk_type": "implementation",
        "content": content,
        "content_hash": ContentHashMixin.compute_content_hash(content),  # NEW
        "start_line": self.start_line,
        "end_line": self.end_line,
        "semantic_metadata": self.semantic_metadata,  # PRESERVE
        "file_path": self.file_path,
        # ... other fields
    }
```

### 3. Diff Layer Tracking

**New Module**: `claude_indexer/storage/diff_layers.py`

```python
@dataclass
class DiffLayer:
    """Represents a change layer in the immutable diff system"""
    timestamp: datetime
    file_path: str
    event_type: str  # "created", "modified", "deleted"
    changes: Dict[str, Any]

@dataclass
class DiffSketch:
    """Mechanical summary of changes (Meta's approach)"""
    added_entities: List[str]      # entity IDs added
    removed_entities: List[str]    # entity IDs removed
    modified_entities: List[str]   # entity IDs with content changes
    unchanged_entities: List[str]  # entity IDs with same content hash

class DiffLayerManager:
    """Manages immutable diff layers for efficient change tracking"""

    def create_diff_sketch(self, old_entities: List[Entity], new_entities: List[Entity]) -> DiffSketch:
        """Compare entity sets and create change summary"""
        old_by_id = {e.id: e for e in old_entities}
        new_by_id = {e.id: e for e in new_entities}

        # Compute differences using content hashes
        added = [id for id in new_by_id if id not in old_by_id]
        removed = [id for id in old_by_id if id not in new_by_id]

        modified = []
        unchanged = []
        for id in set(old_by_id) & set(new_by_id):
            old_hash = ContentHashMixin.compute_content_hash(old_by_id[id].content)
            new_hash = ContentHashMixin.compute_content_hash(new_by_id[id].content)
            if old_hash != new_hash:
                modified.append(id)
            else:
                unchanged.append(id)

        return DiffSketch(added, removed, modified, unchanged)
```

### 4. Optimized _store_vectors

**Modified**: `claude_indexer/indexer.py::_store_vectors`

```python
def _store_vectors(self, collection_name: str, entities: List[Entity],
                   relations: List[Relation], implementation_chunks: List[EntityChunk]) -> bool:
    """Store entities with content-aware deduplication"""

    # Phase 1: Content hash analysis
    entity_hashes = {}
    chunks_to_embed = []
    chunks_to_skip = []

    # Check which entities need embedding
    for entity in entities:
        chunk = EntityChunk.create_metadata_chunk(entity)
        content_hash = chunk["content_hash"]
        entity_hashes[entity.id] = content_hash

        if self.vector_store.check_content_exists(collection_name, content_hash):
            chunks_to_skip.append((chunk, "exists"))
        else:
            chunks_to_embed.append(chunk)

    # Log efficiency gains
    if chunks_to_skip:
        logger.info(f"⚡ Skipping {len(chunks_to_skip)} unchanged entities (saved {len(chunks_to_skip)} embeddings)")

    # Phase 2: Embed only new/changed content
    if chunks_to_embed:
        texts_to_embed = [chunk["content"] for chunk in chunks_to_embed]
        embeddings = self.embedder.embed_batch(texts_to_embed)

        # Create points for new content
        points = []
        for chunk, embedding in zip(chunks_to_embed, embeddings):
            point = self.vector_store.create_chunk_point(
                chunk_id=chunk["id"],
                vector=embedding,
                chunk_data=chunk
            )
            points.append(point)

        # Store new points
        self.vector_store.batch_upsert(collection_name, points)

    # Phase 3: Update metadata for skipped entities (update timestamps, etc.)
    # Note: Relations still processed normally as they depend on entity relationships

    return True
```

### 5. Event-Aware Watcher Integration with Debouncing

**Modified**: `claude_indexer/watcher/handler.py`

```python
def _process_file_change(self, file_path: Path, event_type: str) -> None:
    """Process file changes with event awareness and debouncing"""

    # Use AsyncDebouncer for efficient batching
    if hasattr(self, 'debouncer') and self.debouncer:
        # Add to debouncer queue
        asyncio.run_coroutine_threadsafe(
            self.debouncer.add_file_event(str(file_path), event_type),
            self.loop
        )
    else:
        # Fallback to direct processing
        self._process_file_direct(file_path, event_type)

def _setup_debouncer_callback(self):
    """Setup callback for AsyncDebouncer integration"""
    async def debouncer_callback(batch_event: Dict[str, Any]):
        """Process debounced batch events with Git+Meta efficiency"""
        modified_files = [Path(f) for f in batch_event.get("modified_files", [])]
        deleted_files = [Path(f) for f in batch_event.get("deleted_files", [])]

        # Process deletions immediately (no debouncing needed)
        if deleted_files:
            run_indexing_with_specific_files(
                indexer=self.indexer,
                project=self.project_path,
                files=deleted_files,
                collection_name=self.collection_name,
                event_type="deleted",
                verbose=self.verbose
            )

        # Process modifications with Git+Meta content checking
        if modified_files:
            run_indexing_with_specific_files(
                indexer=self.indexer,
                project=self.project_path,
                files=modified_files,
                collection_name=self.collection_name,
                event_type="modified",  # NEW: Triggers Git+Meta dedup
                verbose=self.verbose
            )

    return debouncer_callback
```

**Modified**: `claude_indexer/main.py`

```python
def run_indexing_with_specific_files(indexer, project, files, collection_name,
                                     event_type="modified", verbose=False):
    """Process specific files with event awareness"""

    # Only run cleanup for actual deletions
    if event_type == "deleted":
        for file_path in files:
            indexer._handle_deleted_files(collection_name, file_path, verbose)
    else:
        # For modifications/creations, use content-aware processing
        # The new _store_vectors will handle deduplication
        indexer._process_file_batch(files, collection_name)
```

### 6. Smart Relations Processing

**The Missing Piece**: Relations optimization for changed entities only

**Current Problem**: All relations in a file get re-embedded even for single entity changes
```python
# File with 40 entities = ~100-200 relations
# Change 1 function → ALL 200 relations re-embedded
```

**New Approach**: Only process relations involving changed entities

```python
class SmartRelationsProcessor:
    """Processes only relations involving changed entities"""

    def filter_relations_for_changes(self, all_relations: List[Relation],
                                    changed_entity_ids: Set[str]) -> Tuple[List[Relation], List[Relation]]:
        """Split relations into changed vs unchanged based on entity involvement"""

        relations_to_update = []
        relations_unchanged = []

        for relation in all_relations:
            # Check if relation involves any changed entity
            if (relation.from_entity in changed_entity_ids or
                relation.to_entity in changed_entity_ids):
                relations_to_update.append(relation)
            else:
                # Relation between two unchanged entities - skip
                relations_unchanged.append(relation)

        return relations_to_update, relations_unchanged

    def get_existing_relations_hashes(self, collection_name: str,
                                    file_path: str) -> Dict[str, str]:
        """Get existing relation content hashes for unchanged relations"""
        results = self.client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(must=[
                FieldCondition(key="file_path", match=MatchValue(value=file_path)),
                FieldCondition(key="type", match=MatchValue(value="relation"))
            ]),
            with_payload=True
        )

        relation_hashes = {}
        for point in results[0]:
            if point.payload:
                relation_id = point.payload.get("relation_id")
                content_hash = point.payload.get("content_hash")
                if relation_id and content_hash:
                    relation_hashes[relation_id] = content_hash

        return relation_hashes

### 7. Enhanced Orphaned Relations Cleanup

**Critical Addition**: Handle orphaning scenarios created by content hash changes

```python
class EnhancedOrphanCleanup:
    """Enhanced orphan cleanup for hash-based storage scenarios"""

    def cleanup_hash_orphaned_relations(self, collection_name: str,
                                      file_path: str = None) -> int:
        """Clean up relations orphaned by content hash changes"""

        # Scenario 1: Entity content changed, hash changed, old relations point to old entity
        # Scenario 2: Entity deleted but relations still reference it
        # Scenario 3: Cross-file relations where target entity changed hash

        orphaned_count = 0

        # Get all relations (file-specific if provided)
        relation_filter = [FieldCondition(key="type", match=MatchValue(value="relation"))]
        if file_path:
            relation_filter.append(
                FieldCondition(key="file_path", match=MatchValue(value=file_path))
            )

        relations_result = self.client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(must=relation_filter),
            with_payload=True,
            limit=1000
        )

        orphaned_points = []

        for point in relations_result[0]:
            if not point.payload:
                continue

            relation_data = point.payload
            from_entity = relation_data.get("from_entity")
            to_entity = relation_data.get("to_entity")

            # Check if target entities still exist with current content hashes
            if not self._entity_exists_with_current_hash(collection_name, from_entity):
                orphaned_points.append(point.id)
                continue

            if not self._entity_exists_with_current_hash(collection_name, to_entity):
                orphaned_points.append(point.id)
                continue

        # Batch delete orphaned relations
        if orphaned_points:
            self.client.delete(
                collection_name=collection_name,
                points_selector=PointIdsList(points=orphaned_points)
            )
            orphaned_count = len(orphaned_points)

        return orphaned_count

    def _entity_exists_with_current_hash(self, collection_name: str,
                                        entity_name: str) -> bool:
        """Check if entity exists with valid current content hash"""

        # Look for metadata chunk of this entity
        results = self.client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(must=[
                FieldCondition(key="entity_name", match=MatchValue(value=entity_name)),
                FieldCondition(key="chunk_type", match=MatchValue(value="metadata"))
            ]),
            limit=1,
            with_payload=True
        )

        if not results[0]:
            return False

        # Entity exists if we found a metadata chunk
        # The hash validation happens during storage - if it's there, it's current
        return True

# Integration with existing _store_vectors
def _enhanced_store_vectors_with_cleanup(self, collection_name: str, entities: List[Entity],
                                        relations: List[Relation], implementation_chunks: List[EntityChunk],
                                        changed_entity_ids: Set[str] = None) -> bool:
    """Enhanced _store_vectors with integrated orphan cleanup"""

    # ... existing Git+Meta logic ...

    # NEW: Clean up orphaned relations after processing
    cleanup = EnhancedOrphanCleanup()
    cleanup.client = self.vector_store.client

    orphaned_count = cleanup.cleanup_hash_orphaned_relations(
        collection_name=collection_name,
        file_path=self.current_file_path  # Clean file-specific orphans
    )

    if orphaned_count > 0:
        logger.info(f"🧹 Cleaned {orphaned_count} orphaned relations after hash changes")

    return True

### 8. Batch Processing & Streaming Compatibility

**Enhanced**: `_create_batch_callback` integration with Git+Meta efficiency

```python
# In claude_indexer/indexer.py
def _create_batch_callback(self, collection_name: str, verbose: bool = False):
    """Create batch callback with Git+Meta content deduplication"""

    def batch_callback(entities: List[Entity], relations: List[Relation],
                      implementation_chunks: List[EntityChunk]) -> bool:
        """Process batch with content hashing efficiency"""

        # Track changed entities for smart relations processing
        changed_entity_ids = set()

        # Apply Git+Meta approach to batch
        success = self._enhanced_store_vectors_with_cleanup(
            collection_name=collection_name,
            entities=entities,
            relations=relations,
            implementation_chunks=implementation_chunks,
            changed_entity_ids=changed_entity_ids,  # NEW: Track changes in batch
            verbose=verbose
        )

        # Stream progress with efficiency metrics
        if verbose and hasattr(self, '_stream_progress'):
            efficiency_stats = {
                "entities_processed": len(entities),
                "entities_deduplicated": len(entities) - len(changed_entity_ids),
                "relations_processed": len(relations),
                "dedup_savings_percent": ((len(entities) - len(changed_entity_ids)) / len(entities) * 100) if entities else 0
            }
            self._stream_progress("batch_processed", efficiency_stats)

        return success

    return batch_callback

# Streaming Response Integration
class StreamingResponseBuilder:
    """Enhanced streaming with Git+Meta efficiency reporting"""

    def add_efficiency_metrics(self, metrics: Dict[str, Any]):
        """Add deduplication efficiency metrics to stream"""
        if "dedup_savings_percent" in metrics:
            self.add_section("efficiency", {
                "savings_percent": metrics["dedup_savings_percent"],
                "entities_skipped": metrics.get("entities_deduplicated", 0),
                "message": f"🚀 {metrics['dedup_savings_percent']:.1f}% efficiency gain from content deduplication"
            })

### 9. Progressive Disclosure Architecture Preservation

**Critical**: Maintain 90% speed boost with Git+Meta enhancements

```python
# Enhanced _store_vectors preserves progressive disclosure
def _enhanced_store_vectors_with_progressive_disclosure(self, collection_name: str,
                                                       entities: List[Entity],
                                                       relations: List[Relation],
                                                       implementation_chunks: List[EntityChunk]) -> bool:
    """Git+Meta efficiency with progressive disclosure preservation"""

    # Phase 1: Metadata chunks (fast search) - Enhanced with content hashing
    metadata_chunks_to_embed = []
    metadata_chunks_to_skip = []

    for entity in entities:
        metadata_chunk = entity.create_metadata_chunk()
        content_hash = metadata_chunk["content_hash"]

        # Preserve has_implementation flag for progressive disclosure
        metadata_chunk["has_implementation"] = bool(implementation_chunks and
                                                   any(ic.entity_name == entity.name for ic in implementation_chunks))

        if self.vector_store.check_content_exists(collection_name, content_hash):
            metadata_chunks_to_skip.append(metadata_chunk)
        else:
            metadata_chunks_to_embed.append(metadata_chunk)

    # Phase 2: Implementation chunks (on-demand) - Enhanced with content hashing
    impl_chunks_to_embed = []
    impl_chunks_to_skip = []

    for impl_chunk in implementation_chunks:
        if impl_chunk.content_hash and self.vector_store.check_content_exists(collection_name, impl_chunk.content_hash):
            impl_chunks_to_skip.append(impl_chunk)
        else:
            impl_chunks_to_embed.append(impl_chunk)

    # Preserve progressive disclosure performance metrics
    efficiency_metrics = {
        "metadata_skipped": len(metadata_chunks_to_skip),
        "implementation_skipped": len(impl_chunks_to_skip),
        "total_chunks": len(entities) + len(implementation_chunks),
        "progressive_disclosure_preserved": True,
        "speed_boost_maintained": True
    }

    # Log efficiency gains
    total_skipped = len(metadata_chunks_to_skip) + len(impl_chunks_to_skip)
    total_chunks = len(entities) + len(implementation_chunks)

    if total_skipped > 0:
        savings_percent = (total_skipped / total_chunks) * 100
        logger.info(f"🚀 Progressive Disclosure + Git+Meta: {savings_percent:.1f}% efficiency gain")
        logger.info(f"📊 Metadata: {len(metadata_chunks_to_skip)}/{len(entities)} skipped")
        logger.info(f"📊 Implementation: {len(impl_chunks_to_skip)}/{len(implementation_chunks)} skipped")

    # Embed only new content while preserving chunk separation
    all_chunks_to_embed = metadata_chunks_to_embed + impl_chunks_to_embed

    if all_chunks_to_embed:
        # Batch embed with chunk type awareness
        texts = [chunk.get("content", "") for chunk in all_chunks_to_embed]
        embeddings = self.embedder.embed_batch(texts)

        # Store with proper chunk type tagging
        points = []
        for chunk, embedding in zip(all_chunks_to_embed, embeddings):
            point = self.vector_store.create_chunk_point(
                chunk_id=chunk["id"],
                vector=embedding,
                chunk_data=chunk  # Includes chunk_type, has_implementation, content_hash
            )
            points.append(point)

        self.vector_store.batch_upsert(collection_name, points)

    return True

### 10. MCP Server Updates for Hash-Based Storage

**Minor Schema Updates**: Add content_hash support to MCP server interfaces

```typescript
// In mcp-qdrant-memory/src/types.ts
export interface EntityChunk extends Record<string, unknown> {
  id: string;
  entity_name: string;
  entity_type: string;
  chunk_type: 'metadata' | 'implementation';
  content: string;
  content_hash?: string;  // NEW: Optional for backward compatibility
  file_path?: string;
  line_number?: number;
  has_implementation?: boolean;
  semantic_metadata?: Record<string, any>;  // For implementation chunks
}

export interface ChunkPayload {
  entity_name: string;
  entity_type: string;
  chunk_type: 'metadata' | 'implementation';
  content: string;
  content_hash?: string;  // NEW: Optional hash field
  file_path?: string;
  line_number?: number;
  end_line_number?: number;
  has_implementation?: boolean;
  semantic_metadata?: Record<string, any>;
}

// In mcp-qdrant-memory/src/persistence/qdrant.ts
export class QdrantPersistence {
  // Enhanced search to handle content_hash filtering
  async searchSimilar(
    query: string,
    options: {
      limit?: number;
      entityTypes?: string[];
      contentHashFilter?: string;  // NEW: Optional hash filtering
    } = {}
  ): Promise<SearchResult[]> {

    const filters: any[] = [];

    // Existing filters...
    if (options.entityTypes?.length) {
      filters.push({
        key: "entity_type",
        match: { any: options.entityTypes }
      });
    }

    // NEW: Content hash filtering
    if (options.contentHashFilter) {
      filters.push({
        key: "content_hash",
        match: { value: options.contentHashFilter }
      });
    }

    // Rest of search logic...
  }

  // Enhanced storage to preserve content_hash
  async storeChunk(chunk: EntityChunk): Promise<void> {
    const payload: ChunkPayload = {
      entity_name: chunk.entity_name,
      entity_type: chunk.entity_type,
      chunk_type: chunk.chunk_type,
      content: chunk.content,
      content_hash: chunk.content_hash,  // NEW: Preserve hash
      file_path: chunk.file_path,
      line_number: chunk.line_number,
      has_implementation: chunk.has_implementation,
      semantic_metadata: chunk.semantic_metadata
    };

    // Store with content_hash in payload
    await this.client.upsert(this.collectionName, {
      wait: true,
      points: [{
        id: chunk.id,
        vector: chunk.vector,
        payload: payload
      }]
    });
  }
}
```

**Enhanced _store_vectors with Relations Optimization**:

```python
def _store_vectors(self, collection_name: str, entities: List[Entity],
                   relations: List[Relation], implementation_chunks: List[EntityChunk],
                   changed_entity_ids: Set[str] = None) -> bool:  # NEW parameter
    """Store entities and relations with smart deduplication"""

    # Phase 1: Entity processing (as before)
    entity_hashes = {}
    chunks_to_embed = []

    for entity in entities:
        chunk = EntityChunk.create_metadata_chunk(entity)
        content_hash = chunk["content_hash"]

        if self.vector_store.check_content_exists(collection_name, content_hash):
            chunks_to_skip.append((chunk, "exists"))
        else:
            chunks_to_embed.append(chunk)
            if changed_entity_ids is not None:
                changed_entity_ids.add(entity.id)

    # Phase 2: Smart Relations Processing (NEW)
    if changed_entity_ids:
        relations_processor = SmartRelationsProcessor()

        # Split relations based on entity involvement
        relations_to_embed, relations_unchanged = relations_processor.filter_relations_for_changes(
            relations, changed_entity_ids
        )

        # Get existing hashes for unchanged relations
        existing_relation_hashes = relations_processor.get_existing_relations_hashes(
            collection_name, self.current_file_path
        )

        # Only embed relations involving changed entities
        if relations_to_embed:
            logger.info(f"📊 Relations: {len(relations_to_embed)} to embed, "
                       f"{len(relations_unchanged)} unchanged (saved {len(relations_unchanged)} embeddings)")

        relations_to_process = relations_to_embed
    else:
        # Fallback: process all relations (initial indexing)
        relations_to_process = relations

    # Phase 3: Embed and store (modified to use filtered relations)
    if relations_to_process:
        # Apply existing deduplication
        seen_relation_keys = set()
        unique_relations = []

        for relation in relations_to_process:
            relation_chunk = RelationChunk.from_relation(relation)
            if relation_chunk.id not in seen_relation_keys:
                seen_relation_keys.add(relation_chunk.id)
                unique_relations.append(relation)

        # Generate embeddings only for unique changed relations
        relation_texts = [self._relation_to_text(r) for r in unique_relations]
        relation_embeddings = self.embedder.embed_batch(relation_texts)

        # Create and store relation points
        relation_points = []
        for relation, embedding in zip(unique_relations, relation_embeddings):
            relation_chunk = RelationChunk.from_relation(relation)
            relation_chunk.content_hash = ContentHashMixin.compute_content_hash(relation_chunk.content)

            point = self.vector_store.create_relation_chunk_point(
                chunk_id=relation_chunk.id,
                vector=embedding,
                relation_data=relation_chunk.to_vector_payload()
            )
            relation_points.append(point)

        self.vector_store.batch_upsert(collection_name, relation_points)

    return True
```

## Implementation Phases

### Phase 1: Core Infrastructure (Day 1-2)
1. Implement `ContentHashMixin` in `storage/qdrant.py`
2. Add `content_hash` field to both metadata and implementation `EntityChunk` creation
3. Update Qdrant schema to include content_hash in payload
4. Create `diff_layers.py` module with basic classes
5. **NEW**: Implement `EnhancedOrphanCleanup` class for hash-based orphaning

### Phase 2: Storage Optimization (Day 3-4)
1. Modify `_store_vectors` to check content hashes for both chunk types
2. Implement `check_content_exists` method
3. Add hash-based skip logic for embeddings
4. **NEW**: Integrate progressive disclosure preservation
5. **NEW**: Add orphaned relations cleanup integration

### Phase 3: Smart Relations Integration (Day 5)
1. **NEW**: Implement `SmartRelationsProcessor` class
2. **NEW**: Add `changed_entity_ids` parameter to `_store_vectors`
3. **NEW**: Add relations filtering based on entity involvement
4. **NEW**: Integrate with existing relation deduplication
5. Update relation hash storage and checking

### Phase 4: Watcher & Event Integration (Day 6)
1. **NEW**: Modify AsyncDebouncer callback integration
2. **NEW**: Add debouncer setup for Git+Meta approach
3. Update `run_indexing_with_specific_files` with event awareness
4. Remove pre-cleanup for modifications
5. Test event routing with relations

### Phase 5: Batch Processing & Streaming (Day 7)
1. **NEW**: Enhance `_create_batch_callback` with Git+Meta efficiency
2. **NEW**: Add streaming response efficiency metrics
3. **NEW**: Ensure batch processing compatibility
4. Test streaming workflows with content deduplication

### Phase 6: MCP Server Integration (Day 8)
1. **NEW**: Update TypeScript interfaces for content_hash support
2. **NEW**: Add optional hash filtering to search functions
3. **NEW**: Ensure backward compatibility
4. Test MCP server with new hash-based storage

### Phase 7: Testing & Validation (Day 9-10)
1. Unit tests for content hashing and relations filtering
2. Integration tests for deduplication across all chunk types
3. Performance benchmarks including relations and progressive disclosure
4. **NEW**: Watcher integration testing with debouncing
5. **NEW**: Batch processing efficiency validation
6. **NEW**: MCP server compatibility testing
7. Edge case handling and orphan cleanup validation

## Test Strategy

### Unit Tests

```python
# tests/unit/test_content_hash.py
def test_content_hash_consistency():
    """Same content always produces same hash"""
    content = "def hello(): return 'world'"
    hash1 = ContentHashMixin.compute_content_hash(content)
    hash2 = ContentHashMixin.compute_content_hash(content)
    assert hash1 == hash2

def test_content_hash_uniqueness():
    """Different content produces different hashes"""
    content1 = "def hello(): return 'world'"
    content2 = "def hello(): return 'universe'"
    hash1 = ContentHashMixin.compute_content_hash(content1)
    hash2 = ContentHashMixin.compute_content_hash(content2)
    assert hash1 != hash2

# tests/unit/test_diff_sketch.py
def test_diff_sketch_detection():
    """Correctly identifies added/removed/modified/unchanged"""
    old_entities = [
        Entity(id="func1", content="old content"),
        Entity(id="func2", content="same content"),
        Entity(id="func3", content="to be removed")
    ]
    new_entities = [
        Entity(id="func1", content="new content"),  # modified
        Entity(id="func2", content="same content"), # unchanged
        Entity(id="func4", content="new function")  # added
    ]

    sketch = DiffLayerManager().create_diff_sketch(old_entities, new_entities)
    assert sketch.added_entities == ["func4"]
    assert sketch.removed_entities == ["func3"]
    assert sketch.modified_entities == ["func1"]
    assert sketch.unchanged_entities == ["func2"]
```

### Integration Tests

```python
# tests/integration/test_incremental_efficiency.py
def test_single_function_change_efficiency():
    """Changing one function should only embed that function"""
    # Create test file with 10 functions
    test_file = create_test_file_with_functions(10)

    # Initial indexing
    result1 = indexer.index_single_file(test_file)
    assert result1.embeddings_created == 10

    # Modify one function
    modify_function_in_file(test_file, "func5", "new implementation")

    # Re-index with new system
    with patch.object(indexer, '_store_vectors', wraps=indexer._store_vectors) as mock:
        result2 = indexer.index_single_file(test_file)

        # Should only embed the changed function
        assert result2.embeddings_created == 1
        assert result2.embeddings_skipped == 9
```

### Performance Benchmarks

```python
# tests/performance/test_embedding_savings.py
def benchmark_embedding_costs():
    """Measure API cost savings with new approach"""
    scenarios = [
        ("Single function change in 40-function file", 1, 40),
        ("Two functions changed in 100-function file", 2, 100),
        ("Half functions changed in 20-function file", 10, 20)
    ]

    for scenario, changed, total in scenarios:
        old_cost = calculate_embedding_cost(total)  # All re-embedded
        new_cost = calculate_embedding_cost(changed)  # Only changed
        savings = (1 - new_cost/old_cost) * 100

        print(f"{scenario}: {savings:.1f}% cost reduction")
```

### Manual Testing Checklist

1. **File Modification Flow**
   - [ ] Create file with 10 functions
   - [ ] Index file (verify 10 embeddings created)
   - [ ] Modify 1 function
   - [ ] Re-index (verify only 1 embedding created)
   - [ ] Check all 10 functions still searchable

2. **Deletion Handling**
   - [ ] Delete file
   - [ ] Verify entities removed
   - [ ] No phantom deletions

3. **Large File Performance**
   - [ ] Test with 100+ entity file
   - [ ] Change single entity
   - [ ] Measure time difference

## Migration Considerations

1. **Existing Data**: Current collections lack content_hash field
   - Option A: Re-index all projects (clean start)
   - Option B: Add migration script to compute hashes

2. **Backward Compatibility**: MCP server needs minor updates
   - Add content_hash to payload handling
   - No breaking changes to API

3. **Rollback Plan**: Feature flag for new behavior
   ```python
   USE_CONTENT_DEDUP = os.getenv("CLAUDE_INDEXER_CONTENT_DEDUP", "true") == "true"
   ```

## Success Metrics

1. **Embedding Reduction**: 90%+ fewer embeddings for typical file edits (entities + relations)
2. **Relations Optimization**: 95%+ fewer relation embeddings for single entity changes
3. **Cost Savings**: 85%+ reduction in API costs for modifications
4. **Performance**: <5 seconds for single function change (vs 30+ seconds currently)
5. **Accuracy**: Zero functional regression in search quality

### Relations Optimization Examples
```python
# File with 40 entities, 200 relations
# Change 1 function that calls 3 other functions

Old approach:
- 200 relations re-embedded = 200 API calls
- All entity relations processed

New approach:
- 4 relations re-embedded (1 changed function + 3 calls)
- 196 relations skipped
- 98% relations embedding reduction
```

## Summary

This comprehensive implementation solves the core inefficiency by:

### Core Improvements
1. **Content-addressable storage**: Only new content gets embedded (90%+ savings)
2. **Event-aware processing**: No phantom deletions
3. **Immutable diff layers**: Track changes efficiently
4. **Smart relations processing**: Only re-embed relations touching changed entities (95%+ savings)

### Architecture Preservation
5. **Progressive disclosure maintained**: 90% speed boost preserved with dual-chunk hashing
6. **Watcher integration**: AsyncDebouncer works with Git+Meta efficiency
7. **Batch processing**: Streaming callbacks enhanced with deduplication
8. **Orphaned cleanup**: Extended to handle hash-based orphaning scenarios

### Technical Integration
9. **MCP server compatibility**: Backward-compatible schema updates
10. **Comprehensive testing**: All components validated together

### Implementation Scope
- **Original Plan**: 5 days
- **Enhanced Plan**: 10 days (doubled for missing components)
- **Files Modified**: 8-10 files across Python indexer and Node.js MCP server
- **New Code**: ~800-1000 lines
- **Risk Level**: Medium (comprehensive but builds on existing patterns)

The solution maintains all current functionality while dramatically improving efficiency for the common case of small file modifications, with comprehensive integration across all system components.
