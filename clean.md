# Memory Cleanup System Implementation Plan

## Executive Summary

This plan outlines the implementation of an intelligent memory cleanup system for Claude Code Memory. The system targets manual entries only (debugging patterns, implementation patterns, knowledge insights) while preserving auto-indexed code. It will automatically identify and resolve conflicting manual patterns, score quality, merge duplicates, and maintain temporal relevance.

**Goal**: Transform from reactive manual cleanup to proactive automated memory evolution with LLM-driven intelligence.

## Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  Cleanup CLI    │───►│ Cleanup Pipeline │───►│  Quality Scorer │
│  (Scheduler)    │    │   (Orchestrator) │    │   (LLM-based)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                      │                        │
         ▼                      ▼                        ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  QdrantStore    │◄───│ Similarity Engine│◄───│ Backup Manager  │
│  (Embeddings)   │    │   (Clustering)   │    │   (Rollback)    │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## Critical Detection Logic: Dynamic Field-Based Approach

### Why Dynamic Detection is Essential

**The system MUST use field-based detection, NOT entity type lists!**

Traditional approaches fail because:
1. **Entity types are not exclusive** - The same entityType (e.g., `documentation`) can be both manual and auto-indexed
2. **New entity types emerge** - Hardcoded lists become outdated as users create new categories
3. **Context determines type** - The presence/absence of automation fields defines whether an entry is manual or auto-indexed

### Core Detection Logic: Field-Based Identification

The system identifies manual vs auto-indexed entries by examining their field structure:

```python
def is_manual_entry(payload):
    """
    Dynamic detection based on field presence, not entity type.
    This approach automatically adapts to ANY new entity type.
    """
    # Auto-indexed entries ALWAYS contain automation fields
    # These fields are injected by the indexing pipeline
    automation_fields = {
        'file_path',      # Source file location
        'line_number',    # Line in source file
        'ast_data',       # Abstract syntax tree data
        'signature',      # Function/class signature
        'docstring',      # Extracted documentation
        'full_name',      # Fully qualified name
        'ast_type',       # AST node type
        'start_line',     # Code block start
        'end_line',       # Code block end
        'source_hash',    # Content hash
        'parsed_at',      # Indexing timestamp
        'is_chunk',       # Chunk indicator
        'chunk_index'     # Chunk sequence
    }
    
    # If ANY automation field exists, it's auto-indexed
    if any(field in payload for field in automation_fields):
        return False
    
    # Absence of automation fields = manual entry
    # This works for ANY entity type, existing or future
    return True
```

### Dynamic Entity Type Examples

The beauty of field-based detection is that it works for ANY entity type:

```python
# Example 1: 'documentation' entity type (can be EITHER manual or auto)

# Auto-indexed documentation (from README.md)
{
    "entityType": "documentation",
    "name": "Project Setup Guide",
    "content": "Installation instructions...",
    "file_path": "/project/README.md",    # ← Automation field!
    "line_number": 15,                     # ← Automation field!
    "parsed_at": "2024-01-01T00:00:00Z"   # ← Automation field!
}
# Result: is_manual_entry() = FALSE (auto-indexed)

# Manual documentation (user insight)
{
    "entityType": "documentation", 
    "name": "Architecture Decision",
    "content": "We chose microservices because..."
}
# Result: is_manual_entry() = TRUE (manual)

# Example 2: Future entity type not yet invented
{
    "entityType": "quantum_pattern",  # New type!
    "name": "Entanglement Strategy",
    "content": "Novel approach to state management..."
}
# Result: is_manual_entry() = TRUE (no automation fields)

# Same new type, but auto-indexed
{
    "entityType": "quantum_pattern",
    "name": "QuantumClass",
    "content": "class implementation...",
    "file_path": "/src/quantum.py",    # ← Automation field!
    "ast_type": "ClassDef"             # ← Automation field!
}
# Result: is_manual_entry() = FALSE (has automation fields)
```

### Advantages of Dynamic Detection

1. **Future-Proof**: New entity types are automatically classified correctly
2. **No Maintenance**: No need to update lists when adding new categories
3. **Accurate**: Based on actual data structure, not assumptions
4. **Flexible**: Users can create any entity type name they want
5. **Consistent**: Same logic works across all components

## Core Components

### 1. Memory Cleanup Pipeline (`claude_indexer/cleanup/pipeline.py`)

