# read_graph Enhancement Plan: Making it Usable

## Problem Statement

Current `read_graph` returns 393k tokens, exceeding Claude Code's 25k limit, making it unusable. The tool needs smart filtering and summarization to provide valuable code intelligence within token constraints.

## Solution: Enhanced read_graph with Smart Filtering

### 1. Update Tool Schema

**File**: `src/index.ts` (tool definition ~line 332)

```typescript
{
  name: "read_graph",
  description: "Read filtered knowledge graph with smart summarization",
  inputSchema: {
    type: "object",
    properties: {
      mode: {
        type: "string",
        enum: ["smart", "entities", "relationships", "raw"],
        description: "smart: AI-optimized view (default), entities: filtered entities, relationships: connection focus, raw: full graph (may exceed limits)",
        default: "smart"
      },
      entityTypes: {
        type: "array",
        items: { type: "string" },
        description: "Filter specific entity types (e.g., ['class', 'function'])"
      },
      limit: {
        type: "number",
        description: "Max entities per type (default: 50)",
        default: 50
      }
    }
  }
}
```

### 2. Remove useQdrant Parameter

**Why**: Always needs to be true since JSON is empty. Simplify by removing.

**Files to update**:
- Remove `useQdrant` from tool schema
- Update handler to always use Qdrant
- Remove getGraph boolean parameter

### 3. Implement Smart Filtering in scrollAll

**File**: `src/persistence/qdrant.ts`

Add parameters to existing scrollAll:

```typescript
async scrollAll(options?: {
  entityTypes?: string[],
  limit?: number,
  mode?: 'smart' | 'entities' | 'relationships' | 'raw'
}): Promise<KnowledgeGraph | SmartGraph>
```

**Smart Mode Logic**:
1. **Project Structure**: All File/Directory entities (gives overview)
2. **Public API Surface**:
   - Functions without leading underscore
   - Classes without leading underscore
   - Methods named `__init__`, `__new__`
3. **Key Relationships**:
   - All "inherits" relations (class hierarchies)
   - External "imports" (dependencies)
   - "contains" for structure
4. **Priority Scoring**:
   - Has docstring: +10 points
   - Public name: +5 points
   - In main modules: +5 points
   - Referenced by many: +3 per reference

### 4. Smart Graph Response Structure

```typescript
interface SmartGraph {
  summary: {
    totalEntities: number,
    totalRelations: number,
    breakdown: Record<string, number>, // { "class": 45, "function": 198, ... }
    keyModules: string[], // Top-level directories/packages
    timestamp: string
  },
  structure: {
    // Hierarchical file tree with entity counts
    [path: string]: {
      type: 'file' | 'directory',
      entities: number,
      children?: Record<string, any>
    }
  },
  apiSurface: {
    classes: Array<{
      name: string,
      file: string,
      line: number,
      docstring?: string,
      methods: string[], // Just names
      inherits?: string[]
    }>,
    functions: Array<{
      name: string,
      file: string,
      line: number,
      signature?: string,
      docstring?: string
    }>
  },
  dependencies: {
    external: string[], // External package imports
    internal: Array<{ from: string, to: string }> // Key internal dependencies
  },
  relations: {
    inheritance: Array<{ from: string, to: string }>,
    keyUsages: Array<{ from: string, to: string, type: string }>
  }
}
```

### 5. Implementation Steps

#### Phase 1: Update Interfaces
1. Create SmartGraph interface
2. Update tool schema (remove useQdrant, add new params)
3. Update KnowledgeGraphManager.getGraph signature

#### Phase 2: Enhance scrollAll
1. Add filtering by entity type
2. Add limit per type
3. Implement smart mode selection logic
4. Create priority scoring for entities

#### Phase 3: Build Smart Response
1. Generate summary statistics
2. Build hierarchical file structure
3. Extract public API surface
4. Identify key relationships
5. Format response under 25k tokens

#### Phase 4: Update Handler
1. Remove useQdrant logic
2. Parse new parameters
3. Call enhanced scrollAll
4. Return appropriate response format

### 6. Testing Plan

#### Unit Tests

**Test 1: Smart Mode Token Limits**
- Create mock data with 1000+ entities
- Verify smart mode returns <25k tokens
- Check all sections populated correctly

**Test 2: Entity Type Filtering**
- Test filtering single type: `entityTypes: ['class']`
- Test multiple types: `entityTypes: ['class', 'function']`
- Verify only requested types returned

**Test 3: Limit Parameter**
- Test with limit: 10
- Verify max 10 entities per type
- Check priority scoring works

**Test 4: Mode Switching**
- Test all modes: smart, entities, relationships, raw
- Verify each returns expected format
- Check smart mode is default

**Test 5: Empty Collection Handling**
- Test with empty Qdrant collection
- Verify graceful empty response
- No errors thrown

#### Integration Tests

**Test 6: Real Data Test**
- Use actual memory-project data
- Verify smart response meaningful
- Check file structure accurate
- Validate API surface correct

**Test 7: Performance Test**
- Measure response time <5 seconds
- Check memory usage reasonable
- Verify pagination works efficiently

**Test 8: Backwards Compatibility**
- Old calls without parameters work
- Default to smart mode
- No breaking changes

### 7. Rollback Plan

Keep current implementation intact:
1. Enhance existing methods (don't replace)
2. Add new logic conditionally
3. Raw mode preserves original behavior
4. Can revert by changing defaults

### 8. Success Metrics

- ✅ read_graph returns usable data (<25k tokens)
- ✅ Smart mode provides immediate code understanding
- ✅ Filtering works for specific queries
- ✅ No performance degradation
- ✅ All tests pass
- ✅ Claude Code can effectively use the data

### 9. Code Quality Guidelines

- **No duplication**: Reuse existing validation functions
- **Clean code**: Single responsibility per method
- **Type safety**: Full TypeScript types
- **Error handling**: Graceful degradation
- **Documentation**: Clear parameter descriptions

### 10. Future Enhancements

- Caching for frequently accessed graphs
- Incremental updates instead of full reload
- Custom priority scoring per project
- Export to different formats (GraphQL, etc.)

This plan transforms the unusable 393k token response into an intelligent, filtered view that gives Claude Code exactly what it needs for effective source code understanding.
