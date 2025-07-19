# Layer 2: Orphaned Relation Cleanup Implementation Plan

## Overview
Layer 2 implements search-based orphan detection to find and remove relations that reference deleted entities. This complements the existing entity deletion in `_handle_deleted_files()`.

## Problem Statement
When files are deleted:
- ✅ Current: Entities with matching `file_path` are removed
- ❌ Missing: Relations pointing to/from deleted entities remain orphaned
- 🎯 Goal: Find and remove relations where `from` or `to` references non-existent entities

## Design Principles
1. **No Duplication**: Reuse existing QdrantStore methods and patterns
2. **Elegant Integration**: Extend `_handle_deleted_files()` without major refactoring
3. **Performance**: Use efficient bulk operations via scroll/batch delete
4. **Observability**: Verbose logging shows what relations are found/deleted
5. **Testability**: Add comprehensive tests for orphan scenarios

## Implementation Architecture

### 1. Core Algorithm
```python
def _cleanup_orphaned_relations(self, collection_name: str, verbose: bool = False) -> int:
    """Clean up relations that reference non-existent entities.

    Returns:
        Number of orphaned relations deleted
    """
    # Step 1: Get all entity names currently in collection
    existing_entities = self._get_all_entity_names(collection_name)

    # Step 2: Find all relations
    orphaned_relations = []
    all_relations = self._get_all_relations(collection_name)

    # Step 3: Check each relation for orphaned references
    for relation in all_relations:
        from_entity = relation.payload.get('from', '')
        to_entity = relation.payload.get('to', '')

        if from_entity not in existing_entities or to_entity not in existing_entities:
            orphaned_relations.append(relation)
            if verbose:
                self.logger.info(f"🔍 Found orphaned relation: {from_entity} -> {to_entity}")

    # Step 4: Batch delete orphaned relations
    if orphaned_relations:
        relation_ids = [r.id for r in orphaned_relations]
        self.vector_store.delete_points(collection_name, relation_ids)

        if verbose:
            self.logger.info(f"🗑️  Deleted {len(orphaned_relations)} orphaned relations")

    return len(orphaned_relations)
```

### 2. Helper Methods

#### Get All Entity Names
```python
def _get_all_entity_names(self, collection_name: str) -> Set[str]:
    """Get all entity names from the collection."""
    entity_names = set()

    # Use scroll to get all entities (type != "relation")
    scroll_result = self.vector_store.client.scroll(
        collection_name=collection_name,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="type",
                    match=models.MatchValue(value="relation"),
                    range=None
                )
            ],
            must_not=[
                models.FieldCondition(
                    key="type",
                    match=models.MatchValue(value="relation")
                )
            ]
        ),
        limit=1000,
        with_payload=True,
        with_vectors=False
    )

    for point in scroll_result[0]:
        name = point.payload.get('name', '')
        if name:
            entity_names.add(name)

    return entity_names
```

#### Get All Relations
```python
def _get_all_relations(self, collection_name: str) -> List[models.ScoredPoint]:
    """Get all relations from the collection."""
    relations = []

    # Use scroll to get all relations
    scroll_result = self.vector_store.client.scroll(
        collection_name=collection_name,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="type",
                    match=models.MatchValue(value="relation")
                )
            ]
        ),
        limit=1000,
        with_payload=True,
        with_vectors=False
    )

    relations.extend(scroll_result[0])
    return relations
```

### 3. Integration with _handle_deleted_files

Modify the existing method to call orphan cleanup after entity deletion:

```python
def _handle_deleted_files(self, deleted_files: List[Path], collection_name: str,
                         verbose: bool = False) -> Tuple[int, int]:
    """Handle file deletions by removing entities and orphaned relations."""

    # Existing entity deletion code...
    total_deleted = len(deleted_entity_ids)

    # NEW: Clean up orphaned relations
    orphaned_deleted = 0
    if total_deleted > 0:
        if verbose:
            self.logger.info("🔍 Searching for orphaned relations...")

        orphaned_deleted = self._cleanup_orphaned_relations(collection_name, verbose)

    return total_deleted, orphaned_deleted
```

### 4. Verbose Logging Examples

When `--verbose` is enabled, users will see:

```
🗑️  Handling 2 deleted files...
   Deleted: src/old_module.py
   Deleted: tests/test_old.py
🔍 Searching for entities to delete...
   Found 5 entities to delete
🔍 Searching for orphaned relations...
🔍 Found orphaned relation: src/old_module.py -> pandas
🔍 Found orphaned relation: tests/test_old.py -> src/old_module.py
🔍 Found orphaned relation: MyOldClass -> helper_function
🗑️  Deleted 3 orphaned relations
✅ Cleanup complete: 5 entities, 3 relations removed
```

## Testing Strategy

### 1. Unit Tests (`test_orphan_cleanup.py`)

```python
def test_cleanup_orphaned_relations_basic():
    """Test basic orphan detection and cleanup."""
    # Setup: Create entities and relations
    # Delete some entities
    # Verify orphaned relations are detected
    # Verify cleanup removes only orphaned relations

def test_cleanup_preserves_valid_relations():
    """Test that valid relations are not deleted."""
    # Setup: Create interconnected entities
    # Delete one entity
    # Verify only relations touching deleted entity are removed

def test_cleanup_cross_file_relations():
    """Test orphan cleanup for cross-file relations."""
    # Create entities in multiple files
    # Create cross-file relations
    # Delete one file
    # Verify cross-file orphans are cleaned
```

### 2. Integration Tests

```python
def test_incremental_update_with_orphan_cleanup():
    """Test full incremental update flow with orphan cleanup."""
    # Index project
    # Modify files to remove entities
    # Run incremental update
    # Verify orphaned relations are cleaned

def test_verbose_logging_output():
    """Test verbose output for orphan detection."""
    # Run with verbose=True
    # Capture logs
    # Verify detailed orphan information is logged
```

### 3. Performance Tests

```python
def test_orphan_cleanup_performance():
    """Test performance with large number of relations."""
    # Create 10,000 entities and 50,000 relations
    # Delete 1,000 entities
    # Measure orphan cleanup time
    # Should complete in < 5 seconds
```

## Rollout Plan

### Phase 1: Core Implementation
1. Implement `_cleanup_orphaned_relations()` method
2. Implement helper methods for entity/relation retrieval
3. Update `_handle_deleted_files()` to call cleanup
4. Add basic unit tests

### Phase 2: Testing & Refinement
1. Add comprehensive test coverage
2. Test with real projects
3. Optimize performance for large collections
4. Add detailed logging

### Phase 3: Future Enhancements
1. Consider caching entity names for performance
2. Add metrics for orphan detection rate
3. Optional: Implement periodic background cleanup
4. Optional: Add CLI command for manual orphan cleanup

## Success Metrics
- ✅ All orphaned relations detected and removed
- ✅ No false positives (valid relations preserved)
- ✅ Performance: <5s for collections with 100k+ points
- ✅ Clear verbose output showing what was cleaned
- ✅ 100% test coverage for orphan scenarios

## Notes
- Layer 2 is stateless - no persistence needed
- Works independently of Layer 1 (enhanced state tracking)
- Can be deployed immediately without breaking changes
- Complements existing entity deletion perfectly