```python
class MemoryCleanupPipeline:
    """Main orchestrator for memory cleanup operations"""
    
    def __init__(self, store: QdrantStore, llm_scorer: QualityScorer):
        self.store = store
        self.scorer = llm_scorer
        self.similarity_threshold = 0.85
        self.quality_weights = {
            "accuracy": 0.3,
            "completeness": 0.25,
            "specificity": 0.2,
            "reusability": 0.15,
            "recency": 0.1
        }
    
    async def run_cleanup(self, collection_name: str, dry_run: bool = True):
        """Execute cleanup pipeline for manual entries only"""
        # 1. Create backup
        # 2. Filter manual entries using field-based detection (NOT entityType)
        #    - Include: Entries WITHOUT file_path, line_number, ast_data fields
        #    - Exclude: ALL entries with automation fields (even if entityType='documentation')
        # 3. Analyze manual patterns for duplicates/conflicts
        # 4. Score quality of manual insights
        # 5. Execute cleanup on manual entries only
        # 6. Validate results
```

### 2. Similarity Clustering Engine (`claude_indexer/cleanup/clustering.py`)

```python
class SimilarityClusterer:
    """Groups similar manual entries using existing embeddings"""
    
    def cluster_manual_patterns(self, patterns: List[Dict], threshold: float = 0.85) -> List[List[Dict]]:
        """
        Cluster manual entries by similarity using dynamic field-based detection.
        
        This method is entity-type agnostic and works with ANY entity type,
        present or future, by examining field structure rather than type names.
        
        Process:
        1. Filter entries using is_manual_entry() - excludes ANY entry with automation fields
        2. Group remaining manual entries by cosine similarity > threshold  
        3. Return clusters for duplicate/conflict detection
        
        The clustering works identically whether the entity type is:
        - Traditional: debugging_pattern, implementation_pattern, etc.
        - Domain-specific: ml_experiment, security_vulnerability, etc.
        - Future types: Any new category users create
        """
        manual_entries = [p for p in patterns if is_manual_entry(p['payload'])]
        # Cluster logic using embeddings...
```

**Dynamic Implementation Benefits**:
- **No hardcoded lists**: Works with ANY entity type based on field structure
- **Automatic adaptation**: New entity types are handled without code changes
- **Consistent behavior**: Same detection logic as all other components
- **Zero maintenance**: No need to update when users create new categories

### 3. LLM Quality Scorer (`claude_indexer/cleanup/scorer.py`)

```python
class QualityScorer:
    """LLM-based quality assessment using GPT-4.1-mini for patterns"""
    
    def __init__(self):
        self.model = "gpt-4.1-mini"  # 83% cheaper than GPT-4o, 1M context window
        self.client = openai.OpenAI()
    
    async def score_manual_entry(self, entry: Dict) -> QualityScore:
        """
        Score entries using dynamic field-based detection.
        Works with ANY entity type - no hardcoded lists needed.
        """
        # Dynamic detection - works for any current or future entity type
        if not is_manual_entry(entry['payload']):
            return None  # Skip auto-indexed entries
            
        # Score any manual entry regardless of its entityType
        prompt = self._build_scoring_prompt(entry)
        scores = await self._call_llm(prompt)
        return QualityScore(
            accuracy=scores['accuracy'],
            completeness=scores['completeness'],
            specificity=scores['specificity'],
            reusability=scores['reusability'],
            recency=scores['recency'],
            overall=self._calculate_weighted_score(scores)
        )
    
    def _build_scoring_prompt(self, entry: Dict) -> str:
        """Generate scoring prompt that works for ANY entity type"""
        return f"""
        Evaluate this {entry['entityType']} entry for quality.
        Content: {entry['content']}
        
        Score on standard dimensions regardless of type...
        """
```

**Scoring Dimensions**:
- **Accuracy** (0.3): Is the solution correct?
- **Completeness** (0.25): Does it cover all aspects?
- **Specificity** (0.2): How well-defined is the problem?
- **Reusability** (0.15): Can it be applied elsewhere?
- **Recency** (0.1): Is it using current best practices?

### 4. Conflict Resolution Engine (`claude_indexer/cleanup/resolver.py`)

```python
class ConflictResolver:
    """Resolve contradictory patterns intelligently"""
    
    async def resolve_conflicts(self, cluster: List[Dict]) -> ResolutionAction:
        """Determine how to handle conflicting patterns"""
        if self._are_version_specific(cluster):
            return self._create_versioned_hierarchy(cluster)
        elif self._quality_difference_significant(cluster):
            return self._keep_highest_quality(cluster)
        else:
            return self._merge_compatible_entries(cluster)
```

