# Get Implementation Scope Enhancement Plan

## Executive Summary

Enhance the `get_implementation` MCP tool to support semantic scope levels, enabling Claude Code to retrieve contextually related code beyond single entity boundaries. This addresses the need for logical code understanding without overwhelming context noise.

## Current State Analysis

### Existing Architecture
- **Function**: `get_implementation(entityName: string)` returns implementation chunks for exact entity match
- **Data Flow**: MCP Server → Qdrant persistence → Filter by entity_name + chunk_type:"implementation"
- **Semantic Metadata Available**:
  ```json
  {
    "calls": ["func1", "func2"],        // Functions called
    "imports_used": ["module1"],         // Import dependencies
    "exceptions_handled": ["ValueError"], // Exception types
    "complexity": 67,                    // Line count
    "inferred_types": []                 // Type hints from Jedi
  }
  ```

### Limitations
1. Only returns exact entity matches
2. No semantic grouping of related code
3. Underutilizes rich semantic metadata
4. No scope control for varying detail levels

## Proposed Enhancement

### API Design
```typescript
get_implementation(
  entityName: string,
  scope?: 'minimal' | 'logical' | 'dependencies' = 'minimal'
)
```

### Scope Definitions

#### 1. **Minimal Scope** (Default - Current Behavior)
- Returns: Only the requested entity's implementation
- Use Case: Focused code inspection
- Example: `get_implementation("parseAST")` → Just parseAST function

#### 2. **Logical Scope**
- Returns: Entity + helper functions/classes from same file
- Discovery Method:
  - Analyze `semantic_metadata.calls` for local function calls
  - Filter by same `file_path`
  - Include private helper functions (prefix: `_`)
- Example: `parseAST` + `_extract_nodes`, `_validate_syntax` (same file helpers)

#### 3. **Dependencies Scope**
- Returns: Entity + imported functions/classes it uses
- Discovery Method:
  - Parse `semantic_metadata.imports_used`
  - Follow `CALLS` relations in knowledge graph
  - Include external dependencies referenced
- Example: `parseAST` + `TreeSitter.parse`, `validate_ast` (cross-file dependencies)

## Implementation Plan

### Phase 1: Infrastructure Updates (Week 1)

#### 1.1 Update MCP Tool Schema
**File**: `mcp-qdrant-memory/src/index.ts`
```typescript
// Line 364-376 enhancement
{
  name: "get_implementation",
  description: "Retrieve implementation with semantic scope control",
  inputSchema: {
    type: "object",
    properties: {
      entityName: {
        type: "string",
        description: "Name of the entity to retrieve"
      },
      scope: {
        type: "string",
        enum: ["minimal", "logical", "dependencies"],
        default: "minimal",
        description: "Scope of related code to include"
      }
    },
    required: ["entityName"]
  }
}
```

#### 1.2 Enhance Validation
**File**: `mcp-qdrant-memory/src/validation.ts`
```typescript
export function validateGetImplementationRequest(args: Record<string, unknown>) {
  const entityName = args.entityName;
  if (!entityName || typeof entityName !== 'string') {
    throw new Error('entityName is required and must be a string');
  }

  const scope = args.scope || 'minimal';
  if (!['minimal', 'logical', 'dependencies'].includes(scope)) {
    throw new Error('Invalid scope. Must be: minimal, logical, or dependencies');
  }

  return { entityName, scope };
}
```

### Phase 2: Core Logic Implementation (Week 2)

#### 2.1 Enhance Qdrant Persistence
**File**: `mcp-qdrant-memory/src/persistence/qdrant.ts`