**Resolution Strategies**:
1. **Version-specific**: Create hierarchy (Python 2.7 vs 3.12)
2. **Platform-specific**: Separate by OS/environment
3. **Quality-based**: Keep best, delete inferior
4. **Merge**: Combine complementary information

### 5. Automated Action Executor (`claude_indexer/cleanup/executor.py`)

```python
class CleanupExecutor:
    """Execute cleanup actions with safety limits"""
    
    def execute_actions(self, actions: List[CleanupAction], safety_config: SafetyConfig):
        """Apply cleanup actions with rollback capability"""
        # Safety checks:
        # - Max 15% deletion per run
        # - Backup before execution
        # - Rollback triggers
```

**Action Types** (Dynamic Detection):
- **Delete**: Remove entries with quality < 0.3 (only if is_manual_entry() = true)
- **Merge**: Combine similar entries with similarity > 0.8 (manual entries only)
- **Update**: Refresh insights with current best practices (manual entries only)
- **Preserve**: ANY entry with automation fields remains untouched

**Dynamic Protection**:
- The system automatically preserves ALL auto-indexed entries
- No need to maintain lists of "protected" entity types
- Works with future entity types without modification
- Example: If tomorrow a new `quantum_state` entity type is auto-indexed with file_path, it's automatically protected

### 6. Safety and Rollback Manager (`claude_indexer/cleanup/safety.py`)

```python
class SafetyManager:
    """Ensure cleanup operations are safe and reversible"""
    
    def create_backup(self, collection_name: str) -> BackupHandle:
        """Create point-in-time backup before cleanup"""
        
    def validate_cleanup(self, metrics: CleanupMetrics) -> bool:
        """Check if cleanup was successful"""
        # Rollback if search accuracy drops > 20%
        # Rollback if critical patterns missing
```

## Integration Points

### 1. Reuse Existing Components

```python
# Leverage existing QdrantStore for all vector operations
from claude_indexer.storage.qdrant import QdrantStore

# Use existing clear_collection logic for manual preservation
# Pattern: preserve_manual=True logic already implemented

# Build on _cleanup_orphaned_relations pattern
# Similar batch processing and safety checks
```

### 2. New CLI Commands

```bash
# Add to claude_indexer/cli_full.py
@cli.command()
@click.option('--collection', '-c', required=True)
@click.option('--dry-run/--execute', default=True)
@click.option('--mode', type=click.Choice(['analysis', 'full']), default='analysis')
def cleanup(collection: str, dry_run: bool, mode: str):
    """Run memory cleanup pipeline"""
```

### 3. Configuration

```python
# claude_indexer/config/cleanup_config.py
class CleanupConfig:
    similarity_threshold: float = 0.85
    quality_thresholds = {
        "delete": 0.3,
        "merge": 0.8
    }
    safety_limits = {
        "max_deletion_percentage": 15,
        "rollback_threshold": 0.2
    }
```

## Implementation Phases

### Phase 1: Foundation (Week 1-2)
1. **Similarity Clustering**
   - Implement `SimilarityClusterer` using existing embeddings
   - Test with known duplicate patterns
   - Validate clustering accuracy

2. **Basic Quality Scoring**
   - Implement `QualityScorer` with GPT-4.1-mini integration
   - Create scoring prompts and validation
   - Test on sample patterns

### Phase 2: Core Logic (Week 3-4)
1. **Conflict Resolution**
   - Implement conflict detection within clusters
   - Build resolution strategies
   - Test with real conflicting patterns

2. **Action Execution**
   - Implement safe deletion/merge/archive
   - Add progress tracking and logging
   - Test rollback mechanisms

### Phase 3: Production Ready (Week 5-6)
1. **Safety & Monitoring**
   - Implement backup/restore functionality
   - Add metrics collection
   - Build rollback triggers

2. **Automation & Scheduling**
   - Add cron job support
   - Implement incremental cleanup
   - Production deployment

## Testing Strategy

### Unit Tests
```python
# tests/unit/test_cleanup_pipeline.py
def test_similarity_clustering():
    """Test pattern clustering with known duplicates"""
    
def test_quality_scoring():
    """Test LLM scoring with mock responses"""
    
def test_conflict_resolution():
    """Test various conflict scenarios"""
```

### Integration Tests
```python
# tests/integration/test_cleanup_flow.py
def test_full_cleanup_pipeline():
    """Test end-to-end cleanup with real data"""
    
def test_rollback_on_failure():
    """Test automatic rollback triggers"""
```

### Performance Tests
- Cleanup time for 1000, 5000, 10000 patterns
- Memory usage during clustering
- Search accuracy before/after cleanup

## Key Design Decisions

### 1. Build on Existing Infrastructure
- Use QdrantStore's existing methods (no new storage layer)
- Leverage stored embeddings (no recalculation)
- Follow `_cleanup_orphaned_relations` pattern for safety
- Reuse `is_truly_manual_entry` detection logic from utils/manual_memory_backup.py

### 2. Progressive Disclosure Support
- Preserve metadata/implementation chunk separation
- Maintain has_implementation flags
- Keep entity relationships intact

### 3. Dynamic Entry Protection
- **Field-based detection** is the ONLY method used
- **No entity type lists** - system adapts automatically
- **Future-proof** - new entity types work without code changes
- **Examples of dynamic protection**:
  - `documentation` with file_path → Protected (auto-indexed)
  - `documentation` without file_path → Eligible for cleanup (manual)
  - `new_ml_pattern` with ast_data → Protected (auto-indexed)
  - `new_ml_pattern` without automation fields → Eligible for cleanup (manual)

### 4. Incremental Processing
- Process in batches to avoid memory issues
- Allow partial cleanup runs
- Support resumable operations

## Risks and Mitigations

### Risk 1: Over-aggressive Deletion
- **Mitigation**: Conservative thresholds, dry-run by default
- **Mitigation**: 15% max deletion limit per run
- **Mitigation**: Automatic rollback on quality degradation

### Risk 2: LLM API Costs
- **Mitigation**: Batch API calls for efficiency
- **Mitigation**: Cache quality scores for patterns
- **Mitigation**: Incremental processing to spread costs

### Risk 3: Breaking Existing Functionality
- **Mitigation**: Extensive testing before production
- **Mitigation**: Feature flag for gradual rollout
- **Mitigation**: Monitor search quality metrics

## Success Metrics

1. **Storage Efficiency**
   - Target: 15-25% reduction in pattern count
   - Measure: Collection size before/after

2. **Search Quality**
   - Target: <5% degradation in search accuracy
   - Measure: Precision/recall on test queries

3. **Conflict Resolution**
   - Target: 80% of conflicts auto-resolved
   - Measure: Manual intervention rate

4. **Performance**
   - Target: Process 10k patterns in <10 minutes
   - Measure: Cleanup execution time

## Future Enhancements

1. **Temporal Decay**
   - Add time-based relevance scoring
   - Implement gradual quality degradation
   - Auto-archive old patterns

2. **Learning from Feedback**
   - Track which patterns get used
   - Adjust quality scores based on usage
   - Build pattern evolution chains

3. **Cross-Collection Intelligence**
   - Share quality scores across projects
   - Build organization-wide pattern library
   - Collaborative conflict resolution

## Summary

This implementation plan provides a comprehensive, production-ready memory cleanup system that:

1. **Dynamic Field-Based Detection** - Identifies entries by presence/absence of automation fields, NOT by entity type
2. **Zero Maintenance** - No hardcoded entity type lists to update as new types are created
3. **Universal Application** - Works with ANY entity type (current or future) without code changes
4. **Automatic Protection** - ANY entry with automation fields is preserved, regardless of entityType
5. **Future-Proof Design** - New entity types are automatically handled correctly
6. **Safety First** - Multiple safeguards, dry-run by default, automatic rollback capabilities
7. **Intelligent Cleanup** - LLM-driven quality assessment for manual entries only

**Key Innovation**: The system uses the `is_manual_entry()` function as its core detection mechanism. This single function, based purely on field structure, enables the entire system to adapt dynamically to any entity type schema.

**Example of Dynamic Adaptation**:
```python
# Today: System handles known types
{"entityType": "debugging_pattern", ...}  # ✓ Works

# Tomorrow: User creates new type
{"entityType": "quantum_entanglement_strategy", ...}  # ✓ Still works!

# Future: AI creates types we haven't imagined
{"entityType": "neural_synthesis_pattern_v7", ...}  # ✓ Will work!
```

The system transforms memory management from a maintenance burden into a self-adapting, intelligent system that evolves with user needs without requiring code updates.