```typescript
async getImplementationChunks(
  entityName: string,
  scope: 'minimal' | 'logical' | 'dependencies' = 'minimal'
): Promise<SearchResult[]> {
  // Base implementation for minimal scope
  const baseResults = await this.getEntityImplementation(entityName);

  if (scope === 'minimal') return baseResults;

  // Extract semantic metadata for scope expansion
  const metadata = this.extractSemanticMetadata(baseResults);

  if (scope === 'logical') {
    return this.expandLogicalScope(baseResults, metadata);
  }

  if (scope === 'dependencies') {
    return this.expandDependencyScope(baseResults, metadata);
  }
}

private async expandLogicalScope(
  baseResults: SearchResult[],
  metadata: SemanticMetadata
): Promise<SearchResult[]> {
  const filePath = baseResults[0]?.data.file_path;
  const calledFunctions = metadata.calls || [];

  // Query for helper functions in same file
  const helperResults = await this.client.search(COLLECTION_NAME, {
    vector: new Array(this.vectorSize).fill(0),
    limit: 20,
    filter: {
      must: [
        { key: "file_path", match: { value: filePath } },
        { key: "chunk_type", match: { value: "implementation" } },
        { key: "entity_name", match: { any: calledFunctions } }
      ]
    }
  });

  return this.mergeAndDeduplicate([...baseResults, ...helperResults]);
}

private async expandDependencyScope(
  baseResults: SearchResult[],
  metadata: SemanticMetadata
): Promise<SearchResult[]> {
  const imports = metadata.imports_used || [];
  const calls = metadata.calls || [];

  // Query for imported dependencies
  const dependencyResults = await this.client.search(COLLECTION_NAME, {
    vector: new Array(this.vectorSize).fill(0),
    limit: 30,
    filter: {
      must: [
        { key: "chunk_type", match: { value: "implementation" } }
      ],
      should: [
        { key: "entity_name", match: { any: imports } },
        { key: "entity_name", match: { any: calls } }
      ]
    }
  });

  return this.mergeAndDeduplicate([...baseResults, ...dependencyResults]);
}
```

### Phase 3: Testing & Validation (Week 3)

#### 3.1 Unit Tests
**File**: `mcp-qdrant-memory/test/test-get-implementation-scope.js`

```javascript
describe('get_implementation scope tests', () => {
  it('should return only requested entity for minimal scope', async () => {
    const result = await mcp.get_implementation('parseAST', 'minimal');
    expect(result.length).toBe(1);
    expect(result[0].data.entity_name).toBe('parseAST');
  });

  it('should include helper functions for logical scope', async () => {
    const result = await mcp.get_implementation('parseAST', 'logical');
    const entityNames = result.map(r => r.data.entity_name);
    expect(entityNames).toContain('parseAST');
    expect(entityNames).toContain('_extract_nodes');
    // All should be from same file
    const filePaths = [...new Set(result.map(r => r.data.file_path))];
    expect(filePaths.length).toBe(1);
  });

  it('should include dependencies for dependencies scope', async () => {
    const result = await mcp.get_implementation('parseAST', 'dependencies');
    const entityNames = result.map(r => r.data.entity_name);
    expect(entityNames).toContain('parseAST');
    expect(entityNames.some(n => n.includes('TreeSitter'))).toBe(true);
  });
});
```

#### 3.2 Integration Tests
1. Test with real parser.py functions that have known helpers
2. Verify deduplication works correctly
3. Test performance with large result sets
4. Validate error handling for non-existent entities

### Phase 4: Documentation & Rollout (Week 4)

#### 4.1 Update Documentation
- Add scope parameter to CLAUDE.md MCP examples
- Document use cases for each scope level
- Provide performance considerations

#### 4.2 Gradual Rollout
1. Deploy with default `minimal` scope (no breaking changes)
2. Test with select projects using logical scope
3. Enable dependencies scope after performance validation
4. Monitor token usage and response times

## Performance Considerations

### Token Limits
- Minimal: ~500-2000 tokens (current)
- Logical: ~2000-5000 tokens (estimated)
- Dependencies: ~5000-15000 tokens (estimated)

### Query Optimization
- Limit logical scope to 20 results
- Limit dependencies scope to 30 results
- Implement result ranking by relevance
- Cache frequently requested scope expansions

### Deduplication Strategy
- Use entity_name as unique identifier
- Preserve highest relevance score for duplicates
- Maintain insertion order for predictable results

## Risk Mitigation

### Potential Issues
1. **Token Overflow**: Mitigate with strict result limits
2. **Performance Degradation**: Add caching layer if needed
3. **Circular Dependencies**: Implement cycle detection
4. **Missing Semantic Data**: Graceful fallback to minimal scope

### Monitoring
- Track average response times per scope
- Monitor token usage distribution
- Log scope usage patterns
- Alert on performance degradation

## Success Metrics

1. **Adoption**: 30% of get_implementation calls use non-minimal scope
2. **Performance**: <100ms latency increase for logical scope
3. **Accuracy**: 95% of returned helpers are actually used by main entity
4. **User Satisfaction**: Reduced follow-up queries for related code

## Future Enhancements

1. **Smart Scope Selection**: Auto-detect optimal scope based on query context
2. **Scope Combinations**: Allow multiple scopes (e.g., "logical+dependencies")
3. **Depth Control**: Add levels parameter for transitive dependencies
4. **Pattern Learning**: Learn common scope patterns per project

## Conclusion

This enhancement leverages existing semantic metadata to provide contextually aware code retrieval without overwhelming Claude with noise. The phased implementation ensures backward compatibility while progressively adding value through semantic scope control.
